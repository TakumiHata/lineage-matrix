"""chain_queries.json のAccess SQL（Jet-SQL）から直接、クエリが参照するテーブルの
一覧をテーブル単位で抽出するモジュール。lineage-matrix唯一の解析経路であり、
main.py（マトリックス表の構築）と table_reference_extract.main()（後述の
table_references.json単体出力）の両方がここを共通で使う。

マトリックス表に必要なのは「クエリ内での参照テーブル情報」（テーブル単位のみ、
カラム単位は不要）であり、テーブル参照の抽出だけであればsqlglotは関数の意味を
理解する必要がなく構文的にパースできればよいため、AI変換・SSMA変換を一切経由せず、
Jet-SQL特有構文だけを前処理で除去してtsql方言でパースする
（AI変換はボトルネックであり、SSMAはFormat関数・パラメータクエリ・
クロスタブクエリを変換できないため）。
"""

import json
import re

import sqlglot
import sqlglot.expressions as exp

from config import DIALECT, INPUT_DIR, OUTPUT_DIR, discover_start_queries
from loader import load_queries, load_table_info

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
    """SQL（Jet-SQL）が参照するテーブル・サブクエリを分類して返す。

    known_tables に一致すれば「参照テーブル」、known_queries に一致すれば
    「参照サブクエリ」（VBA側のチェーン検出で既に捕捉されている想定のため、
    参照テーブルには含めない）、どちらにも一致しなければ「未解決」とし、
    目視確認の対象として記録する。パースに失敗した場合は他のクエリの処理を
    止めないよう例外を捕捉し、「パース失敗」として記録する。
    """
    normalized = normalize_jet_sql(sql)
    try:
        parsed = sqlglot.parse_one(normalized, read=DIALECT)
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


def classify_queries(queries: dict[str, str], known_tables: set[str]) -> dict[str, dict]:
    """queries内の全クエリについて extract_referenced_tables() を実行し、
    {クエリ名: 分類結果} を返す。known_queries はこのグループ内の登録済み
    クエリ名一覧（queries自身のキー）を使う。main.py（マトリックス構築）と
    main()（table_references.json出力）の両方がこのヘルパーを共有する。
    """
    known_queries = set(queries.keys())
    return {name: extract_referenced_tables(sql, known_tables, known_queries) for name, sql in queries.items()}


def main() -> None:
    known_tables = set(load_table_info().keys())

    start_queries = discover_start_queries()
    results: list[dict] = []
    for start_query in start_queries:
        chain_path = INPUT_DIR / start_query / "chain_queries.json"
        if not chain_path.exists():
            print(f"[{start_query}] 警告: chain_queries.json が見つかりません。スキップします。")
            continue

        queries = load_queries(chain_path)
        classified = classify_queries(queries, known_tables)
        for name, entry in classified.items():
            results.append({"クエリ名": name, **entry})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "table_references.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"クエリ数={len(results)} -> {out_path}")


if __name__ == "__main__":
    main()
