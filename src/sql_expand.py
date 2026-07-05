"""クエリ間参照（FROM句に他のAccessクエリ名がそのまま残る）を
sqlglot標準の exp.expand() でインライン展開する。

exp.expand() は展開したサブクエリに `/* source: <元の名前> */` という
コメントを自動付与し、sqlglot.lineage() 側はこれを検出して
Node.source_name に反映する。これにより、末端の物理テーブルまで辿る過程で
「どの登録済みクエリの内部か」を独自コードなしで判定できる
（lineage_extract.walk_leaves を参照）。
"""

import sqlglot
import sqlglot.expressions as exp


def expand_query_ast(query_name: str, queries: dict[str, str], dialect: str = "tsql") -> exp.Expression:
    parsed = sqlglot.parse_one(queries[query_name], dialect=dialect)
    # exp.expand() は sources の値に .subquery() を直接呼ぶため、
    # あらかじめパース済みの Query オブジェクトである必要がある
    # （sqlglot.lineage() の sources= は内部で maybe_parse() しているが、
    # exp.expand() 自体はしない）。
    sources = {
        name: sqlglot.parse_one(sql, dialect=dialect)
        for name, sql in queries.items()
        if name != query_name
    }
    return exp.expand(parsed, sources, dialect=dialect)


def expand_sql(query_name: str, queries: dict[str, str], dialect: str = "tsql") -> str:
    """クエリ参照を再帰的にサブクエリとしてインライン展開したSQLを返す（デバッグ・ログ用途）。"""
    return expand_query_ast(query_name, queries, dialect).sql(dialect=dialect)


def find_query_references(sql: str, queries: dict[str, str], dialect: str = "tsql") -> list[str]:
    """このSQLのFROM/JOIN句が参照している、他の登録済みクエリ名の一覧を返す
    （クエリ間の依存関係グラフを組み立てるための情報収集。展開は行わない）。
    """
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    refs = []
    for table in parsed.find_all(exp.Table):
        if table.db:
            continue
        matched_query = next((q for q in queries if q.lower() == table.name.lower()), None)
        if matched_query is not None:
            refs.append(matched_query)
    return list(dict.fromkeys(refs))  # 重複除去（順序維持）


def find_query_table_collisions(sql: str, queries: dict[str, str], schema: dict, dialect: str = "tsql") -> list[str]:
    """このSQLのFROM/JOIN句にある参照のうち、クエリ名とテーブル名の両方に
    一致するものを検出する。exp.expand() はクエリを優先して展開するため、
    意図しない展開が起きていないか確認するための警告メッセージを返す。

    find_all(exp.Table) はテキスト一致ではなく、FROM/JOIN句のテーブル参照として
    構文的に認識されたノードのみを返す。文字列リテラルやカラム参照（ドット修飾）は
    別のノード種別（exp.Literal / exp.Column）なので、テキストがたまたま
    クエリ名と一致していても誤ってマッチすることはない。
    """
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    warnings = []
    for table in parsed.find_all(exp.Table):
        if table.db:
            # スキーマ修飾（[dbo].[テーブル]等）がある参照はクエリ参照ではない
            continue
        ref_name = table.name
        is_query = any(q.lower() == ref_name.lower() for q in queries)
        is_table = any(t.lower() == ref_name.lower() for t in schema)
        if is_query and is_table:
            warnings.append(f"'{ref_name}' はクエリ名とテーブル名の両方に一致します。クエリとして展開されます。")
    return warnings
