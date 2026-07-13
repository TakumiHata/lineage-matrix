"""SQLGlotの lineage() を辿り、出力カラムごとの由来をフラット行として抽出する。"""

import sqlglot
import sqlglot.expressions as exp
from sqlglot.lineage import build_scope, to_node
from sqlglot.optimizer.qualify import qualify

from casing import restore_query_casing, restore_schema_casing, restore_table_casing
from config import DIALECT
from sql_expand import expand_query_ast, qualify_expanded


def _has_star(parsed: exp.Expression) -> bool:
    return any(
        isinstance(sel, exp.Star) or (isinstance(sel, exp.Column) and isinstance(sel.this, exp.Star))
        for sel in parsed.selects
    )


def extract_select_columns(sql: str, schema: dict, dialect: str = DIALECT) -> list[str]:
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


def leaf_row(
    query_name: str,
    output_col: str,
    holder: exp.Expr,
    leaf: exp.Expr,
    path: list[str],
    schema: dict,
    queries: dict[str, str],
) -> dict:
    # leaf.name は qualify() 通過後のASCII部分が小文字化されている可能性があるため、
    # "参照テーブル" に出す前に schema 上の本来の表記へ復元する。
    table_name = leaf.name if isinstance(leaf, exp.Table) else None
    table_name = restore_table_casing(table_name, schema)

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


def _failure_row(query_name: str, output_col: str, error: Exception) -> dict:
    """lineage.xlsx 用の失敗行（1カラム分）を作る。"""
    return {
        "開始クエリ": query_name,
        "最終出力カラム": f"{output_col} ({error})",
        "参照クエリパス": ["解析失敗"],
        "参照テーブル": "不明",
        "参照カラム": "不明",
    }


def analyze_query(
    query_name: str, sql: str, schema: dict, queries: dict[str, str], error_log: list[dict]
) -> dict:
    """1クエリを解析し、analysis.json のエントリ（selectカラム・展開後ASTのrepr・
    展開後SQL・出力カラムごとのlineage行）を1つのdictにまとめて返す。

    expand_query_ast() と qualify() をここで1回だけ実行し、その結果を
    analysis.json 用のログとlineage解決の両方に使い回す。以前はこの2つを
    main.py側とここでそれぞれ別に expand_query_ast + qualify していたため、
    片方は成功扱いなのにもう片方は失敗扱いになる（analysis.jsonとlineage.xlsxの
    判定がズレる）ことがあった。1回の計算結果を共有することでズレをなくす。

    sqlglot.lineage.lineage() はカラムを「名前」で引くため、AS別名のない
    SELECT句で複数の出力カラムが同名になる場合（例: 結合した2テーブルが
    どちらも「名称」カラムを持ち、SELECT A.名称, B.名称 のように書かれた場合）、
    常に最初に出現した同名カラムに解決されてしまい、2番目以降の出力カラムの
    参照テーブル・カラムが誤って重複する。to_node() は位置（int）でも引けるため、
    expand_query_ast() でクエリ間参照を展開・qualify_expanded()してスコープを構築した
    上で、ここでは位置指定で解決し同名出力カラムを正しく区別する
    （qualify_expanded() のvalidate_qualify_columns=Trueの意図は sql_expand.py 参照）。
    """
    output_cols = extract_select_columns(sql, schema)
    entry: dict = {
        "extract_select_columns": output_cols,
        "expand_query_ast_repr": None,
        "expand_sql": None,
        "lineage": [],
    }

    try:
        # expand_sql（見やすさのため元の大文字小文字を保つ）は qualify() で識別子が
        # 正規化される前の expanded から文字列として先に確定させる。qualify() は
        # 引数を破壊的に変更するが、その時点で expanded 自体はもう参照しないため
        # .copy() は不要（文字列化済みの entry の値には影響しない）。
        expanded = expand_query_ast(query_name, queries)
        entry["expand_query_ast_repr"] = repr(expanded).splitlines()
        entry["expand_sql"] = expanded.sql(dialect=DIALECT)
        qualified = qualify_expanded(expanded, schema)
        scope = build_scope(qualified)
    except Exception as e:
        # expand_query_ast() は成功したが qualify()/build_scope() が失敗した場合、
        # 上ですでに entry に repr/expand_sql を書き込んでいるため、ここで明示的に
        # None へ戻さないと「一部だけ成功したように見える」矛盾した状態が残る。
        entry["expand_query_ast_repr"] = None
        entry["expand_sql"] = None
        error_log.append({"クエリ": query_name, "種別": "expand_sql失敗", "メッセージ": str(e)})
        entry["lineage"] = [_failure_row(query_name, c, e) for c in output_cols]
        return entry

    cache: dict = {}
    for i, output_col in enumerate(output_cols):
        try:
            node = to_node(i, scope, dialect=DIALECT, schema=schema, _cache=cache)
        except Exception as e:
            error_log.append({"クエリ": query_name, "種別": "lineage失敗", "対象カラム": output_col, "メッセージ": str(e)})
            entry["lineage"].append(_failure_row(query_name, output_col, e))
            continue

        for holder, leaf, path in walk_leaves(node):
            entry["lineage"].append(leaf_row(query_name, output_col, holder, leaf, path, schema, queries))

    return entry
