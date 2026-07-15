"""input/ 配下のJSON（チェーン済みクエリ）とtable.json（全テーブル）を読み込む処理。"""

import json
from pathlib import Path

from config import INPUT_DIR, TABLES_FILE


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


def load_queries(path: Path) -> dict[str, str]:
    """chain_queries.json を読み込み、{クエリ名: SQL} の辞書を返す
    （新フォーマット: [{"クエリ名": "...", "SQL": "...", "呼び出し元": [...]}]。
    "呼び出し元"は使わない）。
    """
    data = _load_json_with_encoding(path)
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_group_queries(start_query: str) -> dict[str, str] | None:
    """input/<start_query>/chain_queries.json を読み込む。存在しなければ警告を
    出してNoneを返す（main.py・table_reference_extract.pyの両方が起点クエリ単位で
    共有するロード処理）。
    """
    chain_path = INPUT_DIR / start_query / "chain_queries.json"
    if not chain_path.exists():
        print(f"[{start_query}] 警告: chain_queries.json が見つかりません。スキップします。")
        return None
    return load_queries(chain_path)


def load_table_info(path: Path = TABLES_FILE) -> dict[str, dict]:
    """table.json（全テーブルのフラットな一覧）を読み込み、
    {テーブル名: {"物理名": ..., "スキーマ": ..., "スキーマ取得方法": ...}} を返す。
    build_physical_table_name() と組み合わせて物理テーブル名を組み立てる際に使う。
    """
    data = _load_json_with_encoding(path)
    return {
        item["テーブル名"]: {
            "物理名": item.get("物理名", item["テーブル名"]),
            "スキーマ": item.get("スキーマ", ""),
            "スキーマ取得方法": item.get("スキーマ取得方法", ""),
        }
        for item in data
    }


def build_physical_table_name(table_info: dict) -> str:
    """load_table_info() の1テーブル分の情報から、マトリックス表用の物理テーブル名を組み立てる。
    スキーマが存在する場合は「スキーマ.物理名」、存在しない場合は「物理名」のみを返す。
    """
    schema_name = table_info["スキーマ"]
    physical_name = table_info["物理名"]
    return f"{schema_name}.{physical_name}" if schema_name else physical_name
