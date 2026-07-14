"""AI変換前（生のAccessクエリ）を対象に、年度違いなど内容がほぼ同一のクエリを
グルーピングし、AI変換にかける母数を減らすための下ごしらえを行うスクリプト。

Jet SQLはsqlglotが対応していない方言のため、この段階ではAST解析を使わず、
正規表現ベースの文字列処理のみでグルーピングする。

処理の流れ：
1. 各クエリのSQL本文を最初のWHERE（大文字小文字無視）で分割し、WHERE前を
   空白正規化した上で完全一致比較してグルーピングする（粗いグルーピング）。
   全件同士の総当たり比較（1546×1546）を避けるための一次フィルタ。
2. 同一グループ内でのみ、WHERE以降をdifflibで比較し、類似度が閾値を下回る
   ペアがあれば「年度違いではなく別物の可能性あり」として目視確認用にフラグを立てる。
"""

import difflib
import re

import pandas as pd

from config import QUERIES_FILE, QUERY_GROUPS_FILE
from loader import load_raw_queries

WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")

SIMILARITY_THRESHOLD = 0.8


def normalize_sql(sql: str) -> tuple[str, str]:
    """SQL本文を最初のWHERE（大文字小文字無視）で分割し、
    (WHERE前・正規化済み, WHERE以降・原文) を返す。
    WHEREが存在しない場合、WHERE以降は空文字とする。
    正規化は連続する空白・改行・タブを半角スペース1個に置換し、前後の空白を除去する。
    """
    m = WHERE_RE.search(sql)
    before = sql if m is None else sql[: m.start()]
    after = "" if m is None else sql[m.start() :]
    return WHITESPACE_RE.sub(" ", before).strip(), after


def group_queries(queries: list[dict]) -> pd.DataFrame:
    """query.json形式のクエリ一覧（[{"クエリ名":..., "SQL":...}, ...]）を、
    normalize_sql()のWHERE前が完全一致するクエリ同士でグルーピングし、
    グループIDを付与したDataFrameを返す。
    """
    rows = []
    for q in queries:
        before, after = normalize_sql(q["SQL"])
        rows.append({"クエリ名": q["クエリ名"], "SQL": q["SQL"], "WHERE前": before, "WHERE以降": after})

    df = pd.DataFrame(rows, columns=["クエリ名", "SQL", "WHERE前", "WHERE以降"])
    # WHERE前が完全一致するクエリを同じグループに束ねる。出現順を保つためsort=False。
    df["グループID"] = df.groupby("WHERE前", sort=False).ngroup() + 1
    return df


def check_similarity(group: pd.DataFrame, threshold: float = SIMILARITY_THRESHOLD) -> list[dict]:
    """同一グループ内の「WHERE以降」をdifflib.SequenceMatcherで総当たり比較し、
    類似度がthreshold未満のペアを検出して返す。
    """
    flagged = []
    rows = group[["クエリ名", "WHERE以降"]].to_dict("records")
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            ratio = difflib.SequenceMatcher(None, a["WHERE以降"], b["WHERE以降"]).ratio()
            if ratio < threshold:
                flagged.append({"クエリ名1": a["クエリ名"], "クエリ名2": b["クエリ名"], "類似度": round(ratio, 3)})
    return flagged


def main() -> None:
    queries = load_raw_queries(QUERIES_FILE)
    df = group_queries(queries)

    flagged_names: set[str] = set()
    for _, group in df.groupby("グループID", sort=False):
        for pair in check_similarity(group):
            flagged_names.add(pair["クエリ名1"])
            flagged_names.add(pair["クエリ名2"])
    df["要確認フラグ"] = df["クエリ名"].isin(flagged_names)

    QUERY_GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    df = df[["グループID", "クエリ名", "SQL", "WHERE前", "WHERE以降", "要確認フラグ"]]
    df.to_excel(QUERY_GROUPS_FILE, sheet_name="クエリグルーピング", index=False)

    print(f"クエリ数={len(df)}, グループ数={df['グループID'].nunique()}, 要確認={df['要確認フラグ'].sum()} 件")
    print(f"-> {QUERY_GROUPS_FILE}")


if __name__ == "__main__":
    main()
