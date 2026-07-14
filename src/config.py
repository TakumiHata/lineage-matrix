"""パス関連の定数。"""

from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
TABLES_FILE = INPUT_DIR / "table.json"
QUERIES_FILE = INPUT_DIR / "query.json"
QUERY_GROUPS_FILE = OUTPUT_DIR / "query_groups.xlsx"

# AI変換済みSQLはSQL Server用SQLのみを対象とするツールのため、方言は固定。
DIALECT = "tsql"


def discover_start_queries() -> list[str]:
    """input/ 直下のサブフォルダのうち converted_queries.json が存在するものを
    起点クエリ名として返す。AI変換済みのSQLが配置されているフォルダを
    リネージ解析対象として識別する。
    """
    start_queries = []
    if not INPUT_DIR.exists():
        return start_queries

    for subdir in INPUT_DIR.iterdir():
        if subdir.is_dir() and (subdir / "converted_queries.json").exists():
            start_queries.append(subdir.name)

    return sorted(start_queries)
