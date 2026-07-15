"""クエリ呼び出し木をボトムアップに辿り、間接参照テーブルを積み上げる。

「クエリごとに直接触れる物理テーブル・直接呼び出す登録済みクエリ」の抽出自体は
Jet-SQLの構文解析が要る作業のため table_reference_extract.classify_queries() が
担う（PARAMETERS/TRANSFORM/PIVOT等のJet-SQL特有構文の前処理もそちらに集約）。
このモジュールはその結果（direct/children）を受け取り、「自分の直接参照 ∪
全子孫クエリの(直接∪間接)」から自分の直接分を引いた間接参照を求める、
グラフ上の伝播ロジックのみを担当する（sqlglotに一切依存しない）。
"""


def resolve_table_usage(
    direct: dict[str, set[str]], children: dict[str, set[str]], error_log: list[dict]
) -> dict[str, dict[str, set[str]]]:
    """クエリごとの {"direct": 直接参照テーブル集合, "indirect": 間接参照テーブル集合} を返す。

    子孫の再帰は結果をキャッシュしつつ、循環呼び出しを検出したらerror_logに警告を
    積んでその経路の探索だけ打ち切る（Accessクエリの通常のチェーンでは循環は
    起きない想定だが、入力データの誤りに備える）。
    """
    subtree_cache: dict[str, set[str]] = {}

    def subtree_tables(name: str, visiting: frozenset[str]) -> set[str]:
        if name in subtree_cache:
            return subtree_cache[name]
        if name in visiting:
            path = " -> ".join([*visiting, name])
            error_log.append(
                {
                    "クエリ": name,
                    "種別": "循環参照警告",
                    "メッセージ": f"クエリ呼び出しの循環を検出したため、この経路の探索を打ち切りました: {path}",
                }
            )
            return set()

        result = set(direct.get(name, set()))
        for child in children.get(name, set()):
            result |= subtree_tables(child, visiting | {name})
        subtree_cache[name] = result
        return result

    usage: dict[str, dict[str, set[str]]] = {}
    for name in direct:
        all_tables = subtree_tables(name, frozenset())
        usage[name] = {"direct": direct[name], "indirect": all_tables - direct[name]}

    return usage
