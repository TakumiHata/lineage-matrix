"""クエリ間参照（FROM句に他のAccessクエリ名がそのまま残る）を扱う。

- expand_query_ast(): sqlglot標準の exp.expand() でクエリ参照をサブクエリに
  インライン展開する。analysis.json 用のログ（expand_query_ast_repr / expand_sql）
  と、qualify() + build_scope() によるリネージ解決の両方がこの展開結果を使う
  （lineage_extract.analyze_query() 参照）。

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
from sqlglot.optimizer.qualify import qualify

from config import DIALECT


def expand_query_ast(query_name: str, queries: dict[str, str], dialect: str = DIALECT) -> exp.Expression:
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


def qualify_expanded(expanded: exp.Expression, schema: dict, dialect: str = DIALECT) -> exp.Expression:
    """expand_query_ast() の結果をqualify()でスキーマ検証する。

    main.py（analysis.json用ログ・lineage解決）とvalidate.py（事前検証）の両方が
    「展開してqualify検証する」という同じ手順を必要とするため、ここに集約している。
    validate_qualify_columns=True にすることで、JOIN先の複数テーブルに存在する
    同名カラムが非修飾のまま参照され一意に解決できない場合も、"不明"のまま
    無警告で処理が進むのではなく例外として検知できる。
    qualify() は引数を破壊的に変更し、戻り値は同じオブジェクト。
    """
    return qualify(expanded, schema=schema, dialect=dialect, validate_qualify_columns=True, identify=False)
