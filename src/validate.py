"""AI変換済みSQL（converted_queries.json）が構文的・スキーマ的に破綻していないかを、
本解析（main.py）とは別に事前チェックするスクリプト。

sqlglotの構文解析と qualify(validate_qualify_columns=True) を使い、以下を検知する。
- 構文エラー
- 複数ステートメントの混入（プロンプトの「単一SELECT文」ルール違反）
- tsql方言が認識できない関数呼び出し（未変換のAccess関数の疑い）
- スキーマ（table.json）に存在しないテーブル・カラム参照

DBに接続して実際に実行するわけではないため、型変換や権限等の実行時エラーまでは検知できない。
あくまで「sqlglotが文法・スキーマ整合性の面で破綻していないと判断できるか」の判定。
"""

import sys

import sqlglot
import sqlglot.expressions as exp
from sqlglot.errors import ErrorLevel, ParseError

from config import CHAIN_QUERIES_DIR, DIALECT, OUTPUT_DIR, TABLES_FILE, discover_start_queries
from loader import load_queries_for_query_dir, load_schema
from report import write_json
from sql_expand import expand_query_ast, qualify_expanded


def validate_query(query_name: str, sql: str, queries: dict[str, str], schema: dict) -> dict:
    """1クエリ分のSQLを検証し、{クエリ, 判定, 問題} を返す。"""
    problems: list[dict] = []

    try:
        stmts = sqlglot.parse(sql, dialect=DIALECT, error_level=ErrorLevel.RAISE)
    except ParseError as e:
        problems.append({"種別": "構文エラー", "メッセージ": str(e)})
        return {"クエリ": query_name, "判定": "NG", "問題": problems}

    if len(stmts) != 1:
        problems.append({
            "種別": "複数ステートメント",
            "メッセージ": f"SQL内に{len(stmts)}個のSELECT文が含まれています。単一のSELECT文である必要があります。",
        })

    parsed = stmts[0]
    anon_names = sorted({a.this for a in parsed.find_all(exp.Anonymous)})
    if anon_names:
        problems.append({
            "種別": "未知の関数",
            "メッセージ": f"tsql方言で認識できない関数呼び出し（未変換のAccess関数の疑い）: {', '.join(anon_names)}",
        })

    try:
        qualify_expanded(expand_query_ast(query_name, queries), schema)
    except Exception as e:
        problems.append({"種別": "スキーマ不整合", "メッセージ": str(e)})

    return {"クエリ": query_name, "判定": "NG" if problems else "OK", "問題": problems}


def validate_group(start_query: str, schema: dict) -> list[dict]:
    result = load_queries_for_query_dir(CHAIN_QUERIES_DIR / start_query)
    if result is None:
        return []
    queries, _ = result
    return [validate_query(name, sql, queries, schema) for name, sql in queries.items()]


def main() -> None:
    schema = load_schema(TABLES_FILE)
    start_queries = discover_start_queries()

    if not start_queries:
        print(f"エラー: {CHAIN_QUERIES_DIR} 下に起点クエリのフォルダが見つかりません。")
        sys.exit(1)

    total_ng = 0
    for start_query in start_queries:
        results = validate_group(start_query, schema)
        ng_count = sum(1 for r in results if r["判定"] == "NG")
        total_ng += ng_count

        out_dir = OUTPUT_DIR / start_query
        out_dir.mkdir(parents=True, exist_ok=True)
        write_json(out_dir / "validation.json", results)

        print(f"[{start_query}] クエリ数={len(results)}, NG={ng_count} 件 -> {out_dir}/validation.json")

    if total_ng:
        print(f"\n合計 {total_ng} 件のクエリでNG判定。詳細は各 output/<起点クエリ名>/validation.json を確認してください。")
        sys.exit(1)

    print("\n全クエリOK。")


if __name__ == "__main__":
    main()
