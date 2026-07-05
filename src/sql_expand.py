"""クエリ間参照（FROM句に他のAccessクエリ名がそのまま残る）を
再帰的にサブクエリとしてインライン展開する。
"""

import sqlglot
import sqlglot.expressions as exp


def expand_query_ast(
    query_name: str, queries: dict[str, str], schema: dict, visited: set[str], warnings: list[str]
) -> exp.Expression:
    parsed = sqlglot.parse_one(queries[query_name], dialect="tsql")

    # find_all(exp.Table) はテキスト一致ではなく、FROM/JOIN句のテーブル参照として
    # 構文的に認識されたノードのみを返す。文字列リテラルやカラム参照（ドット修飾）は
    # 別のノード種別（exp.Literal / exp.Column）なので、テキストがたまたま
    # クエリ名と一致していても誤ってマッチすることはない。
    for table in list(parsed.find_all(exp.Table)):
        if table.db:
            # スキーマ修飾（[dbo].[テーブル]等）がある参照はクエリ参照ではない
            continue

        ref_name = table.name
        matched_query = next((q for q in queries if q.lower() == ref_name.lower()), None)
        if matched_query is None or matched_query in visited:
            continue

        if any(t.lower() == ref_name.lower() for t in schema):
            warnings.append(f"'{ref_name}' はクエリ名とテーブル名の両方に一致します。クエリとして展開しました。")

        sub_ast = expand_query_ast(matched_query, queries, schema, visited | {matched_query}, warnings)
        alias = table.alias or ref_name
        subquery = exp.Subquery(this=sub_ast, alias=exp.TableAlias(this=exp.to_identifier(alias)))
        table.replace(subquery)

    return parsed


def expand_sql(query_name: str, queries: dict[str, str], schema: dict, warnings: list[str] | None = None) -> str:
    """クエリ参照を再帰的にサブクエリとしてインライン展開する。

    AI変換後のSQLはAccessのクエリ間参照（FROM句に他クエリ名がそのまま残る）を
    解決しないため、SQLGlotからは物理テーブルとして誤認識される。
    テキストへの正規表現マッチではなく、一度パースしてから exp.Table ノードの
    名前を query_dependencies.json のクエリ名一覧と照合することで、
    「本当にFROM/JOIN句のテーブル参照として使われているか」を構文的に保証する。
    """
    if warnings is None:
        warnings = []
    return expand_query_ast(query_name, queries, schema, {query_name}, warnings).sql(dialect="tsql")
