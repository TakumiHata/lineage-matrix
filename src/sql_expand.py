"""クエリ間参照（FROM句に他のAccessクエリ名がそのまま残る）を扱う。

- expand_query_ast(): sqlglot標準の exp.expand() でクエリ参照をサブクエリに
  インライン展開する（analysis.json 用のログ生成にのみ使用し、実際のリネージ
  解決は sqlglot.lineage() の sources= 引数が別途行う）。

チェーン検出（起点クエリから到達可能な全クエリを辿ること）はVBA側が
担当し、input/<起点クエリ>/chain_queries.json として出力される。
lineage-matrix では detect_chain() は不要。

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


def find_query_table_collisions(sql: str, queries: dict[str, str], schema: dict, dialect: str = "tsql") -> list[str]:
    """このSQLのFROM/JOIN句で参照されている名前のうち、クエリ名とテーブル名の
    両方に一致するものを検出する。exp.expand() はクエリを優先して展開するため、
    意図しない展開が起きていないか確認するための警告メッセージを返す。

    find_all(exp.Table) はテキスト一致ではなく、FROM/JOIN句のテーブル参照として
    構文的に認識されたノードのみを返すため、文字列リテラルやカラム参照（ドット修飾）
    を誤ってマッチすることはない。スキーマ修飾（[dbo].[テーブル]等）がある参照は
    クエリ参照ではないため除外する。
    """
    warnings = []
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    for t in parsed.find_all(exp.Table):
        if t.db:
            continue
        is_query = any(q.lower() == t.name.lower() for q in queries)
        is_table = any(tbl.lower() == t.name.lower() for tbl in schema)
        if is_query and is_table:
            warnings.append(f"'{t.name}' はクエリ名とテーブル名の両方に一致します。クエリとして展開されます。")
    return warnings
