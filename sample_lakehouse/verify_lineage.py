"""Verify that the recorded lineage (derived_from) actually holds in the data.

Part 2 (Lineage) verification: for each `source -> target` edge in the config,
we check that the two assets genuinely share columns. A derivation should carry
at least one join/identity column forward from its source; here we assert that
the derived table shares a meaningful set of columns with what it claims to come
from, and we spot-check a specific column that must flow end to end.

Run:  python verify_lineage.py
Exits non-zero if any lineage claim fails.
"""
from pathlib import Path
from typing import List, Set

from scan_data_assets import (
    DEFAULT_CONFIG_FILENAME,
    build_lineage_edges,
    load_catalog_config,
    scan_assets,
)

# Minimum share of the derived table's columns that must also appear upstream.
# Derived tables add/rename a few columns, but should mostly inherit from source.
MIN_SHARED_RATIO = 0.5
# A concrete column that must survive the whole orders lineage chain.
SPOT_CHECK_COLUMN = "order_id"


def columns_of(reports, relative_path: str) -> Set[str]:
    for report in reports:
        if report.relative_path == relative_path:
            return {f.name for f in report.schema}
    raise AssertionError(f"asset not found in scan: {relative_path}")


def main() -> int:
    here = Path(__file__).resolve().parent
    config = load_catalog_config(here / DEFAULT_CONFIG_FILENAME)
    reports = scan_assets(here, config=config)
    edges = build_lineage_edges(reports)

    if not edges:
        print("No lineage edges to verify.")
        return 0

    failures: List[str] = []
    for edge in edges:
        try:
            src_cols = columns_of(reports, edge.source)
            tgt_cols = columns_of(reports, edge.target)
        except AssertionError as exc:
            failures.append(f"[{edge.source} -> {edge.target}] {exc}")
            continue

        shared = src_cols & tgt_cols
        ratio = len(shared) / max(len(tgt_cols), 1)
        status = "OK " if ratio >= MIN_SHARED_RATIO else "FAIL"
        print(
            f"{status} {edge.source} -> {edge.target}  "
            f"shared {len(shared)}/{len(tgt_cols)} cols ({ratio:.0%})"
        )
        if ratio < MIN_SHARED_RATIO:
            failures.append(
                f"[{edge.source} -> {edge.target}] only {ratio:.0%} of derived "
                f"columns trace to the source (need >= {MIN_SHARED_RATIO:.0%})"
            )

    # Spot check: order_id must exist in every asset in the orders lineage chain.
    orders_chain = {
        e.source for e in edges if SPOT_CHECK_COLUMN in columns_of(reports, e.target)
    } | {
        e.target for e in edges if SPOT_CHECK_COLUMN in columns_of(reports, e.target)
    }
    for path in sorted(orders_chain):
        cols = columns_of(reports, path)
        if SPOT_CHECK_COLUMN not in cols:
            failures.append(f"spot check: '{SPOT_CHECK_COLUMN}' missing from {path}")
        else:
            print(f"OK  spot check: '{SPOT_CHECK_COLUMN}' present in {path}")

    if failures:
        print("\nLINEAGE VERIFICATION FAILED:")
        for f in failures:
            print("  - " + f)
        return 1

    print(f"\nAll {len(edges)} lineage edge(s) verified against real schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
