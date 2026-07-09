"""SQLGlotの lineage() を辿り、出力カラムごとの由来をフラット行として抽出する。"""

import sqlglot
import sqlglot.expressions as exp
from sqlglot.lineage import build_scope, to_node
from sqlglot.optimizer.qualify import qualify


def _has_star(parsed: exp.Expression) -> bool:
    return any(
        isinstance(sel, exp.Star) or (isinstance(sel, exp.Column) and isinstance(sel.this, exp.Star))
        for sel in parsed.selects
    )


def extract_select_columns(sql: str, schema: dict, dialect: str = "tsql") -> list[str]:
    # parsed.selects は最も外側の SELECT の出力カラムのみを返す。
    # find_all(exp.Column) + 親でのフィルタでは、サブクエリ内部の
    # SELECT句のカラムまで拾ってしまい重複の原因になるため使わない。
    parsed = sqlglot.parse_one(sql, dialect=dialect)

    if not _has_star(parsed):
        # 通常はここで完結。qualify() を経由しないため大文字小文字がそのまま保たれる。
        return [sel.alias_or_name for sel in parsed.selects]

    # SELECT * を含む場合のみ、スキーマを使って実際のカラム名に展開する。
    # qualify(expand_stars=True) はデフォルトで有効なので、schema付きで呼ぶだけで
    # * を実カラム名のリストに展開できる（lineage()が内部でやっているのと同じ処理）。
    # ただし qualify() は識別子を正規化する（例: 工事ID → 工事id）ため、
    # テーブルのエイリアスを実テーブル名に戻したうえで restore_schema_casing() で
    # 本来の大文字小文字に復元する。展開先が物理テーブルでない場合（他クエリの
    # 参照に対する * 等）は expand_query_ast() での事前展開が必要になるため、
    # ここでは schema 未登録の参照は諦めて "*" のまま返す。
    try:
        qualified = qualify(parsed, schema=schema, dialect=dialect, validate_qualify_columns=False, identify=False)
    except Exception:
        return [sel.alias_or_name for sel in parsed.selects]

    alias_to_table = {t.alias_or_name: t.name for t in qualified.find_all(exp.Table)}

    columns = []
    for sel in qualified.selects:
        col_expr = sel.this if isinstance(sel, exp.Alias) else sel
        if isinstance(col_expr, exp.Column) and col_expr.table:
            real_table = alias_to_table.get(col_expr.table, col_expr.table)
            columns.append(restore_schema_casing(real_table, col_expr.name, schema) or col_expr.name)
        else:
            columns.append(sel.alias_or_name)
    return columns


def extract_used_tables(expanded_ast: exp.Expression) -> list[str]:
    """SELECT出力カラムのリネージだけでは、WHERE句やJOIN条件だけで参照され
    SELECT結果には一切現れないテーブル（例: WHERE句のサブクエリでのフィルタ
    条件にのみ使われるテーブル）が漏れてしまう。expand_query_ast() で
    クエリ間参照を展開済みのASTに対して find_all(exp.Table) を行うことで、
    SELECT/WHERE/JOIN/GROUP BY/HAVING/ORDER BYを問わず、そのクエリが実際に
    触れている物理テーブルを漏れなく抽出する。

    展開済みASTを渡す前提のため、ここで見つかる exp.Table はクエリ参照
    （expand_query_ast が Subquery に置き換え済み）ではなく、すべて物理テーブル
    ……のはずだが、WITH句のCTE名もFROM句では exp.Table として現れるため、
    同じクエリ内で定義されたCTE名（exp.CTE.alias）は物理テーブルではないので除外する。
    """
    cte_names = {c.alias for c in expanded_ast.find_all(exp.CTE)}
    tables = [t.name for t in expanded_ast.find_all(exp.Table) if t.name not in cte_names]
    return list(dict.fromkeys(tables))  # 重複除去（順序維持）


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

    output_cols = extract_select_columns(sql, schema)

    # sqlglot.lineage.lineage() はカラムを「名前」で引くため、AS別名のない
    # SELECT句で複数の出力カラムが同名になる場合（例: 結合した2テーブルが
    # どちらも「名称」カラムを持ち、SELECT A.名称, B.名称 のように書かれた場合）、
    # 常に最初に出現した同名カラムに解決されてしまい、2番目以降の出力カラムの
    # 参照テーブル・カラムが誤って重複する。to_node() は位置（int）でも引けるため、
    # lineage() 内部と同じ前処理（クエリ間参照展開→qualify→スコープ構築）を行った上で
    # ここでは位置指定で解決し、同名出力カラムを正しく区別する。
    # validate_qualify_columns=True にすることで、JOIN先の複数テーブルに存在する
    # 同名カラムが非修飾のまま参照され一意に解決できない場合も、"不明"のまま
    # 無警告で処理が進むのではなく例外として検知しエラーログに残す。
    try:
        parsed = sqlglot.parse_one(sql, dialect="tsql")
        if sources:
            parsed = exp.expand(
                parsed,
                {name: sqlglot.parse_one(s, dialect="tsql") for name, s in sources.items()},
                dialect="tsql",
            )
        qualified = qualify(parsed, schema=schema, dialect="tsql", validate_qualify_columns=True, identify=False)
        scope = build_scope(qualified)
    except Exception as e:
        for output_col in output_cols:
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
        return rows

    cache: dict = {}
    for i, output_col in enumerate(output_cols):
        try:
            node = to_node(i, scope, dialect="tsql", schema=schema, _cache=cache)
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
