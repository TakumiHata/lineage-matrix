"""クエリ単位で「直接触れる物理テーブル」と「直接呼び出す登録済みクエリ」を分類し、
呼び出し木に沿って間接参照テーブルをボトムアップで積み上げる。

lineage_extract.py の to_node() ベースの列リネージは、SELECT出力カラムに寄与する
テーブルしか捕捉できない（JOIN条件やWHERE句のサブクエリだけで参照され、SELECT結果に
一切現れないテーブルを見落とす）。テーブル参照マトリックスの「実際に参照している
テーブル」はJOIN/WHERE専用の参照も含むべきのため、展開前のSQLを直接
find_all(exp.Table) で走査する別経路をここに用意する。
"""

import sqlglot
import sqlglot.expressions as exp

from casing import restore_table_casing
from config import DIALECT
from sql_expand import find_ci


def direct_references(
    sql: str, queries: dict[str, str], schema: dict, dialect: str = DIALECT
) -> tuple[set[str], set[str]]:
    """このクエリ自身のSQL（クエリ参照展開前）が直接触れる物理テーブル名の集合と、
    直接呼び出す登録済みクエリ名の集合を返す。WITH句のCTE名はFROM句上でも
    exp.Tableとして現れるため、物理テーブルとして誤検出しないよう除外する。
    非修飾名がqueriesとschemaの両方に一致する場合はexp.expand()と同じ優先順位で
    クエリとして扱う（この場合の警告は sql_expand.find_query_table_collisions が
    別途出す）。
    """
    parsed = sqlglot.parse_one(sql, dialect=dialect)
    cte_names = {c.alias for c in parsed.find_all(exp.CTE)}

    tables: set[str] = set()
    child_queries: set[str] = set()
    for t in parsed.find_all(exp.Table):
        if t.name in cte_names:
            continue
        if not t.db:
            matched_query = find_ci(t.name, queries)
            if matched_query:
                child_queries.add(matched_query)
                continue
        tables.add(restore_table_casing(t.name, schema) or t.name)

    return tables, child_queries


def resolve_table_usage(
    queries: dict[str, str], schema: dict, error_log: list[dict], dialect: str = DIALECT
) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    """クエリごとの {"direct": 直接参照テーブル集合, "indirect": 間接参照テーブル集合} と、
    直接呼び出し子クエリのグラフ {クエリ名: 子クエリ名の集合} を返す。

    間接参照は「自分の直接参照 ∪ 全子孫クエリの(直接∪間接)」から自分の直接分を
    引いたもの。子孫の再帰は結果をキャッシュしつつ、循環呼び出しを検出したら
    error_logに警告を積んでその経路の探索だけ打ち切る（Accessクエリの通常の
    チェーンでは循環は起きない想定だが、入力データの誤りに備える）。
    """
    direct: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for name, sql in queries.items():
        direct[name], children[name] = direct_references(sql, queries, schema, dialect)

    subtree_cache: dict[str, set[str]] = {}

    def subtree_tables(name: str, visiting: frozenset[str]) -> set[str]:
        if name in subtree_cache:
            return subtree_cache[name]
        if name in visiting:
            path = " -> ".join([*visiting, name])
            error_log.append(
                {
                    "クエリ": name,
                    "種別": "循環参照警告",
                    "メッセージ": f"クエリ呼び出しの循環を検出したため、この経路の探索を打ち切りました: {path}",
                }
            )
            return set()

        result = set(direct[name])
        for child in children[name]:
            result |= subtree_tables(child, visiting | {name})
        subtree_cache[name] = result
        return result

    usage: dict[str, dict[str, set[str]]] = {}
    for name in queries:
        all_tables = subtree_tables(name, frozenset())
        usage[name] = {"direct": direct[name], "indirect": all_tables - direct[name]}

    return usage, children
