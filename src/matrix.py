"""クエリ呼び出し木を辿り、クエリ階層×参照物理テーブル名のマトリックスを組み立てる。"""

import pandas as pd


def build_matrix_rows(
    start_query: str,
    children: dict[str, set[str]],
    usage: dict[str, dict[str, set[str]]],
    error_log: list[dict],
) -> list[dict]:
    """start_queryを根に children をDFSし、(パンくずpath, direct, indirect) の行を作る。
    同じクエリが複数の親から呼ばれる場合は呼び出し元ごとに別行にする
    （table_usage.resolve_table_usage()が返す direct/indirect はクエリ固有で
    呼び出し元に依存しないため、複数行になっても内容は同じだが、行の位置＝
    どの祖先チェーンの上位行にも◎が見えるようにするために必要）。

    resolve_table_usage() 側で検出済みの循環はキャッシュ経由でindirectに反映
    されないだけで children 自体には残りうるため、ここでも独立に循環を検出して
    打ち切る（行の無限展開を防ぐため）。
    """
    rows: list[dict] = []

    def walk(name: str, path: list[str], visiting: frozenset[str]) -> None:
        if name in visiting:
            error_log.append(
                {
                    "クエリ": name,
                    "種別": "循環参照警告",
                    "メッセージ": f"マトリックス行の展開中に循環を検出したため打ち切りました: {' -> '.join([*path, name])}",
                }
            )
            return
        current_path = [*path, name]
        rows.append(
            {
                "path": current_path,
                "direct": usage.get(name, {}).get("direct", set()),
                "indirect": usage.get(name, {}).get("indirect", set()),
            }
        )
        for child in sorted(children.get(name, set())):
            walk(child, current_path, visiting | {name})

    walk(start_query, [], frozenset())
    return rows


def build_matrix_dataframe(rows: list[dict], table_physical_names: dict[str, str]) -> pd.DataFrame:
    """行リストをワイドDataFrameに変換する。階層列は「開始クエリ」に加え、
    そのグループ内の最大ネスト数に応じて サブクエリ1, サブクエリ2, ... を動的に
    追加する（report.build_lineage_dataframeの参照クエリ1,2,...と同じ考え方）。
    行はルートからそのクエリまでのパンくず（外側→内側）を、path の深さに応じた
    列まで埋める。

    テーブルを全列挙する列群の代わりに「参照物理テーブル名」列1つにまとめる。
    table_physical_names（loader.build_physical_table_name()で組み立てた
    テーブル名→物理テーブル名の対応表）を使い、直接参照は「物理名(○)」、
    間接参照は「物理名(◎)」の形式にしてカンマ区切りで連結する
    （物理テーブル名が対応表に無い場合はテーブル名をそのまま使う）。
    """
    max_depth = max((len(r["path"]) for r in rows), default=1)
    level_columns = ["開始クエリ"] + [f"サブクエリ{i}" for i in range(1, max_depth)]
    columns = [*level_columns, "参照物理テーブル名"]

    out_rows = []
    for r in rows:
        row = {col: "" for col in level_columns}
        for i, name in enumerate(r["path"]):
            row[level_columns[i]] = name

        direct_names = sorted(table_physical_names.get(t, t) for t in r["direct"])
        indirect_names = sorted(table_physical_names.get(t, t) for t in r["indirect"])
        parts = [f"{name}(○)" for name in direct_names] + [f"{name}(◎)" for name in indirect_names]
        row["参照物理テーブル名"] = ", ".join(parts)

        out_rows.append(row)

    return pd.DataFrame(out_rows, columns=columns)
