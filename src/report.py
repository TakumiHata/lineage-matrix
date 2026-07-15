"""マトリックスDataFrameをExcel/JSONに書き出す。"""

import json
from pathlib import Path

import pandas as pd


def write_json(path: Path, data) -> None:
    """UTF-8・非ASCIIそのまま・インデント2・末尾改行付きでJSONを書き出す
    （error.json で使う共通の出力フォーマット）。
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_group_output(
    out_dir: Path,
    df_matrix_long: pd.DataFrame,
    error_log: list[dict],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1行1テーブル参照の縦持ち形式（output/matrix_long.csv の全起点クエリ横断版に対し、
    # こちらはこのグループのみ）。XLOOKUP等のExcel数式で扱いやすいよう、テーブル名と
    # マークをカンマ区切りで1セルに詰め込む横持ち形式は持たない。
    with pd.ExcelWriter(out_dir / "lineage.xlsx", engine="openpyxl") as writer:
        df_matrix_long.to_excel(writer, sheet_name="テーブル参照マトリクス", index=False)

    write_json(out_dir / "error.json", error_log)
