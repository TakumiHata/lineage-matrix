"""input/ 配下のJSON（チェーン済みクエリ・全テーブル）を読み込む処理。"""

import json
from pathlib import Path

from config import TABLES_FILE


def load_queries(path: Path) -> dict[str, str]:
    """chain_queries.json またはconverted_queries.json を読み込み、
    {クエリ名: SQL} の辞書を返す。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    
    # chain_queries.json の新フォーマット: [{"クエリ名": "...", "SQL": "...", "呼び出し元": [...]}]
    # converted_queries.json の軽量フォーマット: [{"クエリ名": "...", "SQL": "..."}]
    # どちらも同じように処理できる（"呼び出し元"は無視）
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
