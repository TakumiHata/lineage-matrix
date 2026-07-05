"""SQLGlotの lineage() を辿り、出力カラムごとの由来をフラット行として抽出する。"""

import sqlglot
import sqlglot.expressions as exp
from sqlglot.lineage import lineage as sqlglot_lineage


def extract_select_columns(sql: str) -> list[str]:
    # parsed.selects は最も外側の SELECT の出力カラムのみを返す。
    # find_all(exp.Column) + 親でのフィルタでは、サブクエリ内部の
    # SELECT句のカラムまで拾ってしまい重複の原因になるため使わない。
    parsed = sqlglot.parse_one(sql, dialect="tsql")
    return [sel.alias_or_name for sel in parsed.selects]


def restore_schema_casing(table_name: str | None, column_name: str | None, schema: dict) -> str | None:
    # tsqlは大文字小文字を区別しないため、schema付きlineage()解決後の
    # カラム名はASCII部分が小文字化されることがある（例: 工事ID → 工事id）。
    # schema.json の正しい表記に戻し、A5M2定義書とのExcel突合で
    # 一致させられるようにする。
    if not table_name or not column_name or table_name not in schema:
        return column_name
    for real_col in schema[table_name]:
        if real_col.lower() == column_name.lower():
            return real_col
    return column_name


def restore_query_casing(reference_query: str, queries: dict[str, str]) -> str:
    # 「参照クエリ」も参照カラムと同様、tsqlの大文字小文字非区別によって
    # ブラケットなしで書かれた箇所（JOIN の ON 句等）ではASCII部分が
    # 小文字化されることがある（例: クエリ工事CTE集計 → クエリ工事cte集計）。
    # query_dependencies.json に登録された正しい表記に戻す。
    for real_name in queries:
        if real_name.lower() == reference_query.lower():
            return real_name
    return reference_query


def leaf_row(
    query_name: str,
    output_col: str,
    holder: exp.Expr,
    leaf: exp.Expr,
    path: list[str],
    schema: dict,
    queries: dict[str, str],
) -> dict:
    table_name = leaf.name if isinstance(leaf, exp.Table) else None

    # holder は SUM(...) のように集計関数がカラムを直接ラップすることがあるため、
    # holder.this が exp.Column である前提ではなく、式全体からカラム参照を探す。
    col_expr = holder.find(exp.Column)
    column_name = col_expr.name if col_expr is not None else None
    column_name = restore_schema_casing(table_name, column_name, schema)

    # 経路は何階層ネストしていても最終的には物理テーブル1つ・カラム1つに
    # 行き着くため、参照クエリだけが「経路の深さ分」複数値になりうる。
    # ここでは丸めず外側→内側の順のリストのまま持ち回し、
    # 実際の列展開（参照クエリ1, 参照クエリ2, ...）は report.py で行う。
    reference_path = [restore_query_casing(p, queries) for p in path]

    return {
        "開始クエリ": query_name,
        "最終出力カラム": output_col,
        "参照クエリパス": reference_path,
        "参照テーブル": table_name or "不明",
        "参照カラム": column_name or "不明",
    }


def walk_leaves(node):
    """ルートから各リーフ（downstreamが空＝実テーブル参照）までの経路をたどり、
    (由来を持つノードの式, リーフの式, 通過したサブクエリエイリアスの経路) を列挙する。
    node.walk() は木全体を平坦に返すだけで経路情報を失うため使わない。

    経路には2種類の要素が混在しうる：
    - reference_node_name: SQL内に直接書かれたローカルな無名サブクエリの
      エイリアス（例：サブクエリ1）
    - source_name: sqlglot.lineage() の sources= 引数（exp.expand()の
      `/* source: 名前 */` コメント）によって判明する、そのホップが実際に
      属している登録済みクエリ名。同じ登録済みクエリの内部に留まっている
      間は変化しないため、遷移したタイミングでのみ経路に追加する。
    """
    if not node.downstream:
        yield node.expression, node.expression, []
        return

    def _walk(n, path: list[str], current_source: str):
        for d in n.downstream:
            new_path = path
            if d.source_name and d.source_name != current_source:
                new_path = new_path + [d.source_name]
                current_source = d.source_name
            if d.reference_node_name:
                new_path = new_path + [d.reference_node_name]

            if not d.downstream:
                yield n.expression, d.expression, new_path
            else:
                yield from _walk(d, new_path, current_source)

    yield from _walk(node, [], "")


def extract_lineage_rows(
    query_name: str, sql: str, schema: dict, queries: dict[str, str], error_log: list[dict]
) -> list[dict]:
    rows = []

    # sources= には自分自身を含めない。exp.expand() は循環検出を行わないため、
    # 自己参照的な名前がSQL中にあった場合の無限再帰を避ける。
    sources = {name: q_sql for name, q_sql in queries.items() if name != query_name}

    for output_col in extract_select_columns(sql):
        try:
            node = sqlglot_lineage(column=output_col, sql=sql, schema=schema, sources=sources, dialect="tsql")
        except Exception as e:
            error_log.append({
                "クエリ": query_name,
                "種別": "lineage失敗",
                "対象カラム": output_col,
                "メッセージ": str(e),
            })
            rows.append({
                "開始クエリ": query_name,
                "最終出力カラム": f"{output_col} ({e})",
                "参照クエリパス": ["解析失敗"],
                "参照テーブル": "不明",
                "参照カラム": "不明",
            })
            continue

        for holder, leaf, path in walk_leaves(node):
            rows.append(leaf_row(query_name, output_col, holder, leaf, path, schema, queries))

    return rows
