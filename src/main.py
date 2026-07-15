"""VBA がチェーン検出しフル展開した input/<起点クエリ>/chain_queries.json
（Access SQL/Jet-SQL）を解析し、起点クエリごとに参照テーブルのマトリックス表
（1行1テーブル参照の縦持ち形式）を output/<起点クエリ>/lineage.xlsx に出力するスクリプト。
あわせて、全起点クエリ分の◎/○判定を1つにまとめた output/matrix_long.csv も出力する
（Excel数式で扱いやすい形式）。

処理の流れ：
1. input/table.json を読み込む
2. input/ 直下のサブフォルダを走査して起点クエリを取得
3. 各起点クエリについて：
   - chain_queries.json を読み込む
   - table_reference_extract.classify_queries() でクエリごとの直接参照テーブル・
     直接呼び出す子クエリを求める（Jet-SQLの構文解析はここに集約）
   - table_usage.resolve_table_usage() で間接参照テーブルをボトムアップに積み上げる
   - matrix.py でマトリックス表（縦持ち）を組み立てる
4. output/<起点クエリ>/ に結果ファイルを出力
5. 全起点クエリ分の縦持ちレコードをまとめて output/matrix_long.csv に出力
"""

import sys

from config import INPUT_DIR, OUTPUT_DIR, discover_start_queries
from loader import build_physical_table_name, load_queries, load_table_info
from matrix import build_matrix_long_dataframe, build_matrix_long_entries, build_matrix_rows
from report import write_group_output
from table_reference_extract import classify_queries
from table_usage import resolve_table_usage


def process_group(start_query: str, known_tables: set[str], table_physical_names: dict[str, str]) -> list[dict]:
    """起点クエリに対してテーブル参照を解析し、output/<起点クエリ>/ に結果を出力する。
    matrix_long.csv用に、このグループの縦持ちレコード（build_matrix_long_entries()の
    出力）を返す（スキップした場合は空リスト）。
    """
    chain_path = INPUT_DIR / start_query / "chain_queries.json"
    if not chain_path.exists():
        print(f"[{start_query}] エラー: chain_queries.json が見つかりません。スキップします。")
        return []

    queries = load_queries(chain_path)
    if not queries:
        print(f"[{start_query}] 警告: chain_queries.json が空です。スキップします。")
        return []

    error_log: list[dict] = []

    # クエリごとの直接参照テーブル・直接呼び出す子クエリを求める（Jet-SQLの構文解析は
    # table_reference_extract.classify_queries() に集約。パース失敗・未解決の参照名は
    # 目視確認できるよう error_log に記録しつつ、他のクエリの処理は止めない）。
    classified = classify_queries(queries, known_tables)
    direct: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for name, entry in classified.items():
        if entry["パース失敗"]:
            error_log.append(
                {
                    "クエリ": name,
                    "種別": "パース失敗",
                    "メッセージ": "Jet-SQLの前処理後もsqlglotでパースできませんでした。",
                }
            )
        if entry["未解決"]:
            error_log.append(
                {
                    "クエリ": name,
                    "種別": "未解決テーブル参照",
                    "メッセージ": f"テーブル・登録済みクエリのいずれにも一致しない参照: {', '.join(entry['未解決'])}",
                }
            )
        direct[name] = set(entry["参照テーブル"])
        children[name] = set(entry["参照サブクエリ"])

    usage = resolve_table_usage(direct, children, error_log)

    matrix_rows = build_matrix_rows(start_query, children, usage, error_log)
    long_entries = build_matrix_long_entries(matrix_rows, table_physical_names)
    df_matrix_long = build_matrix_long_dataframe(long_entries)

    out_dir = OUTPUT_DIR / start_query
    write_group_output(out_dir, df_matrix_long, error_log)

    print(f"[{start_query}] クエリ数={len(queries)}, エラー={len(error_log)} 件")
    print(f"  -> {out_dir}/lineage.xlsx（参照物理テーブル名マトリクス・縦持ち）, error.json")

    return long_entries


def main() -> None:
    table_info = load_table_info()
    known_tables = set(table_info.keys())
    table_physical_names = {name: build_physical_table_name(info) for name, info in table_info.items()}

    start_queries = discover_start_queries()

    if not start_queries:
        print(f"エラー: {INPUT_DIR} 下に起点クエリのフォルダが見つかりません。")
        print(f"VBA出力を {INPUT_DIR}/<起点クエリ名>/chain_queries.json に配置してください。")
        sys.exit(1)

    print(f"起点クエリ数: {len(start_queries)}")
    for name in start_queries:
        print(f"  - {name}")
    print()

    all_long_entries: list[dict] = []
    for start_query in start_queries:
        all_long_entries += process_group(start_query, known_tables, table_physical_names)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_long = build_matrix_long_dataframe(all_long_entries)
    long_path = OUTPUT_DIR / "matrix_long.csv"
    df_long.to_csv(long_path, index=False, encoding="utf-8-sig")
    print(f"\n{long_path}（全起点クエリの◎/○判定・縦持ち形式）に {len(df_long)} 行を出力しました。")


if __name__ == "__main__":
    main()
