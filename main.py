"""VBAが出力する input/*.json を解析し、クエリの出力カラムがどのテーブル・
カラムに由来するかをフラットテーブル形式で output/lineage.xlsx に出力するスクリプト。

カラムの由来テーブル解決は input/query_tables.json のスキーマ情報を
lineage() に渡すことで行う。
"""

import json
from pathlib import Path

import pandas as pd
import sqlglot
import sqlglot.expressions as exp
from sqlglot.lineage import lineage as sqlglot_lineage

QUERY_DEPENDENCIES_FILE = Path("input/query_dependencies.json")
QUERY_TABLES_FILE = Path("input/query_tables.json")
OUTPUT_DIR = Path("output")
OUTPUT_LINEAGE_XLSX_FILE = OUTPUT_DIR / "lineage.xlsx"
LINEAGE_COLUMNS = ["開始クエリ", "中間クエリ", "参照テーブル", "参照カラム", "最終出力カラム"]


def load_queries(path: Path = QUERY_DEPENDENCIES_FILE) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {item["クエリ名"]: item["SQL"] for item in data}


def load_schema(path: Path = QUERY_TABLES_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        item["テーブル名"]: {col["名前"]: col.get("型", "text") for col in item["カラム"]}
        for item in data
    }


def extract_select_columns(sql: str) -> list[str]:
    # parsed.selects は最も外側の SELECT の出力カラムのみを返す。
    # find_all(exp.Column) + 親でのフィルタでは、サブクエリ内部の
    # SELECT句のカラムまで拾ってしまい重複の原因になるため使わない。
    parsed = sqlglot.parse_one(sql, dialect="tsql")
    return [sel.alias_or_name for sel in parsed.selects]


def _restore_schema_casing(table_name: str | None, column_name: str | None, schema: dict) -> str | None:
    # tsqlは大文字小文字を区別しないため、schema付きlineage()解決後の
    # カラム名はASCII部分が小文字化されることがある（例: 工事ID → 工事id）。
    # schema.json の正しい表記に戻し、A5M2定義書とのExcel突合で
    # 一致させられるようにする。
    if not table_name or not column_name or table_name not in schema:
        return column_name
    for real_col in schema[table_name]:
        if real_col.lower() == column_name.lower():
            return real_col
    return column_name


def _make_row(query_name: str, intermediate: str, table_name: str, column_name: str, output_col: str) -> dict:
    return dict(zip(LINEAGE_COLUMNS, [query_name, intermediate, table_name, column_name, output_col]))


def _leaf_row(query_name: str, output_col: str, holder: exp.Expr, leaf: exp.Expr, path: list[str], schema: dict) -> dict:
    table_name = leaf.name if isinstance(leaf, exp.Table) else None

    # holder は SUM(...) のように集計関数がカラムを直接ラップすることがあるため、
    # holder.this が exp.Column である前提ではなく、式全体からカラム参照を探す。
    col_expr = holder.find(exp.Column)
    column_name = col_expr.name if col_expr is not None else None
    column_name = _restore_schema_casing(table_name, column_name, schema)

    return _make_row(
        query_name,
        " > ".join(path) if path else "（直接）",
        table_name or "不明",
        column_name or "不明",
        output_col,
    )


def _walk_leaves(node):
    """ルートから各リーフ（downstreamが空＝実テーブル参照）までの経路をたどり、
    (由来を持つノードの式, リーフの式, 通過したサブクエリエイリアスの経路) を列挙する。
    node.walk() は木全体を平坦に返すだけで経路情報を失うため使わない。
    """
    if not node.downstream:
        yield node.expression, node.expression, []
        return

    def _walk(n, path: list[str]):
        for d in n.downstream:
            if not d.downstream:
                yield n.expression, d.expression, path
            else:
                new_path = path + ([d.reference_node_name] if d.reference_node_name else [])
                yield from _walk(d, new_path)

    yield from _walk(node, [])


def extract_lineage_rows(query_name: str, sql: str, schema: dict) -> list[dict]:
    rows = []

    for output_col in extract_select_columns(sql):
        try:
            node = sqlglot_lineage(column=output_col, sql=sql, schema=schema, dialect="tsql")
        except Exception as e:
            rows.append(_make_row(query_name, "解析失敗", "不明", "不明", f"{output_col} ({e})"))
            continue

        for holder, leaf, path in _walk_leaves(node):
            rows.append(_leaf_row(query_name, output_col, holder, leaf, path, schema))

    return rows


def main() -> None:
    queries = load_queries()
    schema = load_schema()
    print(f"読み込んだクエリ数: {len(queries)}")
    for name in queries:
        print(f"  - {name}")

    all_rows = []
    for query_name, sql in queries.items():
        all_rows.extend(extract_lineage_rows(query_name, sql, schema))

    df_lineage = pd.DataFrame(all_rows, columns=LINEAGE_COLUMNS)
    print()
    print("リネージ・フラットテーブル:")
    print(df_lineage.to_string())

    OUTPUT_DIR.mkdir(exist_ok=True)
    df_lineage.to_excel(OUTPUT_LINEAGE_XLSX_FILE, index=False)
    print()
    print(f"{OUTPUT_LINEAGE_XLSX_FILE} に出力しました")


if __name__ == "__main__":
    main()
