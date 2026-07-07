"""クエリ間参照（FROM句に他のAccessクエリ名がそのまま残る）を扱う。

- expand_query_ast(): sqlglot標準の exp.expand() でクエリ参照をサブクエリに
  インライン展開する（analysis.json 用のログ生成にのみ使用し、実際のリネージ
  解決は sqlglot.lineage() の sources= 引数が別途行う）。
- detect_chain(): 起点クエリから参照されるクエリを再帰的に辿り、チェーンに
  含まれる全クエリを特定する。

exp.expand() は展開したサブクエリに `/* source: <元の名前> */` という
コメントを自動付与し、sqlglot.lineage() 側はこれを検出して
Node.source_name に反映する。これにより、末端の物理テーブルまで辿る過程で
「どの登録済みクエリの内部か」を独自コードなしで判定できる
（lineage_extract.walk_leaves を参照）。
"""

import sqlglot
import sqlglot.expressions as exp


def _referenced_table_names(sql: str, dialect: str = "tsql") -> list[str]:
    """このSQLのFROM/JOIN句で参照されている名前（クエリ名またはテーブル名）の
    一覧を返す。find_all(exp.Table) はテキスト一致ではなく、FROM/JOIN句の
    テーブル参照として構文的に認識されたノードのみを返すため、文字列リテラルや
    カラム参照（ドット修飾）は別のノード種別（exp.Literal / exp.Column）なので
    誤ってマッチすることはない。スキーマ修飾（[dbo].[テーブル]等）がある参照は
    クエリ参照ではないため除外する。
    """
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    return [t.name for t in parsed.find_all(exp.Table) if not t.db]


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


def find_query_table_collisions(sql: str, queries: dict[str, str], schema: dict, dialect: str = "tsql") -> list[str]:
    """このSQLの参照のうち、クエリ名とテーブル名の両方に一致するものを検出する。
    exp.expand() はクエリを優先して展開するため、意図しない展開が起きていないか
    確認するための警告メッセージを返す。
    """
    warnings = []
    for ref_name in _referenced_table_names(sql, dialect):
        is_query = any(q.lower() == ref_name.lower() for q in queries)
        is_table = any(t.lower() == ref_name.lower() for t in schema)
        if is_query and is_table:
            warnings.append(f"'{ref_name}' はクエリ名とテーブル名の両方に一致します。クエリとして展開されます。")
    return warnings


def detect_chain(
    start_query: str, all_queries: dict[str, str], query_name_set: set[str], dialect: str = "tsql"
) -> dict[str, str]:
    """起点クエリから到達可能な全クエリを返す {クエリ名: SQL}。

    各クエリのSQLで参照されている名前を query_name_set と照合することで
    「クエリ参照かテーブル参照か」を判別し、クエリ参照であれば再帰的に辿る。
    visited（訪問済み）を管理するため、循環参照があっても無限ループにはならない。
    """
    visited: dict[str, str] = {}
    stack = [start_query]

    while stack:
        name = stack.pop()
        if name in visited or name not in all_queries:
            continue
        visited[name] = all_queries[name]

        for ref_name in _referenced_table_names(all_queries[name], dialect):
            matched = next((q for q in query_name_set if q.lower() == ref_name.lower()), None)
            if matched is not None and matched not in visited:
                stack.append(matched)

    return visited
