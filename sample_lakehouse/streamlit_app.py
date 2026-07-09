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
        height=520,
        scrolling=True,
    )


def inject_ui_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background-color: #f8f9fb;
        }
        .catalog-header {
            padding: 1rem 1.5rem 0.75rem;
            border-radius: 1rem;
            background: #ffffff;
            color: #19202a;
            box-shadow: 0 12px 32px rgba(18, 25, 49, 0.08);
            margin-bottom: 1rem;
        }
        .catalog-title {
            margin: 0;
            font-size: 2rem;
            letter-spacing: -0.04em;
        }
        .catalog-subtitle {
            margin: 0.35rem 0 0;
            color: #5c6878;
        }
        .stMetric {
            border: 1px solid rgba(50, 74, 136, 0.12);
            border-radius: 12px;
            padding: 1rem;
        }
        .stButton>button {
            background-color: #2563eb;
            color: white;
        }
        .stTextInput>div>div>input {
            border-radius: 0.75rem;
        }
        .stSelectbox>div>div>div>div {
            border-radius: 0.75rem;
        }
        .asset-card {
            border: 1px solid #d7e1f3;
            border-radius: 1rem;
            padding: 1rem;
            margin-bottom: 0.75rem;
            background: white;
        }
        .asset-card h4 {
            margin-bottom: 0.25rem;
        }
        .badge {
            background: #e8f0ff;
            color: #0b3d91;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .summary-box {
            background: white;
            border: 1px solid #dde6f2;
            border-radius: 1rem;
            padding: 1rem;
        }
        .dataframe-container {
            border-radius: 1rem;
            overflow: hidden;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Unity Catalog Explorer", layout="wide")
    inject_ui_styles()

    with st.sidebar:
        st.markdown("## Catalog controls")
        root_input = st.text_input("Lakehouse root", value=".")
        semantic_config_input = st.text_input("Semantic YAML", value="semantic_layer.yaml")
        lineage_config_input = st.text_input("Lineage config JSON", value="catalog_config.json")
        real_only = st.checkbox("Show only real data assets", value=True)
        show_raw_json = st.checkbox("Show raw config text", value=False)
        st.markdown("---")
        st.markdown("### Filters")
        search = st.text_input("Search assets", value="")
        asset_type_filter = st.selectbox(
            "Asset type",
            options=["all", "delta_table", "file"],
            index=0,
        )

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

    filtered_reports = [
        report
        for report in reports
        if (search.lower() in report.relative_path.lower())
        and (asset_type_filter == "all" or report.asset_type == asset_type_filter)
    ]

    edges = build_lineage_edges(reports)
    line_edge_dicts = [{"source": e.source, "target": e.target} for e in edges]

    st.markdown(
        "<div class='catalog-header'><h1 class='catalog-title'>Catalog Explorer</h1>"
        "<p class='catalog-subtitle'>Browse assets, lineage, and semantic metadata for your lakehouse.</p></div>",
        unsafe_allow_html=True,
    )

    top_col1, top_col2, top_col3, top_col4 = st.columns(4)
    top_col1.metric("Assets", len(filtered_reports))
    top_col2.metric("Delta tables", sum(1 for report in filtered_reports if report.asset_type == "delta_table"))
    top_col3.metric("Files", sum(1 for report in filtered_reports if report.asset_type == "file"))
    top_col4.metric("Lineage links", len(edges))

    if cov:
        coverage_col1, coverage_col2 = st.columns(2)
        coverage_col1.metric("Table coverage", f"{cov['table_coverage_pct']}%")
        coverage_col2.metric("Column coverage", f"{cov['column_coverage_pct']}%")

    tab_catalog, tab_lineage, tab_semantic, tab_config = st.tabs([
        "Catalog",
        "Lineage",
        "Semantic",
        "Config",
    ])

    with tab_catalog:
        st.markdown("### Asset catalog")
        st.markdown("<div class='summary-box'>This view shows scanned assets from the lakehouse root and config-driven lineage metadata.</div>", unsafe_allow_html=True)

        if filtered_reports:
            rows = [asset_row(report) for report in filtered_reports]
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No assets match the current filters.")

        selected_asset = st.selectbox("Select an asset to inspect", options=[report.relative_path for report in filtered_reports] or [""])
        if selected_asset:
            report = next(report for report in reports if report.relative_path == selected_asset)
            st.markdown(f"### {report.relative_path}")
            st.markdown(
                f"<div class='asset-card'><div style='display:flex;gap:1rem;align-items:center;'>"
                f"<span class='badge'>{report.asset_type}</span>"
                f"<div><strong>{len(report.schema)} fields</strong></div></div>"
                f"<p><strong>Derived from:</strong> {', '.join(report.derived_from) if report.derived_from else '(none)'}</p></div>",
                unsafe_allow_html=True,
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
            st.table(schema_table)

    with tab_lineage:
        st.markdown("### Lineage overview")
        if edges:
            st.write({"lineage_edges": len(edges)})
            st.write(line_edge_dicts)
            render_mermaid(line_edge_dicts)
        else:
            st.info("No lineage edges defined in the current config.")

    with tab_semantic:
        st.markdown("### Semantic coverage")
        if cov:
            st.write(cov)
        else:
            st.warning("Semantic metadata unavailable or not loaded.")

        missing = [item["relative_path"] for item in enriched if not item.get("description")]
        if missing:
            st.warning(f"Assets missing descriptions: {len(missing)}")
            st.write(missing)

    with tab_config:
        st.markdown("### Configuration sources")
        st.write(f"Lakehouse root: {root_path}")
        st.write(f"Semantic YAML: {semantic_config_path}")
        st.write(f"Lineage config: {lineage_config_path}")

        if show_raw_json:
            st.markdown("#### Raw lineage config")
            try:
                with lineage_config_path.open("r", encoding="utf-8") as f:
                    st.json(json.load(f))
            except Exception as exc:
                st.error(f"Unable to load lineage config: {exc}")

            st.markdown("#### Raw semantic config")
            try:
                with semantic_config_path.open("r", encoding="utf-8") as f:
                    st.text(f.read())
            except Exception as exc:
                st.error(f"Unable to load semantic config: {exc}")


def is_real_asset_type(path: str) -> bool:
    return not any(marker in path for marker in ("_delta_log", "scan_report", "catalog_semantic"))


if __name__ == "__main__":
    main()
