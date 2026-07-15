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


def build_matrix_long_entries(rows: list[dict], table_physical_names: dict[str, str]) -> list[dict]:
    """build_matrix_rows()の出力から、1行1テーブル参照の縦持ちレコード
    （{"path": パンくずのリスト, "テーブル名": 物理テーブル名, "マーク": "○"/"◎"}）を組み立てる。

    判定ロジック（direct/indirect）はここでは変更しない。列展開（開始クエリ・
    サブクエリN...列への分割）もここでは行わない。複数の起点クエリを横断して
    1つのCSVにまとめる際、列数はグループ横断の最大ネスト数に揃える必要があるため、
    呼び出し側（build_matrix_long_dataframe()）でまとめて行う。
    """
    entries: list[dict] = []
    for r in rows:
        for mark, tables in (("○", r["direct"]), ("◎", r["indirect"])):
            for t in sorted(tables):
                entries.append({"path": r["path"], "テーブル名": table_physical_names.get(t, t), "マーク": mark})
    return entries


def build_matrix_long_dataframe(entries: list[dict]) -> pd.DataFrame:
    """build_matrix_long_entries()の出力（複数の起点クエリ分をまとめたものでもよい）を
    縦持ちDataFrameに変換する。階層列は build_matrix_dataframe() と同様に
    開始クエリ・サブクエリ1, サブクエリ2, ... を、渡されたentries全体の最大ネスト数に
    応じて動的に追加する（複数起点クエリ分をまとめて1つのCSVに出力する際、
    列数をグループ間で揃えるため）。
    """
    max_depth = max((len(e["path"]) for e in entries), default=1)
    level_columns = ["開始クエリ"] + [f"サブクエリ{i}" for i in range(1, max_depth)]
    columns = [*level_columns, "テーブル名", "マーク"]

    out_rows = []
    for e in entries:
        row = {col: "" for col in level_columns}
        for i, name in enumerate(e["path"]):
            row[level_columns[i]] = name
        row["テーブル名"] = e["テーブル名"]
        row["マーク"] = e["マーク"]
        out_rows.append(row)

    return pd.DataFrame(out_rows, columns=columns)
