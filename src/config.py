"""パス関連の定数。"""

from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
TABLES_FILE = INPUT_DIR / "table.json"

# Jet-SQL（Access SQL）自体の方言はsqlglotにないため、構文的に近いtsqlを使う
# （関数の意味解釈は不要で、構文的にパースできればテーブル参照抽出には十分）。
DIALECT = "tsql"


def discover_start_queries() -> list[str]:
    """input/ 直下のサブフォルダのうち、chain_queries.json（VBA出力のAccess SQL）
    が存在するものを起点クエリ名として返す。
    input/table.json はファイルなのでここでは対象外（is_dir()で除外される）。
    """
    start_queries = []
    if not INPUT_DIR.exists():
        return start_queries

    for subdir in INPUT_DIR.iterdir():
        if not subdir.is_dir():
            continue
        if (subdir / "chain_queries.json").exists():
            start_queries.append(subdir.name)

    return sorted(start_queries)
