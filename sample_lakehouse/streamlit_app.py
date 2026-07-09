import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from streamlit.components.v1 import html as st_html

from scan_data_assets import (
    AssetReport,
    build_lineage_edges,
    load_catalog_config,
    scan_assets,
)
from semantic_layer import (
    coverage,
    describe,
    enrich_reports,
    is_real_asset,
    load_semantic_config,
)


@st.cache_data
def load_scan_data(root: Path, lineage_config: Path) -> List[AssetReport]:
    config = load_catalog_config(lineage_config)
    return scan_assets(root, config=config)


@st.cache_data
def load_semantic_data(root: Path, semantic_config: Path, reports: List[AssetReport]) -> Dict[str, Any]:
    config = load_semantic_config(semantic_config)
    enriched = enrich_reports(reports, config)
    return {
        "config": config,
        "enriched": enriched,
        "coverage": coverage(enriched),
    }


def asset_row(report: AssetReport) -> Dict[str, Any]:
    return {
        "path": report.relative_path,
        "type": report.asset_type,
        "schema_count": len(report.schema),
        "derived_from": report.derived_from,
    }


def render_mermaid(edges: List[Dict[str, str]]) -> None:
    if not edges:
        st.info("No recorded lineage edges found in the current config.")
        return

    def node_id(path: str) -> str:
        return "n_" + "".join(c if c.isalnum() else "_" for c in path)

    nodes = {node_id(e["source"]): e["source"] for e in edges}
    for edge in edges:
        nodes[node_id(edge["target"])] = edge["target"]

    node_lines = "\n".join(f'{nid}["{path}"]' for nid, path in nodes.items())
    edge_lines = "\n".join(
        f"{node_id(e['source'])} --> {node_id(e['target'])}" for e in edges
    )
    mermaid = f"flowchart LR\n{node_lines}\n{edge_lines}"

    st_html(
        f"""
        <div class='mermaid'>{mermaid}</div>
        <script type='module'>
          import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
          mermaid.initialize({{ startOnLoad: true, theme: 'base' }});
        </script>
        """,
        height=480,
        scrolling=True,
    )


def main() -> None:
    st.set_page_config(page_title="Data Lake Catalog", layout="wide")
    st.title("Data Lake Catalog Explorer")
    st.markdown(
        "Use this app to browse scanned assets, semantic metadata, and lineage for your lakehouse."
    )

    with st.sidebar:
        st.header("Configuration")
        root_input = st.text_input("Lakehouse root", value=".")
        semantic_config_input = st.text_input("Semantic YAML", value="semantic_layer.yaml")
        lineage_config_input = st.text_input("Lineage config JSON", value="catalog_config.json")
        real_only = st.checkbox("Show only real business assets", value=True)
        show_raw_json = st.checkbox("Show raw scan JSON", value=False)

    root_path = Path(root_input).expanduser().resolve()
    semantic_config_path = Path(semantic_config_input)
    lineage_config_path = Path(lineage_config_input)

    if not semantic_config_path.is_absolute():
        semantic_config_path = root_path / semantic_config_path
    if not lineage_config_path.is_absolute():
        lineage_config_path = root_path / lineage_config_path

    if not root_path.exists():
        st.error(f"Root path does not exist: {root_path}")
        return

    try:
        reports = load_scan_data(root_path, lineage_config_path)
    except Exception as exc:
        st.error(f"Error scanning assets: {exc}")
        return

    try:
        semantic_data = load_semantic_data(root_path, semantic_config_path, reports)
        enriched = semantic_data["enriched"]
        cov = semantic_data["coverage"]
    except Exception as exc:
        st.warning(f"Could not load semantic layer: {exc}")
        enriched = []
        cov = {}

    if real_only:
        reports = [report for report in reports if is_real_asset(report)]
        enriched = [item for item in enriched if is_real_asset_type(item["relative_path"])]

    asset_counts = {
        "total": len(reports),
        "delta_tables": sum(1 for report in reports if report.asset_type == "delta_table"),
        "files": sum(1 for report in reports if report.asset_type == "file"),
    }

    edges = build_lineage_edges(reports)
    line_edge_dicts = [{"source": e.source, "target": e.target} for e in edges]

    st.subheader("Lakehouse summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Assets scanned", asset_counts["total"])
    col2.metric("Delta tables", asset_counts["delta_tables"])
    col3.metric("Files", asset_counts["files"])
    col4.metric("Lineage links", len(edges))

    if cov:
        cov_col1, cov_col2 = st.columns(2)
        cov_col1.metric("Table coverage", f"{cov['table_coverage_pct']}%")
        cov_col2.metric("Column coverage", f"{cov['column_coverage_pct']}%")

    search = st.text_input("Search assets", value="")
    asset_type_filter = st.selectbox(
        "Asset type",
        options=["all", "delta_table", "file"],
        index=0,
    )

    filtered_reports = [
        report
        for report in reports
        if (search.lower() in report.relative_path.lower())
        and (asset_type_filter == "all" or report.asset_type == asset_type_filter)
    ]

    st.markdown(f"**Showing {len(filtered_reports)} assets**")
    report_rows = [asset_row(report) for report in filtered_reports]
    if report_rows:
        st.write(report_rows)
    else:
        st.info("No assets match the current search or filter.")

    asset_names = [report.relative_path for report in filtered_reports]
    selected_asset = st.selectbox("Select asset to inspect", options=asset_names)
    if selected_asset:
        report = next(report for report in reports if report.relative_path == selected_asset)
        st.markdown(f"### {report.relative_path}")
        st.write(
            {
                "type": report.asset_type,
                "schema_fields": len(report.schema),
                "derived_from": report.derived_from or "(none)",
            }
        )

        st.markdown("#### Schema")
        schema_table = [
            {
                "column": field.name,
                "dtype": field.dtype,
                "description": describe(semantic_data["config"], report.relative_path, field.name)
                if semantic_data.get("config")
                else None,
            }
            for field in report.schema
        ]
        st.write(schema_table)

        upstream = report.derived_from
        downstream = [e.target for e in edges if e.source == report.relative_path]
        st.markdown("#### Lineage")
        st.write({"upstream": upstream or "(none)", "downstream": downstream or "(none)"})

    st.markdown("## Lineage graph")
    render_mermaid(line_edge_dicts)

    if show_raw_json:
        st.markdown("## Raw data")
        st.write("### Lineage config")
        try:
            with lineage_config_path.open("r", encoding="utf-8") as f:
                st.json(json.load(f))
        except Exception as exc:
            st.error(f"Unable to load lineage config: {exc}")

        st.write("### Semantic config")
        try:
            with semantic_config_path.open("r", encoding="utf-8") as f:
                st.text(f.read())
        except Exception as exc:
            st.error(f"Unable to load semantic config: {exc}")


def is_real_asset_type(path: str) -> bool:
    return not any(marker in path for marker in ("_delta_log", "scan_report", "catalog_semantic"))


if __name__ == "__main__":
    main()
