"""VBAが出力する input/<起点クエリ>/*.json を解析し、クエリの出力カラムが
どのテーブル・カラムに由来するかをフラットテーブル形式で
output/<起点クエリ>/lineage.xlsx に出力するスクリプト。

input/ は起点クエリ（クエリ連鎖の一番外側のクエリ）ごとにフォルダ分けされており、
各フォルダに query_dependencies.json（そのグループに属するクエリ一覧）と
table.json（そのグループで使う物理テーブルのスキーマ）が入っている。
output/ 側も同じ起点クエリ単位のフォルダ構成で、フォルダごとに
lineage.xlsx・解析ログ（analysis.json）・エラーログ（error.json）を出力する。
"""

import json
from pathlib import Path

import pandas as pd
import sqlglot
import sqlglot.expressions as exp
from sqlglot.lineage import lineage as sqlglot_lineage

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")
DEPENDENCIES_FILENAME = "query_dependencies.json"
TABLES_FILENAME = "table.json"


def discover_query_groups(input_dir: Path = INPUT_DIR) -> list[Path]:
    """input/<起点クエリ名>/ 形式のグループフォルダを列挙する。"""
    return sorted(p for p in input_dir.iterdir() if p.is_dir())


def load_queries(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_schema(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }


def _expand_query_ast(
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

        sub_ast = _expand_query_ast(matched_query, queries, schema, visited | {matched_query}, warnings)
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
    return _expand_query_ast(query_name, queries, schema, {query_name}, warnings).sql(dialect="tsql")


def extract_select_columns(sql: str) -> list[str]:
    # parsed.selects は最も外側の SELECT の出力カラムのみを返す。
    # find_all(exp.Column) + 親でのフィルタでは、サブクエリ内部の
    # SELECT句のカラムまで拾ってしまい重複の原因になるため使わない。
    parsed = sqlglot.parse_one(sql, dialect="tsql")
    return [sel.alias_or_name for sel in parsed.selects]


def _restore_schema_casing(table_name: str | None, column_name: str | None, schema: dict) -> str | None:
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


def _restore_query_casing(reference_query: str, queries: dict[str, str]) -> str:
    # 「参照クエリ」も参照カラムと同様、tsqlの大文字小文字非区別によって
    # ブラケットなしで書かれた箇所（JOIN の ON 句等）ではASCII部分が
    # 小文字化されることがある（例: クエリ工事CTE集計 → クエリ工事cte集計）。
    # query_dependencies.json に登録された正しい表記に戻す。
    for real_name in queries:
        if real_name.lower() == reference_query.lower():
            return real_name
    return reference_query


def _leaf_row(
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
    column_name = _restore_schema_casing(table_name, column_name, schema)

    # 経路は何階層ネストしていても最終的には物理テーブル1つ・カラム1つに
    # 行き着くため、参照クエリだけが「経路の深さ分」複数値になりうる。
    # ここでは丸めず外側→内側の順のリストのまま持ち回し、
    # 実際の列展開（参照クエリ1, 参照クエリ2, ...）は process_group() で行う。
    reference_path = [_restore_query_casing(p, queries) for p in path]

    return {
        "開始クエリ": query_name,
        "最終出力カラム": output_col,
        "参照クエリパス": reference_path,
        "参照テーブル": table_name or "不明",
        "参照カラム": column_name or "不明",
    }


def _walk_leaves(node):
    """ルートから各リーフ（downstreamが空＝実テーブル参照）までの経路をたどり、
    (由来を持つノードの式, リーフの式, 通過したサブクエリエイリアスの経路) を列挙する。
    node.walk() は木全体を平坦に返すだけで経路情報を失うため使わない。
    """
    if not node.downstream:
        yield node.expression, node.expression, []
        return

    def _walk(n, path: list[str]):
        for d in n.downstream:
            if not d.downstream:
                yield n.expression, d.expression, path
            else:
                new_path = path + ([d.reference_node_name] if d.reference_node_name else [])
                yield from _walk(d, new_path)

    yield from _walk(node, [])


def extract_lineage_rows(
    query_name: str, sql: str, schema: dict, queries: dict[str, str], error_log: list[dict]
) -> list[dict]:
    rows = []

    for output_col in extract_select_columns(sql):
        try:
            node = sqlglot_lineage(column=output_col, sql=sql, schema=schema, dialect="tsql")
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

        for holder, leaf, path in _walk_leaves(node):
            rows.append(_leaf_row(query_name, output_col, holder, leaf, path, schema, queries))

    return rows


def _build_lineage_dataframe(rows: list[dict]) -> pd.DataFrame:
    """rows の「参照クエリパス」（外側→内側のクエリ名リスト）を、そのグループ内の
    最大ネスト数に合わせて 参照クエリ1, 参照クエリ2, ... の固定列に展開する。
    経路が空（直接参照）の行は 参照クエリ1 に「（直接）」を入れ、残りは空欄にする。
    """
    max_depth = max((len(r["参照クエリパス"]) for r in rows), default=1)
    max_depth = max(max_depth, 1)
    ref_columns = [f"参照クエリ{i}" for i in range(1, max_depth + 1)]
    columns = ["開始クエリ", "最終出力カラム", *ref_columns, "参照テーブル", "参照カラム"]

    expanded_rows = []
    for r in rows:
        path = r["参照クエリパス"]
        row = {"開始クエリ": r["開始クエリ"], "最終出力カラム": r["最終出力カラム"]}
        for i, col in enumerate(ref_columns, start=1):
            if not path:
                row[col] = "（直接）" if i == 1 else ""
            else:
                row[col] = path[i - 1] if i <= len(path) else ""
        row["参照テーブル"] = r["参照テーブル"]
        row["参照カラム"] = r["参照カラム"]
        expanded_rows.append(row)

    return pd.DataFrame(expanded_rows, columns=columns)


def process_group(group_dir: Path) -> None:
    group_name = group_dir.name
    queries = load_queries(group_dir / DEPENDENCIES_FILENAME)
    schema = load_schema(group_dir / TABLES_FILENAME)

    analysis_log: dict[str, dict] = {}
    error_log: list[dict] = []

    expanded_queries: dict[str, str] = {}
    for name in queries:
        select_cols = extract_select_columns(queries[name])

        try:
            warnings: list[str] = []
            ast_result = _expand_query_ast(name, queries, schema, {name}, warnings)
            error_log.extend({"クエリ": name, "種別": "クエリ名衝突警告", "メッセージ": w} for w in warnings)

            expanded_sql = ast_result.sql(dialect="tsql")
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": repr(ast_result).splitlines(),
                "expand_sql": expanded_sql,
            }

            expanded_queries[name] = expanded_sql
        except Exception as e:
            error_log.append({"クエリ": name, "種別": "expand_sql失敗", "メッセージ": str(e)})
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": None,
                "expand_sql": None,
            }
            expanded_queries[name] = queries[name]

    all_rows = []
    for query_name, sql in expanded_queries.items():
        all_rows.extend(extract_lineage_rows(query_name, sql, schema, queries, error_log))

    df_lineage = _build_lineage_dataframe(all_rows)

    out_dir = OUTPUT_DIR / group_name
    out_dir.mkdir(parents=True, exist_ok=True)

    df_lineage.to_excel(out_dir / "lineage.xlsx", index=False)
    with open(out_dir / "analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis_log, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(out_dir / "error.json", "w", encoding="utf-8") as f:
        json.dump(error_log, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[{group_name}] クエリ数={len(queries)}, 行数={len(df_lineage)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx, analysis.json, error.json")


def main() -> None:
    groups = discover_query_groups()
    print(f"読み込んだクエリグループ数: {len(groups)}")
    for group_dir in groups:
        print(f"  - {group_dir.name}")
    print()

    for group_dir in groups:
        process_group(group_dir)


if __name__ == "__main__":
    main()
