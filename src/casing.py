"""qualify() 通過後に失われる識別子の大文字小文字を、schema/queries上の本来の
表記へ復元するヘルパー。tsqlは大文字小文字を区別しないため、qualify()を経由した
識別子はASCII部分が小文字化されることがある（例: 工事ID → 工事id）。

lineage_extract.py（列単位のリネージ解決）と table_usage.py（テーブル単位の
直接参照抽出）の両方が必要とするため、どちらにも依存しない独立モジュールに
切り出している。
"""


def _restore_casing(name: str | None, candidates) -> str | None:
    if not name:
        return name
    for candidate in candidates:
        if candidate.lower() == name.lower():
            return candidate
    return name


def restore_table_casing(table_name: str | None, schema: dict) -> str | None:
    return _restore_casing(table_name, schema)


def restore_schema_casing(table_name: str | None, column_name: str | None, schema: dict) -> str | None:
    # table_name も qualify() 通過後は小文字化されている可能性があるため、
    # schema のキーとして使う前に本来の表記へ復元しておく必要がある
    # （復元前のtable_nameのままだと schema に存在せず、常にcolumn_nameが
    # 未復元のまま返ってしまう）。
    table_name = restore_table_casing(table_name, schema)
    if not table_name or table_name not in schema:
        return column_name
    return _restore_casing(column_name, schema[table_name])


def restore_query_casing(reference_query: str, queries: dict[str, str]) -> str:
    return _restore_casing(reference_query, queries)
