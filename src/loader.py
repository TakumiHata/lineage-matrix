"""input/<起点クエリ>/ 配下のJSONを読み込む処理。"""

import json
from pathlib import Path

from config import INPUT_DIR


def discover_query_groups(input_dir: Path = INPUT_DIR) -> list[Path]:
    """input/<起点クエリ名>/ 形式のグループフォルダを列挙する。"""
    return sorted(p for p in input_dir.iterdir() if p.is_dir())


def load_queries(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_schema(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }
