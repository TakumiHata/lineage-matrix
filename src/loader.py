"""chain_queries/ 配下のJSON（チェーン済みクエリ）とtable.json（全テーブル）を読み込む処理。"""

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


def load_queries(path: Path) -> dict[str, str]:
    """chain_queries.json またはconverted_queries.json を読み込み、
    {クエリ名: SQL} の辞書を返す。
    """
    data = _load_json_with_encoding(path)

    # chain_queries.json の新フォーマット: [{"クエリ名": "...", "SQL": "...", "呼び出し元": [...]}]
    # converted_queries.json の軽量フォーマット: [{"クエリ名": "...", "SQL": "..."}]
    # どちらも同じように処理できる（"呼び出し元"は無視）
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_queries_for_query_dir(query_dir: Path) -> tuple[dict[str, str], Path] | None:
    """起点クエリのフォルダ（chain_queries/<起点クエリ名>/）から、
    converted_queries.json（AI変換／SSMA変換済みSQL）があればそちらを優先して読み込み、
    なければ chain_queries.json（VBA出力のAccess SQL）を読み込む。
    どちらのファイルも存在しない場合はNoneを返す。
    """
    converted_path = query_dir / "converted_queries.json"
    if converted_path.exists():
        return load_queries(converted_path), converted_path

    chain_path = query_dir / "chain_queries.json"
    if chain_path.exists():
        return load_queries(chain_path), chain_path

    return None


def load_schema(path: Path = TABLES_FILE) -> dict:
    """table.json（全テーブルのフラットな一覧）を読み込み、
    sqlglot.lineage() 用のスキーマ辞書 {テーブル名: {カラム名: 型}} を返す。
    種別・接続先・物理名・スキーマ・スキーマ取得方法は現状のパイプラインでは使用しない
    （物理テーブル名の組み立てには load_table_info() / build_physical_table_name() を使う）。
    """
    data = _load_json_with_encoding(path)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }


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
