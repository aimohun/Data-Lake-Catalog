"""
Semantic layer for the data catalog  (MSDS 681 - Part 2 goal).

Builds on the Part 1 foundation (scan_data_assets.py). The foundation tells you
the STRUCTURE of each asset (path, type, schema, history). This adds MEANING: a
short, plain-English description for each table and each column, stored in a
separate config file (semantic_layer.yaml) so it can be maintained by hand.

Why this matters "for AI agents": an agent reading the raw schema sees a column
called `cust_id` and a column called `customer_id` and has no way to know they
are the same concept. The semantic layer records that human knowledge in a
machine-readable place, and `describe()` gives an agent a single lookup for it.

Usage:
    # Enriched, browsable HTML catalog (descriptions merged into the scan):
    python semantic_layer.py sample_lakehouse --html catalog_semantic.html

    # Machine-readable enriched catalog (for agents / downstream tools):
    python semantic_layer.py sample_lakehouse --json catalog_semantic.json

    # Coverage report -- how much of the lakehouse is documented:
    python semantic_layer.py sample_lakehouse --coverage

    # Look up one asset (what an AI agent would call):
    python semantic_layer.py sample_lakehouse --describe silver/ops/returns
"""

import argparse
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from scan_data_assets import scan_assets, AssetReport


DEFAULT_CONFIG = "semantic_layer.yaml"

# Foundation quirk: rglob() walks into Delta directories, so the scan also lists
# each table's internal _delta_log/*.json transaction-log files and the scanner's
# own report artifacts as "file" assets. Those are plumbing, not real data
# assets, so we hide them from the semantic catalog.
_NOISE_SUBSTRINGS = ("_delta_log", "scan_report", "catalog_semantic")


def is_real_asset(report: AssetReport) -> bool:
    """A real, user-facing data asset (not a Delta transaction log or a report file)."""
    return not any(marker in report.relative_path for marker in _NOISE_SUBSTRINGS)


def load_semantic_config(path: Path) -> Dict[str, Any]:
    """Load the semantic-layer YAML. Returns {} if the file does not exist yet."""
    if not path.exists():
        return {"assets": {}}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("assets", {})
    return data


def describe(config: Dict[str, Any], asset_path: str,
             column: Optional[str] = None) -> Optional[str]:
    """
    Single lookup an AI agent (or a human) can call: return the plain-English
    description for a table, or for one column within it. Returns None if there
    is no description on record.
    """
    entry = config.get("assets", {}).get(asset_path)
    if entry is None:
        return None
    if column is None:
        desc = entry.get("description")
        return desc.strip() if isinstance(desc, str) else None
    col_desc = (entry.get("columns") or {}).get(column)
    return col_desc.strip() if isinstance(col_desc, str) else None


