"""AI変換／SSMA変換を経由せず、chain_queries.json のAccess SQL（Jet-SQL）から
直接、クエリが参照するテーブルの一覧をテーブル単位で抽出する軽量パス。

マトリックス表に必要なのは「クエリ内での参照テーブル情報」（テーブル単位のみ、
カラム単位は不要）であり、テーブル参照の抽出だけであればsqlglotは関数の意味を
理解する必要がなく構文的にパースできればよいため、AI変換（ボトルネック）や
SSMA（Format関数・パラメータクエリ・クロスタブクエリを変換できない）を経由せず、
Jet-SQL特有構文だけを前処理で除去してtsql方言でパースする。

カラム単位の詳細なリネージ（lineage_extract.py が行う出力位置ベースの解決等）は
対象外。converted_queries.json は使わず、常に chain_queries.json を使う。
"""

import json
import re

import sqlglot
import sqlglot.expressions as exp

from config import INPUT_DIR, OUTPUT_DIR, TABLES_FILE, discover_start_queries
from loader import load_queries, load_schema

_PARAMETERS_RE = re.compile(r"^\s*PARAMETERS\b.*?;\s*", re.IGNORECASE | re.DOTALL)
_TRANSFORM_RE = re.compile(r"\bTRANSFORM\b\s+.*?\s+(?=SELECT\b)", re.IGNORECASE | re.DOTALL)
_PIVOT_RE = re.compile(r"\s*\bPIVOT\b.*?(;\s*)?\Z", re.IGNORECASE | re.DOTALL)


def normalize_jet_sql(sql: str) -> str:
    """sqlglotが構文的にパースできるよう、Jet-SQL特有構文を除去する。
    関数の意味解釈は不要なため、IIf/Nz/Format等はそのまま残してよい
    （関数呼び出しとして構文的にはパース可能なため）。

    - 先頭の PARAMETERS ... ; 宣言を除去する
    - TRANSFORM <式> SELECT ... PIVOT <式> のクロスタブ構文から
      TRANSFORM句とPIVOT句を除去し、中間のSELECT ... FROM ... GROUP BY ... のみ残す
    """
    sql = _PARAMETERS_RE.sub("", sql, count=1)
    sql = _TRANSFORM_RE.sub("", sql, count=1)
    sql = _PIVOT_RE.sub("", sql, count=1)
    return sql.strip()


def _find_ci(name: str, candidates) -> str | None:
    """candidates（イテラブル）の中から大文字小文字を無視してnameに一致する元の
    表記を返す。見つからなければNone。
    """
    for c in candidates:
        if c.lower() == name.lower():
            return c
    return None


def extract_referenced_tables(sql: str, known_tables: set[str], known_queries: set[str]) -> dict:
    """SQL（AI変換前のJet-SQL）が参照するテーブル・サブクエリを分類して返す。

    known_tables に一致すれば「参照テーブル」、known_queries に一致すれば
    「参照サブクエリ」（VBA側のチェーン検出で既に捕捉されている想定のため、
    参照テーブルには含めない）、どちらにも一致しなければ「未解決」とし、
    目視確認の対象として記録する。パースに失敗した場合は他のクエリの処理を
    止めないよう例外を捕捉し、「パース失敗」として記録する。
    """
    normalized = normalize_jet_sql(sql)
    try:
        parsed = sqlglot.parse_one(normalized, read="tsql")
    except Exception:
        return {"参照テーブル": [], "参照サブクエリ": [], "未解決": [], "パース失敗": True}

    tables: set[str] = set()
    subqueries: set[str] = set()
    unresolved: set[str] = set()
    for t in parsed.find_all(exp.Table):
        name = t.name
        matched_table = _find_ci(name, known_tables)
        if matched_table:
            tables.add(matched_table)
            continue
        matched_query = _find_ci(name, known_queries)
        if matched_query:
            subqueries.add(matched_query)
            continue
        unresolved.add(name)

    return {
        "参照テーブル": sorted(tables),
        "参照サブクエリ": sorted(subqueries),
        "未解決": sorted(unresolved),
        "パース失敗": False,
    }


def main() -> None:
    schema = load_schema(TABLES_FILE)
    known_tables = set(schema.keys())

    start_queries = discover_start_queries()
    results: list[dict] = []
    for start_query in start_queries:
        chain_path = INPUT_DIR / start_query / "chain_queries.json"
        if not chain_path.exists():
            print(f"[{start_query}] 警告: chain_queries.json が見つかりません。スキップします。")
            continue

        queries = load_queries(chain_path)
        known_queries = set(queries.keys())
        for name, sql in queries.items():
            entry = extract_referenced_tables(sql, known_tables, known_queries)
            results.append({"クエリ名": name, **entry})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "table_references.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"クエリ数={len(results)} -> {out_path}")


if __name__ == "__main__":
    main()
