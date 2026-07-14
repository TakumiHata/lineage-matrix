"""input/ 配下のJSON（チェーン済みクエリ・全テーブル）を読み込む処理。"""

import json
from pathlib import Path

from config import TABLES_FILE


def _load_json_with_encoding(path: Path) -> dict:
    """JSONファイルをUTF-8（BOM有無いずれも可）として読み込む。
    VBA側の出力仕様は常にUTF-8のため、他エンコーディングへの総当たりは行わない。
    総当たりにすると、誤ったエンコーディングでも偶然デコードが成功し、
    文字化けに気づかないまま処理が進んでしまう恐れがあるため。
    """
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{path} をUTF-8として読み込めませんでした。入力ファイルはUTF-8で保存してください。") from e


def load_raw_queries(path: Path) -> list[dict]:
    """query.json（クエリ名・SQLのみの全クエリ一覧、呼び出し元関係なし）を読み込み、
    [{"クエリ名": ..., "SQL": ...}, ...] の生のリストをそのまま返す。
    """
    return _load_json_with_encoding(path)


def load_queries(path: Path) -> dict[str, str]:
    """chain_queries.json またはconverted_queries.json を読み込み、
    {クエリ名: SQL} の辞書を返す。
    """
    data = _load_json_with_encoding(path)
    
    # chain_queries.json の新フォーマット: [{"クエリ名": "...", "SQL": "...", "呼び出し元": [...]}]
    # converted_queries.json の軽量フォーマット: [{"クエリ名": "...", "SQL": "..."}]
    # どちらも同じように処理できる（"呼び出し元"は無視）
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_schema(path: Path = TABLES_FILE) -> dict:
    """input/table.json（全テーブルのフラットな一覧）を読み込み、
    sqlglot.lineage() 用のスキーマ辞書 {テーブル名: {カラム名: 型}} を返す。
    種別・接続先・物理名は現状のパイプラインでは使用しない。
    """
    data = _load_json_with_encoding(path)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }
