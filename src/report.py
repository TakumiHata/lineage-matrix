"""リネージ行をDataFrame化し、グループごとの出力ファイルを書き出す。"""

import json
from pathlib import Path

import pandas as pd


def build_lineage_dataframe(analysis_log: dict[str, dict]) -> pd.DataFrame:
    """analysis_log（analysis.json とまったく同じ内容の解析結果）の各クエリエントリが
    持つ "lineage" 行を連結し、「参照クエリパス」（外側→内側のクエリ名リスト）を、
    そのグループ内の最大ネスト数に合わせて 参照クエリ1, 参照クエリ2, ... の固定列に展開する。
    経路が空（直接参照）の行は 参照クエリ1 に「（直接）」を入れ、残りは空欄にする。
    """
    rows = [row for entry in analysis_log.values() for row in entry["lineage"]]

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


def write_json(path: Path, data) -> None:
    """UTF-8・非ASCIIそのまま・インデント2・末尾改行付きでJSONを書き出す
    （analysis.json/error.json/validation.json で共通の出力フォーマット）。
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_group_output(
    out_dir: Path,
    df_matrix: pd.DataFrame,
    df_lineage: pd.DataFrame,
    analysis_log: dict,
    error_log: list[dict],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # マトリックスとカラム単位の詳細行は同じ analysis_log から生成される2つのビューの
    # ため、別ファイルにすると再生成のタイミングでズレうる。1つのブック内の別シートに
    # まとめ、常に同時生成・同時配布されるようにする。
    with pd.ExcelWriter(out_dir / "lineage.xlsx", engine="openpyxl") as writer:
        df_matrix.to_excel(writer, sheet_name="テーブル参照マトリクス", index=False)
        df_lineage.to_excel(writer, sheet_name="カラム単位リネージ", index=False)
        # マトリクスの○/◎はPython側で確定させた値であり、Excel数式では再現しない
        # （分析ロジックが2箇所に分かれてズレるのを避けるため）。その代わり、
        # 詳細シートにオートフィルタを付け、開始クエリ・参照テーブルで絞り込んで
        # ○/◎の根拠を人手で検証できるようにする。
        detail_sheet = writer.sheets["カラム単位リネージ"]
        detail_sheet.auto_filter.ref = detail_sheet.dimensions

    write_json(out_dir / "analysis.json", analysis_log)
    write_json(out_dir / "error.json", error_log)
