"""input/ 配下のフラットなJSON（全クエリ・全テーブル）を読み込む処理。"""

import json
from pathlib import Path

from config import QUERY_DEPENDENCIES_FILE, TABLES_FILE


def load_queries(path: Path = QUERY_DEPENDENCIES_FILE) -> dict[str, str]:
    """input/query_dependencies.json（全クエリのフラットな一覧）を読み込み、
    {クエリ名: SQL} の辞書を返す。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_schema(path: Path = TABLES_FILE) -> dict:
    """input/table.json（全テーブルのフラットな一覧）を読み込み、
    sqlglot.lineage() 用のスキーマ辞書 {テーブル名: {カラム名: 型}} を返す。
    種別・接続先・物理名は現状のパイプラインでは使用しない。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }
