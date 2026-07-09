"""
Verification for the semantic layer (Part 2 requirement: "verify it against the
real data -- pick a specific table or column and check the behavior holds").

Run from inside the sample_lakehouse/ folder:
    python test_semantic_layer.py

Exits non-zero if any check fails.
"""

from pathlib import Path

from semantic_layer import build_catalog, load_semantic_config, describe, is_real_asset
from scan_data_assets import scan_assets

ROOT = Path(__file__).parent
CONFIG = ROOT / "semantic_layer.yaml"


def check(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise AssertionError(name)


def main():
    enriched, cov = build_catalog(ROOT, CONFIG)
    config = load_semantic_config(CONFIG)
    by_path = {a["relative_path"] for a in enriched}

    print("Verifying semantic layer against the real lakehouse data:")

    # 1. The noise filter works: no _delta_log / report files leak into the catalog.
    check("no _delta_log/report noise in catalog",
          all(is_real_asset_path(p) for p in by_path))

    # 2. Every real table the scanner finds is present in the enriched catalog.
    real_from_scan = {r.relative_path for r in scan_assets(ROOT) if is_real_asset(r)}
    check("all real scanned assets are in the catalog", real_from_scan == by_path)

    # 3. Specific column check -- the documented naming quirk actually exists in
    #    the data AND has a description explaining it. returns.cust_id is the
    #    same concept as customers.customer_id.
    returns = next(a for a in enriched if a["relative_path"] == "silver/ops/returns")
    cust_id = next((f for f in returns["schema"] if f["name"] == "cust_id"), None)
    check("returns.cust_id column exists in real data", cust_id is not None)
    check("returns.cust_id has a description", bool(cust_id and cust_id.get("description")))
    check("returns.cust_id description links it to customer_id",
          "customer_id" in (cust_id.get("description") or ""))

    # 4. The describe() lookup (the AI-agent API) returns the same text.
    check("describe() matches enriched column description",
          describe(config, "silver/ops/returns", "cust_id") == cust_id.get("description"))

    # 5. Table-level description round-trips.
    check("describe() returns orders table description",
          bool(describe(config, "silver/sales/orders")))

    # 6. Config only describes columns that really exist (no typos / stale keys).
    scanned_cols = {
        (a["relative_path"], f["name"]) for a in enriched for f in a["schema"]
    }
    stale = []
    for path, entry in config["assets"].items():
        for col in (entry.get("columns") or {}):
            if (path, col) not in scanned_cols and path in by_path:
                stale.append(f"{path}.{col}")
    check(f"no stale/typo column keys in config (found: {stale})", not stale)

    # 7. Coverage is fully documented (this seeded config aims for 100%).
    check(f"100% table coverage (got {cov['table_coverage_pct']}%)",
          cov["table_coverage_pct"] == 100.0)
    check(f"100% column coverage (got {cov['column_coverage_pct']}%)",
          cov["column_coverage_pct"] == 100.0)

    print(f"\nAll checks passed. "
          f"{cov['tables_documented']}/{cov['tables_total']} tables, "
          f"{cov['columns_documented']}/{cov['columns_total']} columns documented.")


def is_real_asset_path(path: str) -> bool:
    return not any(m in path for m in ("_delta_log", "scan_report", "catalog_semantic"))


if __name__ == "__main__":
    main()