def enrich_reports(reports: List[AssetReport],
                   config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Merge descriptions from the config onto each real asset and its columns.
    Returns plain dicts (the foundation's dataclasses + description fields) so
    the result is directly JSON-serializable for downstream tools/agents.
    """
    enriched: List[Dict[str, Any]] = []
    for report in reports:
        if not is_real_asset(report):
            continue
        item = asdict(report)
        item["description"] = describe(config, report.relative_path)
        for field in item["schema"]:
            field["description"] = describe(config, report.relative_path, field["name"])
        enriched.append(item)
    return enriched


def coverage(enriched: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How much of the lakehouse is documented -- the verification metric."""
    tables_total = len(enriched)
    tables_documented = sum(1 for a in enriched if a["description"])
    cols_total = sum(len(a["schema"]) for a in enriched)
    cols_documented = sum(
        1 for a in enriched for f in a["schema"] if f.get("description")
    )
    undocumented_tables = [a["relative_path"] for a in enriched if not a["description"]]
    return {
        "tables_total": tables_total,
        "tables_documented": tables_documented,
        "columns_total": cols_total,
        "columns_documented": cols_documented,
        "table_coverage_pct": round(100 * tables_documented / tables_total, 1) if tables_total else 0.0,
        "column_coverage_pct": round(100 * cols_documented / cols_total, 1) if cols_total else 0.0,
        "undocumented_tables": undocumented_tables,
    }


def render_html(enriched: List[Dict[str, Any]], cov: Dict[str, Any]) -> str:
    """Browsable HTML catalog with descriptions shown inline. Descriptions
    missing from the config are flagged so gaps are visible, not hidden."""
    def esc(x: Any) -> str:
        return html.escape(str(x)) if x is not None else ""

    cards = []
    for asset in enriched:
        desc = asset["description"]
        desc_html = (
            f"<p class='desc'>{esc(desc)}</p>" if desc
            else "<p class='missing'>(no description yet -- add one in semantic_layer.yaml)</p>"
        )
        col_rows = []
        for field in asset["schema"]:
            fdesc = field.get("description")
            fdesc_html = esc(fdesc) if fdesc else "<span class='missing'>&mdash;</span>"
            col_rows.append(
                f"<tr><td><code>{esc(field['name'])}</code></td>"
                f"<td class='dtype'>{esc(field['dtype'])}</td>"
                f"<td>{fdesc_html}</td></tr>"
            )
        badge = "delta" if asset["asset_type"] == "delta_table" else "file"
        cards.append(f"""
      <section class="card">
        <h2>{esc(asset['relative_path'])} <span class="badge {badge}">{badge}</span></h2>
        {desc_html}
        <table class="cols">
          <thead><tr><th>Column</th><th>Type</th><th>Description</th></tr></thead>
          <tbody>{''.join(col_rows)}</tbody>
        </table>
      </section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Data Catalog &mdash; Semantic Layer</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
            margin: 0; padding: 24px; line-height: 1.45; }}
    header {{ margin-bottom: 20px; }}
    h1 {{ margin: 0 0 4px; }}
    .cov {{ color: #666; font-size: 0.9rem; }}
    .card {{ border: 1px solid #ccc; border-radius: 10px; padding: 16px 18px;
             margin-bottom: 18px; }}
    .card h2 {{ font-size: 1.05rem; margin: 0 0 6px; font-family: monospace; }}
    .badge {{ font-family: sans-serif; font-size: .7rem; padding: 2px 8px;
              border-radius: 999px; vertical-align: middle; }}
    .badge.delta {{ background: #dcedff; color: #084298; }}
    .badge.file {{ background: #e9e9e9; color: #444; }}
    .desc {{ margin: 4px 0 12px; max-width: 70ch; }}
    .missing {{ color: #b02a37; font-style: italic; }}
    table.cols {{ border-collapse: collapse; width: 100%; }}
    table.cols th, table.cols td {{ border-bottom: 1px solid #e2e2e2;
      padding: 6px 10px; text-align: left; vertical-align: top; font-size: .92rem; }}
    table.cols th {{ font-size: .78rem; text-transform: uppercase; color: #888; }}
    .dtype {{ color: #666; font-family: monospace; font-size: .82rem; }}
    code {{ font-family: monospace; }}
  </style>
</head>
<body>
  <header>
    <h1>Data Catalog &mdash; Semantic Layer</h1>
    <p class="cov">
      {cov['tables_documented']}/{cov['tables_total']} tables documented
      ({cov['table_coverage_pct']}%) &middot;
      {cov['columns_documented']}/{cov['columns_total']} columns documented
      ({cov['column_coverage_pct']}%)
    </p>
  </header>
  {''.join(cards)}
</body>
</html>
"""


def build_catalog(root: Path, config_path: Path):
    """Scan + enrich in one call. Returns (enriched, coverage_dict)."""
    reports = scan_assets(root)
    config = load_semantic_config(config_path)
    enriched = enrich_reports(reports, config)
    return enriched, coverage(enriched)


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic layer over the data catalog.")
    parser.add_argument("root", help="Path to the shared data root")
    parser.add_argument("--config", default=None,
                        help=f"Semantic config YAML (default: <root>/{DEFAULT_CONFIG})")
    parser.add_argument("--html", help="Write browsable HTML catalog to this path")
    parser.add_argument("--json", dest="json_out", help="Write enriched JSON catalog to this path")
    parser.add_argument("--coverage", action="store_true", help="Print documentation coverage")
    parser.add_argument("--describe", metavar="ASSET_PATH",
                        help="Print the description for one asset (and its columns)")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root path does not exist: {root}")
    config_path = Path(args.config) if args.config else root / DEFAULT_CONFIG

    enriched, cov = build_catalog(root, config_path)

    if args.describe:
        config = load_semantic_config(config_path)
        table_desc = describe(config, args.describe)
        print(f"{args.describe}\n  {table_desc or '(no table description on record)'}")
        match = next((a for a in enriched if a["relative_path"] == args.describe), None)
        if match:
            for field in match["schema"]:
                print(f"    - {field['name']}: {field.get('description') or '(no description)'}")
        else:
            print("  (asset not found in scan -- check the path)")
        return

    if args.html:
        Path(args.html).write_text(render_html(enriched, cov), encoding="utf-8")
        print(f"HTML catalog written to {args.html}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(enriched, indent=2), encoding="utf-8")
        print(f"Enriched JSON catalog written to {args.json_out}")
    if args.coverage or not (args.html or args.json_out):
        print(json.dumps(cov, indent=2))


if __name__ == "__main__":
    main()
