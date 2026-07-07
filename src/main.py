"""VBAが出力する input/query_dependencies.json・input/table.json（全クエリ・
全テーブルのフラットな一覧）を解析し、input/ 直下のサブフォルダ名で指定された
起点クエリごとに、クエリの出力カラムがどのテーブル・カラムに由来するかを
フラットテーブル形式で output/<起点クエリ>/lineage.xlsx に出力するスクリプト。

チェーン検出（起点クエリから芋づる式にクエリ依存関係を辿ること）は、
以前はVBA側が担いフォルダ単位に切り分け済みのデータを出力していたが、
現在は lineage-matrix 側（sqlglot AST）が担う。VBAはチェーン追跡を一切行わず、
全クエリ・全テーブルをフラットに出力するだけでよい。

output/ 側は起点クエリ単位のフォルダ構成で、フォルダごとに
lineage.xlsx・テーブル使用状況（table_usage.xlsx）・チェーン検出結果
（chain_queries.json）・解析ログ（analysis.json）・エラーログ（error.json）を
出力する。

チェーン検出で特定したクエリ（Access SQLのまま）は output/<起点クエリ>/
chain_queries.json としても出力される。これをAI変換にかけた結果を
input/<起点クエリ>/converted_queries.json として配置しておくと、次回実行時
そちらのSQL（AI変換済み・SQL Server用）がリネージ解析に優先して使われる。
存在しない場合はチェーン検出結果のSQLがそのまま使われる。
"""

import sys

from config import INPUT_DIR, OUTPUT_DIR, QUERY_DEPENDENCIES_FILE, TABLES_FILE, discover_start_queries
from lineage_extract import extract_lineage_rows, extract_select_columns, extract_used_tables
from loader import load_queries, load_schema
from report import build_lineage_dataframe, build_table_usage_dataframe, write_group_output
from sql_expand import detect_chain, expand_query_ast, find_query_table_collisions


def resolve_queries_for_analysis(start_query: str, chain_queries: dict[str, str]) -> tuple[dict[str, str], str]:
    """リネージ解析（sql_expand → lineage_extract）に使うクエリ辞書を返す。

    input/<起点クエリ>/converted_queries.json （AI変換済みのSQL Server用SQL）が
    存在すればそちらを優先し、なければチェーン検出結果（chain_queries.json と
    同じ内容、Access SQLのまま）をそのまま使う。戻り値の2つ目はどちらを使ったかの
    表示用ラベル。
    """
    converted_path = INPUT_DIR / start_query / "converted_queries.json"
    if converted_path.exists():
        return load_queries(converted_path), f"{converted_path}（AI変換済み）"
    return chain_queries, "チェーン検出結果（Access SQLのまま）"


def process_group(start_query: str, all_queries: dict[str, str], schema: dict, query_name_set: set[str]) -> None:
    group_name = start_query
    chain_queries = detect_chain(start_query, all_queries, query_name_set)

    if not chain_queries:
        print(f"[{group_name}] 警告: 起点クエリ '{start_query}' が query_dependencies.json に見つかりません。スキップします。")
        return

    queries, source_label = resolve_queries_for_analysis(start_query, chain_queries)
    print(f"[{group_name}] リネージ解析に使うSQL: {source_label}")

    analysis_log: dict[str, dict] = {}
    error_log: list[dict] = []
    table_usage_rows: list[dict] = []

    # クエリ参照の展開（sqlglot標準の exp.expand()）は sqlglot_lineage() の
    # sources= 引数が内部で行うため、ここでは analysis.json 用のログ取得と
    # クエリ名／テーブル名の衝突警告、テーブル使用状況の抽出のためだけに呼び出す。
    for name in queries:
        select_cols = extract_select_columns(queries[name], schema)

        collisions = find_query_table_collisions(queries[name], queries, schema)
        error_log.extend({"クエリ": name, "種別": "クエリ名衝突警告", "メッセージ": w} for w in collisions)

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

    out_dir = OUTPUT_DIR / group_name
    write_group_output(out_dir, df_lineage, df_table_usage, chain_queries, analysis_log, error_log)

    print(f"[{group_name}] クエリ数={len(queries)}, 行数={len(df_lineage)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx, table_usage.xlsx, chain_queries.json, analysis.json, error.json")


def main() -> None:
    all_queries = load_queries(QUERY_DEPENDENCIES_FILE)
    schema = load_schema(TABLES_FILE)
    query_name_set = set(all_queries)

    start_queries = discover_start_queries()
    if not start_queries:
        print(f"エラー: input/ 直下に起点クエリ用のサブフォルダが見つかりません。")
        sys.exit(1)

    print(f"読み込んだ全クエリ数: {len(all_queries)}")
    print(f"起点クエリ数: {len(start_queries)}")
    for name in start_queries:
        print(f"  - {name}")
    print()

    for start_query in start_queries:
        process_group(start_query, all_queries, schema, query_name_set)


if __name__ == "__main__":
    main()
