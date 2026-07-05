"""VBAが出力する input/<起点クエリ>/*.json を解析し、クエリの出力カラムが
どのテーブル・カラムに由来するかをフラットテーブル形式で
output/<起点クエリ>/lineage.xlsx に出力するスクリプト。

input/ は起点クエリ（クエリ連鎖の一番外側のクエリ）ごとにフォルダ分けされており、
各フォルダに query_dependencies.json（そのグループに属するクエリ一覧）と
table.json（そのグループで使う物理テーブルのスキーマ）が入っている。
output/ 側も同じ起点クエリ単位のフォルダ構成で、フォルダごとに
lineage.xlsx・テーブル使用状況（table_usage.xlsx）・クエリ依存関係図
（query_graph.md）・解析ログ（analysis.json）・エラーログ（error.json）を出力する。
"""

from pathlib import Path

from config import DEPENDENCIES_FILENAME, OUTPUT_DIR, TABLES_FILENAME
from lineage_extract import extract_lineage_rows, extract_select_columns, extract_used_tables
from loader import discover_query_groups, load_queries, load_schema
from report import (
    build_lineage_dataframe,
    build_query_graph_mermaid,
    build_table_usage_dataframe,
    write_group_output,
)
from sql_expand import expand_query_ast, find_query_references, find_query_table_collisions


def process_group(group_dir: Path) -> None:
    group_name = group_dir.name
    queries = load_queries(group_dir / DEPENDENCIES_FILENAME)
    schema = load_schema(group_dir / TABLES_FILENAME)

    analysis_log: dict[str, dict] = {}
    error_log: list[dict] = []
    table_usage_rows: list[dict] = []
    query_graph_edges: list[tuple[str, str]] = []

    # クエリ参照の展開（sqlglot標準の exp.expand()）は sqlglot_lineage() の
    # sources= 引数が内部で行うため、ここでは analysis.json 用のログ取得と
    # クエリ名／テーブル名の衝突警告、テーブル使用状況・依存関係グラフの
    # 抽出のためだけに呼び出す。
    for name in queries:
        select_cols = extract_select_columns(queries[name], schema)

        collisions = find_query_table_collisions(queries[name], queries, schema)
        error_log.extend({"クエリ": name, "種別": "クエリ名衝突警告", "メッセージ": w} for w in collisions)

        query_graph_edges.extend((name, ref) for ref in find_query_references(queries[name], queries))

        try:
            ast_result = expand_query_ast(name, queries)
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": repr(ast_result).splitlines(),
                "expand_sql": ast_result.sql(dialect="tsql"),
            }
            table_usage_rows.extend(
                {"開始クエリ": name, "参照テーブル": table} for table in extract_used_tables(ast_result)
            )
        except Exception as e:
            error_log.append({"クエリ": name, "種別": "expand_sql失敗", "メッセージ": str(e)})
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": None,
                "expand_sql": None,
            }

    all_rows = []
    for query_name, sql in queries.items():
        all_rows.extend(extract_lineage_rows(query_name, sql, schema, queries, error_log))

    df_lineage = build_lineage_dataframe(all_rows)
    df_table_usage = build_table_usage_dataframe(table_usage_rows)
    query_graph_mermaid = build_query_graph_mermaid(list(queries), query_graph_edges)

    out_dir = OUTPUT_DIR / group_name
    write_group_output(out_dir, df_lineage, df_table_usage, query_graph_mermaid, analysis_log, error_log)

    print(f"[{group_name}] クエリ数={len(queries)}, 行数={len(df_lineage)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx, table_usage.xlsx, query_graph.md, analysis.json, error.json")


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
