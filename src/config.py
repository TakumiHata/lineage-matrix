"""パス関連の定数。"""

from pathlib import Path

CHAIN_QUERIES_DIR = Path("chain_queries")
OUTPUT_DIR = Path("output")
TABLES_FILE = Path("table.json")

# AI変換済みSQLはSQL Server用SQLのみを対象とするツールのため、方言は固定。
DIALECT = "tsql"


def discover_start_queries() -> list[str]:
    """chain_queries/ 直下のサブフォルダのうち、chain_queries.json（VBA出力）または
    converted_queries.json（AI変換済み）が存在するものを起点クエリ名として返す。
    chain_queries.json のみ（AI変換がまだのクエリ）のフォルダも解析対象に含める。
    """
    start_queries = []
    if not CHAIN_QUERIES_DIR.exists():
        return start_queries

    for subdir in CHAIN_QUERIES_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if (subdir / "chain_queries.json").exists() or (subdir / "converted_queries.json").exists():
            start_queries.append(subdir.name)

    return sorted(start_queries)
