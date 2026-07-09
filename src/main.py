"""VBA がチェーン検出して AI 変換済みSQLを配置した
input/<起点クエリ>/converted_queries.json を解析し、起点クエリごとに
出力カラムがどのテーブル・カラムに由来するかをフラットテーブル形式で
output/<起点クエリ>/lineage.xlsx に出力するスクリプト。

処理の流れ：
1. input/table.json を読み込む
2. input/ 直下のサブフォルダを走査して起点クエリ（converted_queries.json が存在するフォルダ）を取得
3. 各起点クエリについて：
   - converted_queries.json を読み込む
   - sql_expand → lineage_extract → report で解析実行
4. output/<起点クエリ>/ に結果ファイルを出力
"""

import sys

from config import INPUT_DIR, OUTPUT_DIR, TABLES_FILE, discover_start_queries
from lineage_extract import extract_lineage_rows, extract_select_columns
from loader import load_queries, load_schema
from report import build_lineage_dataframe, write_group_output
from sql_expand import expand_query_ast, find_query_table_collisions


def process_group(start_query: str, schema: dict) -> None:
    """起点クエリに対してリネージ解析を実行し、output/<起点クエリ>/ に結果を出力する。"""
    group_name = start_query
    
    # converted_queries.json（AI変換済みSQL）を優先して使用
    converted_path = INPUT_DIR / start_query / "converted_queries.json"
    if not converted_path.exists():
        print(f"[{group_name}] エラー: converted_queries.json が見つかりません。スキップします。")
        return

    queries = load_queries(converted_path)
    if not queries:
        print(f"[{group_name}] 警告: converted_queries.json が空です。スキップします。")
        return
    print(f"[{group_name}] リネージ解析に使うSQL: converted_queries.json（AI変換済み）")

    analysis_log: dict[str, dict] = {}
    error_log: list[dict] = []
    all_rows = []

    for name, sql in queries.items():
        select_cols = extract_select_columns(sql, schema)

        collisions = find_query_table_collisions(sql, queries, schema)
        error_log.extend({"クエリ": name, "種別": "クエリ名衝突警告", "メッセージ": w} for w in collisions)

        try:
            ast_result = expand_query_ast(name, queries)
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": repr(ast_result).splitlines(),
                "expand_sql": ast_result.sql(dialect="tsql"),
            }
        except Exception as e:
            error_log.append({"クエリ": name, "種別": "expand_sql失敗", "メッセージ": str(e)})
            analysis_log[name] = {
                "extract_select_columns": select_cols,
                "expand_query_ast_repr": None,
                "expand_sql": None,
            }

        all_rows.extend(extract_lineage_rows(name, sql, schema, queries, error_log))

    df_lineage = build_lineage_dataframe(all_rows)

    out_dir = OUTPUT_DIR / group_name
    write_group_output(out_dir, df_lineage, analysis_log, error_log)

    print(f"[{group_name}] クエリ数={len(queries)}, 行数={len(df_lineage)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx, analysis.json, error.json")


def main() -> None:
    schema = load_schema(TABLES_FILE)

    start_queries = discover_start_queries()

    if not start_queries:
        print(f"エラー: {INPUT_DIR} 下に chain_queries.json が見つかりません。")
        print(f"VBAが出力した input/<起点クエリ名>/chain_queries.json を配置してください。")
        sys.exit(1)

    print(f"起点クエリ数: {len(start_queries)}")
    for name in start_queries:
        print(f"  - {name}")
    print()

    for start_query in start_queries:
        process_group(start_query, schema)


if __name__ == "__main__":
    main()
