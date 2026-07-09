import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from deltalake import DeltaTable


SUPPORTED_FILE_EXTENSIONS = {".csv", ".json", ".parquet"}
IGNORE_DIR_NAMES = {".venv", "__pycache__", ".git"}


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


def scan_assets(root_path: Path) -> List[AssetReport]:
    reports: List[AssetReport] = []
    root_path = root_path.resolve()

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
                )
            )

    return reports


def report_to_json(reports: List[AssetReport]) -> str:
    return json.dumps([asdict(report) for report in reports], indent=2)


def report_to_html(reports: List[AssetReport]) -> str:
    rows = []
    for report in reports:
        schema_html = "<br>".join(f"<code>{field.name}: {field.dtype}</code>" for field in report.schema)
        history_html = "<br>".join(
            f"v{entry.version} | {entry.operation or 'unknown'} | {entry.timestamp or 'unknown'}"
            for entry in (report.history or [])
        ) or "-"
        rows.append(
            f"<tr><td>{report.relative_path}</td><td>{report.asset_type}</td>"
            f"<td>{schema_html}</td><td>{history_html}</td></tr>"
        )

    return f"""
<html>
<head>
  <meta charset='utf-8'>
  <title>Data Asset Scan</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f2f2f2; }}
    code {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>Data Asset Scan</h1>
  <p>{len(reports)} assets found.</p>
  <table>
    <thead><tr><th>Asset Path</th><th>Type</th><th>Schema</th><th>History</th></tr></thead>
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
    args = parser.parse_args()

    root_path = Path(args.root)
    if not root_path.exists():
        raise SystemExit(f"Root path does not exist: {root_path}")

    reports = scan_assets(root_path)
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
