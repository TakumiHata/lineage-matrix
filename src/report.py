"""リネージ行をDataFrame化し、グループごとの出力ファイルを書き出す。"""

import json
from pathlib import Path

import pandas as pd


def build_lineage_dataframe(rows: list[dict]) -> pd.DataFrame:
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


def build_table_usage_dataframe(rows: list[dict]) -> pd.DataFrame:
    """クエリごとに実際に参照している物理テーブルの一覧（開始クエリ, 参照テーブル）。

    lineage.xlsx はSELECT出力カラムの由来だけを追うため、WHERE句やJOIN条件
    だけで使われてSELECT結果には現れないテーブルが漏れる。このテーブルは
    それを補い、そのクエリが触れている全テーブルを漏れなく記録する。
    """
    return pd.DataFrame(rows, columns=["開始クエリ", "参照テーブル"])


def build_query_graph_mermaid(query_names: list[str], edges: list[tuple[str, str]]) -> str:
    """クエリ間の依存関係（どのクエリがどのクエリをFROM句で参照しているか）を
    Mermaidのflowchart記法で表す。sqlglot自体にMermaid出力機能はないため、
    収集済みのエッジ情報をテキストとして組み立てるだけの単純な整形処理。

    日本語のクエリ名をそのままMermaidのノードIDにすると記法上のトラブルの
    可能性があるため、q0, q1, ... という安全なIDを振り、表示名は
    `["表示名"]` のラベルとして持たせる。依存先を持たない（孤立した）
    クエリも見落とさないよう、全クエリを先にノードとして明示的に宣言する。
    """
    node_id = {name: f"q{i}" for i, name in enumerate(query_names)}

    lines = ["graph LR"]
    for name in query_names:
        lines.append(f'    {node_id[name]}["{name}"]')
    for src, dst in edges:
        lines.append(f"    {node_id[src]} --> {node_id[dst]}")

    return "\n".join(lines) + "\n"


def write_group_output(
    out_dir: Path,
    df_lineage: pd.DataFrame,
    df_table_usage: pd.DataFrame,
    query_graph_mermaid: str,
    analysis_log: dict,
    error_log: list[dict],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    df_lineage.to_excel(out_dir / "lineage.xlsx", index=False)
    df_table_usage.to_excel(out_dir / "table_usage.xlsx", index=False)
    (out_dir / "query_graph.mmd").write_text(query_graph_mermaid, encoding="utf-8")
    with open(out_dir / "analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis_log, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(out_dir / "error.json", "w", encoding="utf-8") as f:
        json.dump(error_log, f, ensure_ascii=False, indent=2)
        f.write("\n")
