"""パス関連の定数。"""

from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
TABLES_FILE = INPUT_DIR / "table.json"

# AI変換済みSQLはSQL Server用SQLのみを対象とするツールのため、方言は固定。
DIALECT = "tsql"


def discover_start_queries() -> list[str]:
    """input/ 直下のサブフォルダのうち、chain_queries.json（VBA出力）または
    converted_queries.json（AI変換済み）が存在するものを起点クエリ名として返す。
    chain_queries.json のみ（AI変換がまだのクエリ）のフォルダも解析対象に含める。
    input/table.json はファイルなのでここでは対象外（is_dir()で除外される）。
    """
    start_queries = []
    if not INPUT_DIR.exists():
        return start_queries

    for subdir in INPUT_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if (subdir / "chain_queries.json").exists() or (subdir / "converted_queries.json").exists():
            start_queries.append(subdir.name)

    return sorted(start_queries)
