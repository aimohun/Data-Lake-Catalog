import argparse
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from deltalake import DeltaTable


SUPPORTED_FILE_EXTENSIONS = {".csv", ".json", ".parquet"}
IGNORE_DIR_NAMES = {".venv", "__pycache__", ".git", ".mypy_cache"}
DEFAULT_CONFIG_FILENAME = "catalog_config.json"


@dataclass
class FileSchemaField:
    name: str
    dtype: str


@dataclass
class DeltaHistoryEntry:
    version: int
    operation: Optional[str]
    timestamp: Optional[str]
    user_id: Optional[str]
    user_name: Optional[str]
    operation_parameters: Optional[Dict[str, Any]]


@dataclass
class AssetReport:
    relative_path: str
    asset_type: str
    schema: List[FileSchemaField]
    history: Optional[List[DeltaHistoryEntry]] = None
    # Lineage: assets this one was produced from (upstream), keyed in the config
    # file by relative_path. Empty list means "no recorded upstream".
    derived_from: List[str] = field(default_factory=list)


@dataclass
class LineageEdge:
    source: str  # upstream asset (relative_path)
    target: str  # downstream asset produced from the source


def is_delta_table(path: Path) -> bool:
    return path.is_dir() and (path / "_delta_log").is_dir()


def infer_file_schema(path: Path) -> List[FileSchemaField]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, nrows=1000)
    elif suffix == ".json":
        df = pd.read_json(path, lines=True if _is_json_lines(path) else False)
    elif suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        raise ValueError(f"Unsupported file type: {path}")

    schema = []
    for name, dtype in df.dtypes.items():
        schema.append(FileSchemaField(name=name, dtype=str(dtype)))
    return schema


