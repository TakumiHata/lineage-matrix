"""パス関連の定数。"""

from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
QUERY_DEPENDENCIES_FILE = INPUT_DIR / "query_dependencies.json"
TABLES_FILE = INPUT_DIR / "table.json"


def discover_start_queries(input_dir: Path = INPUT_DIR) -> list[str]:
    """input/ 直下のサブフォルダ名を起点クエリ名の一覧として返す。

    フォルダの中身は空でよく、フォルダ名自体が「このクエリを起点として
    チェーン検出（sql_expand/main.detect_chain）を行う」という指定に使われる。
    """
    return sorted(p.name for p in input_dir.iterdir() if p.is_dir())
