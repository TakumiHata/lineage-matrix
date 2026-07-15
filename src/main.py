"""VBA がチェーン検出しフル展開した chain_queries/<起点クエリ>/chain_queries.json
（AI変換済みの場合は converted_queries.json）を解析し、起点クエリごとに
出力カラムがどのテーブル・カラムに由来するかをフラットテーブル形式で
output/<起点クエリ>/lineage.xlsx に出力するスクリプト。

処理の流れ：
1. table.json を読み込む
2. chain_queries/ 直下のサブフォルダを走査して起点クエリを取得
3. 各起点クエリについて：
   - converted_queries.json があればそちらを優先、なければ chain_queries.json を読み込む
   - sql_expand → lineage_extract → report で解析実行
4. output/<起点クエリ>/ に結果ファイルを出力
"""

import sys

from config import CHAIN_QUERIES_DIR, OUTPUT_DIR, TABLES_FILE, discover_start_queries
from lineage_extract import analyze_query
from loader import build_physical_table_name, load_queries_for_query_dir, load_schema, load_table_info
from matrix import build_matrix_dataframe, build_matrix_rows
from report import build_lineage_dataframe, write_group_output
from table_usage import resolve_table_usage


def process_group(start_query: str, schema: dict, table_physical_names: dict[str, str]) -> None:
    """起点クエリに対してリネージ解析を実行し、output/<起点クエリ>/ に結果を出力する。"""
    query_dir = CHAIN_QUERIES_DIR / start_query
    result = load_queries_for_query_dir(query_dir)
    if result is None:
        print(f"[{start_query}] エラー: chain_queries.json/converted_queries.json が見つかりません。スキップします。")
        return

    queries, used_path = result
    if not queries:
        print(f"[{start_query}] 警告: {used_path.name} が空です。スキップします。")
        return
    print(f"[{start_query}] リネージ解析に使うSQL: {used_path.name}")

    error_log: list[dict] = []

    # テーブル参照マトリクス用：SELECT出力カラムに寄与しない、JOIN/WHERE専用の
    # テーブル参照も拾うため、analyze_query()のlineageとは別経路で直接/間接の
    # テーブル参照を求める（table_usage.py 参照。クエリ名衝突警告もここで検出する）。
    usage, children = resolve_table_usage(queries, schema, error_log)

    # analyze_query() が expand_query_ast + qualify を1回だけ実行し、analysis.json用の
    # ログとlineage行を同じ結果から作る。lineage.xlsx は各クエリの "lineage" を
    # build_lineage_dataframe() で連結して組み立てる（analysis_logが唯一の解析結果）。
    analysis_log = {name: analyze_query(name, sql, schema, queries, error_log) for name, sql in queries.items()}
    for name, u in usage.items():
        analysis_log[name]["参照テーブル_直接"] = sorted(u["direct"])
        analysis_log[name]["参照テーブル_間接"] = sorted(u["indirect"])

    matrix_rows = build_matrix_rows(start_query, children, usage, error_log)
    df_matrix = build_matrix_dataframe(matrix_rows, table_physical_names)

    df_lineage = build_lineage_dataframe(analysis_log)

    out_dir = OUTPUT_DIR / start_query
    write_group_output(out_dir, df_matrix, df_lineage, analysis_log, error_log)

    print(f"[{start_query}] クエリ数={len(queries)}, 行数={len(df_lineage)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx（参照物理テーブル名マトリクス・カラム単位リネージ）, analysis.json, error.json")


def main() -> None:
    schema = load_schema(TABLES_FILE)
    table_info = load_table_info(TABLES_FILE)
    table_physical_names = {name: build_physical_table_name(info) for name, info in table_info.items()}

    start_queries = discover_start_queries()

    if not start_queries:
        print(f"エラー: {CHAIN_QUERIES_DIR} 下に起点クエリのフォルダが見つかりません。")
        print(f"VBA出力を {CHAIN_QUERIES_DIR}/<起点クエリ名>/chain_queries.json に配置してください。")
        sys.exit(1)

    print(f"起点クエリ数: {len(start_queries)}")
    for name in start_queries:
        print(f"  - {name}")
    print()

    for start_query in start_queries:
        process_group(start_query, schema, table_physical_names)


if __name__ == "__main__":
    main()