def _is_json_lines(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        return first_line.startswith("{") and "}\n" not in first_line and first_line.endswith("}")
    except Exception:
        return False


def read_delta_schema(path: Path) -> List[FileSchemaField]:
    table = DeltaTable(str(path))
    schema = []
    for field in table.schema().fields:
        schema.append(FileSchemaField(name=field.name, dtype=str(field.type)))
    return schema


def read_delta_history(path: Path) -> List[DeltaHistoryEntry]:
    table = DeltaTable(str(path))
    history = []
    for entry in table.history():
        history.append(
            DeltaHistoryEntry(
                version=int(entry.get("version", -1)),
                operation=entry.get("operation"),
                timestamp=entry.get("timestamp"),
                user_id=entry.get("userId"),
                user_name=entry.get("userName"),
                operation_parameters=entry.get("operationParameters"),
            )
        )
    return history


def load_catalog_config(config_path: Optional[Path]) -> Dict[str, Any]:
    """Load the table config (keyed by relative_path). Missing file -> empty."""
    if config_path is None or not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_lineage_edges(reports: List[AssetReport]) -> List[LineageEdge]:
    """Flatten each asset's derived_from into source -> target edges."""
    edges: List[LineageEdge] = []
    for report in reports:
        for source in report.derived_from:
            edges.append(LineageEdge(source=source, target=report.relative_path))
    return edges


def scan_assets(root_path: Path, config: Optional[Dict[str, Any]] = None) -> List[AssetReport]:
    reports: List[AssetReport] = []
    root_path = root_path.resolve()
    config = config or {}

    for path in sorted(root_path.rglob("*")):
        if path == root_path:
            continue

        if any(part in IGNORE_DIR_NAMES for part in path.parts):
            continue

        if path.is_dir():
            if is_delta_table(path):
                relative_path = str(path.relative_to(root_path))
                try:
                    schema = read_delta_schema(path)
                    history = read_delta_history(path)
                except Exception as exc:
                    schema = [FileSchemaField(name="_error", dtype=str(exc))]
                    history = []
                reports.append(
                    AssetReport(
                        relative_path=relative_path,
                        asset_type="delta_table",
                        schema=schema,
                        history=history,
                        derived_from=list(config.get(relative_path, {}).get("derived_from", [])),
                    )
                )
                # Do not recurse into Delta table directories as separate assets
                continue

        elif path.is_file() and path.suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
            parent = path.parent
            if is_delta_table(parent):
                continue

            relative_path = str(path.relative_to(root_path))
            try:
                schema = infer_file_schema(path)
            except Exception as exc:
                schema = [FileSchemaField(name="_error", dtype=str(exc))]
            reports.append(
                AssetReport(
                    relative_path=relative_path,
                    asset_type="file",
                    schema=schema,
                    history=None,
                    derived_from=list(config.get(relative_path, {}).get("derived_from", [])),
                )
            )

    return reports


def report_to_json(reports: List[AssetReport]) -> str:
    return json.dumps([asdict(report) for report in reports], indent=2)


def _render_lineage_view(reports: List[AssetReport]) -> str:
    """Render the lineage 'view': an edge table plus a Mermaid flowchart.

    Only assets that participate in at least one derived_from link are shown,
    so the graph stays focused on real lineage rather than every scanned file.
    """
    edges = build_lineage_edges(reports)
    if not edges:
        return "<h2>Lineage</h2><p>No <code>derived_from</code> links recorded in the config.</p>"

    # Map each target to the assets it was produced from.
    edge_rows = "".join(
        f"<tr><td><code>{edge.source}</code></td><td>&rarr;</td>"
        f"<td><code>{edge.target}</code></td></tr>"
        for edge in edges
    )

    # Mermaid flowchart. Node ids must be alnum, so slugify the path.
    def node_id(path: str) -> str:
        return "n_" + "".join(c if c.isalnum() else "_" for c in path)

    nodes = {}
    for edge in edges:
        for path in (edge.source, edge.target):
            nodes[node_id(path)] = path
    node_lines = "\n".join(f'    {nid}["{path}"]' for nid, path in nodes.items())
    edge_lines = "\n".join(
        f"    {node_id(e.source)} --> {node_id(e.target)}" for e in edges
    )
    mermaid = f"flowchart LR\n{node_lines}\n{edge_lines}"

    return f"""
  <h2>Lineage</h2>
  <p>{len(edges)} recorded <code>derived_from</code> link(s). An arrow means the
     right-hand asset was produced from the left-hand one.</p>
  <table>
    <thead><tr><th>Upstream (source)</th><th></th><th>Downstream (produced)</th></tr></thead>
    <tbody>{edge_rows}</tbody>
  </table>
  <h3>Lineage graph</h3>
  <div class="mermaid">
{mermaid}
  </div>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true }});
  </script>
"""


def report_to_html(reports: List[AssetReport]) -> str:
    rows = []
    for report in reports:
        schema_html = "<br>".join(f"<code>{field.name}: {field.dtype}</code>" for field in report.schema)
        history_html = "<br>".join(
            f"v{entry.version} | {entry.operation or 'unknown'} | {entry.timestamp or 'unknown'}"
            for entry in (report.history or [])
        ) or "-"
        derived_html = "<br>".join(f"<code>{src}</code>" for src in report.derived_from) or "-"
        rows.append(
            f"<tr><td>{report.relative_path}</td><td>{report.asset_type}</td>"
            f"<td>{schema_html}</td><td>{history_html}</td><td>{derived_html}</td></tr>"
        )

    lineage_view = _render_lineage_view(reports)

    return f"""
<html>
<head>
  <meta charset='utf-8'>
  <title>Data Asset Scan</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f2f2f2; }}
    code {{ white-space: pre-wrap; }}
    .mermaid {{ border: 1px solid #eee; padding: 12px; background: #fafafa; }}
  </style>
</head>
<body>
  <h1>Data Asset Scan</h1>
  <p>{len(reports)} assets found.</p>
{lineage_view}
  <h2>Assets</h2>
  <table>
    <thead><tr><th>Asset Path</th><th>Type</th><th>Schema</th><th>History</th><th>Derived From</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a data root for Delta tables and plain file assets.")
    parser.add_argument("root", help="Path to the shared data root")
    parser.add_argument("--output", help="Optional JSON output file path", default=None)
    parser.add_argument("--html", help="Optional HTML output file path", default=None)
    parser.add_argument(
        "--config",
        help="Path to the table config file (lineage / derived_from). "
        f"Defaults to '{DEFAULT_CONFIG_FILENAME}' next to this script if present.",
        default=None,
    )
    args = parser.parse_args()

    root_path = Path(args.root)
    if not root_path.exists():
        raise SystemExit(f"Root path does not exist: {root_path}")

    if args.config:
        config_path = Path(args.config)
    else:
        default_config = Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME
        config_path = default_config if default_config.exists() else None
    config = load_catalog_config(config_path)

    reports = scan_assets(root_path, config=config)
    json_output = report_to_json(reports)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_output)
        print(f"Scan complete: wrote {len(reports)} assets to {args.output}")
    else:
        print(json_output)

    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(report_to_html(reports))
        print(f"HTML report written to {args.html}")


if __name__ == "__main__":
    main()
