# Sample Lakehouse Catalog

This sample lakehouse contains a small data catalog and semantic layer built around scanned data assets.

## Streamlit Catalog UI

Start the UI from the `sample_lakehouse` directory:

```bash
streamlit run streamlit_app.py
```

Then open the local Streamlit URL shown in your terminal.

## What it shows

- scanned lakehouse assets from the current directory
- lineage based on `catalog_config.json`
- semantic metadata from `semantic_layer.yaml`
- asset schema and descriptions
- Mermaid lineage graph

## Important files

- `scan_data_assets.py` — scans files and Delta tables
- `semantic_layer.py` — enriches scanned assets with descriptions
- `catalog_config.json` — recorded lineage sources
- `semantic_layer.yaml` — semantic metadata for tables and columns
- `streamlit_app.py` — Streamlit UI entrypoint
