"""パス関連の定数。"""

import json
from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
QUERY_DEPENDENCIES_FILE = INPUT_DIR / "query_dependencies.json"
TABLES_FILE = INPUT_DIR / "table.json"
START_QUERIES_FILE = INPUT_DIR / "start_queries.json"


def discover_start_queries(path: Path = START_QUERIES_FILE) -> list[str]:
    """input/start_queries.json（起点クエリ名の配列）を読み込んで返す。

    起点クエリごとにAI変換済みSQL（converted_queries.json）を使いたい場合は、
    input/<起点クエリ名>/ フォルダを別途作成して配置する（任意）。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)
