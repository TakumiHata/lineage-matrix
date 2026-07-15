"""table_reference_extract.py のテスト。

AI変換前のchain_queries.json（Access/Jet-SQL）から直接、参照テーブル一覧を
テーブル単位で抽出する軽量パスの検証。converted_queries.jsonは一切使わない。
"""

import json

import table_reference_extract as tre


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# normalize_jet_sql: PARAMETERS宣言・TRANSFORM/PIVOTクロスタブ構文の除去
# ---------------------------------------------------------------------------


def test_normalize_removes_leading_parameters_declaration():
    sql = (
        "PARAMETERS [開始日] DateTime, [終了日] DateTime;\n"
        "SELECT * FROM T_受注明細 WHERE 受注日 BETWEEN [開始日] AND [終了日]"
    )
    result = tre.normalize_jet_sql(sql)
    assert "PARAMETERS" not in result
    assert result == "SELECT * FROM T_受注明細 WHERE 受注日 BETWEEN [開始日] AND [終了日]"


def test_normalize_parameters_is_case_insensitive():
    sql = "parameters [x] Long;\nSELECT * FROM T_受注明細"
    result = tre.normalize_jet_sql(sql)
    assert "parameters" not in result.lower()
    assert result == "SELECT * FROM T_受注明細"


def test_normalize_strips_transform_and_pivot_keeping_select_body():
    sql = (
        "TRANSFORM Sum(T_受注明細.金額) AS 合計 "
        "SELECT T_受注明細.顧客ID FROM T_受注明細 GROUP BY T_受注明細.顧客ID "
        "PIVOT T_受注明細.商品名;"
    )
    result = tre.normalize_jet_sql(sql)
    assert "TRANSFORM" not in result
    assert "PIVOT" not in result
    assert result == "SELECT T_受注明細.顧客ID FROM T_受注明細 GROUP BY T_受注明細.顧客ID"


def test_normalize_no_op_on_plain_select():
    sql = "SELECT [a] FROM [dbo].[T]"
    assert tre.normalize_jet_sql(sql) == sql


# ---------------------------------------------------------------------------
# extract_referenced_tables: テーブル/サブクエリ/未解決の分類
# ---------------------------------------------------------------------------


def test_extract_table_reference_survives_unknown_access_functions():
    """関数の意味を理解できなくても（IIf/Nz/DateAdd/Format/Likeいずれも）、
    構文的にパースできればテーブル参照抽出には影響しない。
    """
    known_tables = {"T_受注明細", "T_顧客"}
    known_queries: set[str] = set()

    cases = [
        ("SELECT IIf([数量]>10, \"多\", \"少\") AS 区分 FROM T_受注明細", "T_受注明細"),
        ("SELECT Nz([備考], \"\") AS 備考 FROM T_受注明細", "T_受注明細"),
        ("SELECT DateAdd(\"d\", 1, [受注日]) AS 翌日 FROM T_受注明細", "T_受注明細"),
        ("SELECT Format([受注日], \"yyyy/mm/dd\") AS 受注日文字列 FROM T_受注明細", "T_受注明細"),
        ("SELECT * FROM T_顧客 WHERE 顧客名 Like \"*田*\"", "T_顧客"),
    ]
    for sql, expected_table in cases:
        result = tre.extract_referenced_tables(sql, known_tables, known_queries)
        assert result["参照テーブル"] == [expected_table], sql
        assert result["参照サブクエリ"] == []
        assert result["未解決"] == []
        assert result["パース失敗"] is False


def test_extract_parameter_declaration_removed_and_table_resolved():
    known_tables = {"T_受注明細"}
    sql = "PARAMETERS [開始日] DateTime;\nSELECT * FROM T_受注明細 WHERE 受注日 >= [開始日]"
    result = tre.extract_referenced_tables(sql, known_tables, set())
    assert result["参照テーブル"] == ["T_受注明細"]
    assert result["パース失敗"] is False


def test_extract_crosstab_resolved_after_transform_pivot_stripped():
    known_tables = {"T_受注明細"}
    sql = (
        "TRANSFORM Sum(T_受注明細.金額) AS 合計 "
        "SELECT T_受注明細.顧客ID FROM T_受注明細 GROUP BY T_受注明細.顧客ID "
        "PIVOT T_受注明細.商品名;"
    )
    result = tre.extract_referenced_tables(sql, known_tables, set())
    assert result["参照テーブル"] == ["T_受注明細"]
    assert result["パース失敗"] is False


def test_extract_classifies_known_query_reference_as_subquery_not_table():
    known_tables = {"T_受注明細"}
    known_queries = {"Q_受注明細集計"}
    sql = "SELECT * FROM Q_受注明細集計"
    result = tre.extract_referenced_tables(sql, known_tables, known_queries)
    assert result["参照サブクエリ"] == ["Q_受注明細集計"]
    assert result["参照テーブル"] == []


def test_extract_unknown_name_classified_as_unresolved():
    sql = "SELECT * FROM T_存在しないテーブル"
    result = tre.extract_referenced_tables(sql, {"T_受注明細"}, {"Q_存在しないクエリ"})
    assert result["未解決"] == ["T_存在しないテーブル"]
    assert result["参照テーブル"] == []
    assert result["参照サブクエリ"] == []


def test_extract_parse_failure_recorded_without_raising():
    sql = "SELECT FROM WHERE ("  # 構文的に破綻
    result = tre.extract_referenced_tables(sql, {"T"}, set())
    assert result["パース失敗"] is True
    assert result["参照テーブル"] == []
    assert result["参照サブクエリ"] == []
    assert result["未解決"] == []


def test_extract_case_insensitive_matching_against_known_tables():
    result = tre.extract_referenced_tables("SELECT * FROM t_受注明細", {"T_受注明細"}, set())
    assert result["参照テーブル"] == ["T_受注明細"]


# ---------------------------------------------------------------------------
# 複雑クエリパターンA〜D（7/15の単機能・単一テーブルのダミー7クエリに対し、
# 本番相当のJOINチェーン・クロスタブ参照・特殊識別子・複合構文を検証する）
# ---------------------------------------------------------------------------


def test_pattern_a_multi_table_join_chain_all_tables_captured():
    """A: 括弧でネストしたJOINチェーンでも、参照される全テーブルが拾われる。"""
    known_tables = {"T_顧客", "T_受注明細", "T_商品"}
    sql = (
        "SELECT 受注ID, 顧客名, 商品名\n"
        "FROM (T_受注明細 INNER JOIN T_顧客 ON T_受注明細.顧客ID = T_顧客.顧客ID)\n"
        "INNER JOIN T_商品 ON T_受注明細.商品ID = T_商品.商品ID"
    )
    result = tre.extract_referenced_tables(sql, known_tables, set())
    assert result["参照テーブル"] == ["T_受注明細", "T_商品", "T_顧客"]
    assert result["未解決"] == []
    assert result["パース失敗"] is False


def test_pattern_b_crosstab_query_referenced_by_subscript_access():
    """B: クロスタブクエリを別クエリからFROM参照し、ピボット列を添字アクセスする構成。

    プロンプトに書かれていた元の添字構文 `Trim(Q_年度別売上クロス集計)[1])` は
    開き括弧のない `)` が残る構文エラーのタイプミス（sqlglotの制限ではなく、
    そもそもJet-SQLとしても不正な文字列）。Accessでクロスタブの列を式中で
    参照する際の実際の書き方に合わせ、`Trim([クエリ名].[列名])`
    （ドット記法＋列名をブラケットで囲む形）に補正して検証する。
    なお `!`（バン記法、例: `Q_年度別売上クロス集計![1]`）はAccess固有でtsqlには
    存在しないため、これはsqlglot(tsql)では正しくパース失敗する（想定通り）。
    """
    known_tables = {"T_受注明細"}
    known_queries = {"Q_年度別売上クロス集計"}

    # クロスタブ定義側：TRANSFORM/PIVOTが正しく除去されテーブル参照が解決される
    crosstab_sql = "TRANSFORM Sum(金額) AS 金額合計\nSELECT 顧客ID\nFROM T_受注明細\nGROUP BY 顧客ID\nPIVOT 年度"
    crosstab_result = tre.extract_referenced_tables(crosstab_sql, known_tables, known_queries)
    assert crosstab_result["参照テーブル"] == ["T_受注明細"]
    assert crosstab_result["パース失敗"] is False

    # 参照側：クロスタブクエリ名がFROM句にも式中にも現れるが、参照サブクエリとして
    # 1件のみ分類され、参照テーブル・未解決には混入しない
    ref_sql = "SELECT 顧客ID, Trim([Q_年度別売上クロス集計].[1]) AS 表示用金額 FROM Q_年度別売上クロス集計"
    ref_result = tre.extract_referenced_tables(ref_sql, known_tables, known_queries)
    assert ref_result["参照サブクエリ"] == ["Q_年度別売上クロス集計"]
    assert ref_result["参照テーブル"] == []
    assert ref_result["未解決"] == []
    assert ref_result["パース失敗"] is False


def test_pattern_b_access_bang_notation_is_not_valid_tsql_and_fails_to_parse():
    """B続き: Accessのバン記法（`!`）はtsql方言に存在しないため、sqlglotの制限
    ではなく方言差としてパース失敗になる。パース失敗は例外を伝播させず記録される。
    """
    sql = "SELECT 顧客ID, Trim(Q_年度別売上クロス集計![1]) AS 表示用金額 FROM Q_年度別売上クロス集計"
    result = tre.extract_referenced_tables(sql, set(), {"Q_年度別売上クロス集計"})
    assert result["パース失敗"] is True


def test_pattern_c_fullwidth_parentheses_identifier_not_split():
    """C: テーブル名自体に全角括弧を含む場合でも、ブラケット識別子として
    1つの名前のまま（括弧の前後で分断されずに）解決される。
    """
    known_tables = {"T_工事台帳基本（全庁）"}
    sql = "SELECT 工事ID, 工事名称 FROM [T_工事台帳基本（全庁）]"
    result = tre.extract_referenced_tables(sql, known_tables, set())
    assert result["参照テーブル"] == ["T_工事台帳基本（全庁）"]
    assert result["未解決"] == []
    assert result["パース失敗"] is False


def test_pattern_d_parameters_join_iif_format_combined():
    """D: PARAMETERS宣言・JOIN・IIf・Formatが同時に現れても、PARAMETERSの除去と
    テーブル参照抽出が両立する。
    """
    known_tables = {"T_受注明細", "T_顧客"}
    sql = (
        "PARAMETERS [対象年度] Text ( 255 );\n"
        "SELECT 受注ID,\n"
        '    IIf(金額 > 10000, "高額", "通常") AS 金額区分,\n'
        '    Format(受注日, "yyyy/mm/dd") AS 受注日表示\n'
        "FROM T_受注明細 INNER JOIN T_顧客 ON T_受注明細.顧客ID = T_顧客.顧客ID\n"
        "WHERE 年度 = [対象年度]"
    )
    result = tre.extract_referenced_tables(sql, known_tables, set())
    assert result["参照テーブル"] == ["T_受注明細", "T_顧客"]
    assert result["未解決"] == []
    assert result["パース失敗"] is False


def test_pattern_a_and_d_end_to_end_via_main(workdir):
    """A・Dを main() 経由（chain_queries.json→output/table_references.json）でも確認する。"""
    start = "クエリ複雑構造"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "chain_queries.json",
        [
            {
                "クエリ名": "Q_JOINチェーン",
                "SQL": (
                    "SELECT 受注ID, 顧客名, 商品名\n"
                    "FROM (T_受注明細 INNER JOIN T_顧客 ON T_受注明細.顧客ID = T_顧客.顧客ID)\n"
                    "INNER JOIN T_商品 ON T_受注明細.商品ID = T_商品.商品ID"
                ),
                "呼び出し元": [],
            },
            {
                "クエリ名": "Q_複合構文",
                "SQL": (
                    "PARAMETERS [対象年度] Text ( 255 );\n"
                    "SELECT 受注ID,\n"
                    '    IIf(金額 > 10000, "高額", "通常") AS 金額区分,\n'
                    '    Format(受注日, "yyyy/mm/dd") AS 受注日表示\n'
                    "FROM T_受注明細 INNER JOIN T_顧客 ON T_受注明細.顧客ID = T_顧客.顧客ID\n"
                    "WHERE 年度 = [対象年度]"
                ),
                "呼び出し元": [],
            },
        ],
    )
    write_json(
        workdir / "input" / "table.json",
        [
            {"テーブル名": "T_顧客", "カラム": [{"名前": "顧客ID", "型": "int"}]},
            {"テーブル名": "T_受注明細", "カラム": [{"名前": "受注ID", "型": "int"}]},
            {"テーブル名": "T_商品", "カラム": [{"名前": "商品ID", "型": "int"}]},
        ],
    )

    tre.main()

    out = json.loads((workdir / "output" / "table_references.json").read_text(encoding="utf-8"))
    by_name = {e["クエリ名"]: e for e in out}
    assert by_name["Q_JOINチェーン"]["参照テーブル"] == ["T_受注明細", "T_商品", "T_顧客"]
    assert by_name["Q_複合構文"]["参照テーブル"] == ["T_受注明細", "T_顧客"]


# ---------------------------------------------------------------------------
# main(): input/ 配下を走査し output/table_references.json を出力
# ---------------------------------------------------------------------------


def test_main_uses_chain_queries_json_even_when_converted_queries_json_present(workdir):
    """converted_queries.jsonの有無を問わず、必ずchain_queries.jsonのAccess SQLを使う。"""
    start = "クエリN"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "chain_queries.json",
        [{"クエリ名": "クエリN", "SQL": "SELECT * FROM T_受注明細", "呼び出し元": []}],
    )
    # converted_queries.json が存在しても無視され、chain_queries.jsonのSQLが使われる
    write_json(d / "converted_queries.json", [{"クエリ名": "クエリN", "SQL": "SELECT * FROM T_別テーブル"}])
    write_json(workdir / "input" / "table.json", [{"テーブル名": "T_受注明細", "カラム": [{"名前": "a", "型": "int"}]}])

    tre.main()

    out = json.loads((workdir / "output" / "table_references.json").read_text(encoding="utf-8"))
    entry = next(e for e in out if e["クエリ名"] == "クエリN")
    assert entry["参照テーブル"] == ["T_受注明細"]


def test_main_skips_folder_without_chain_queries_json(workdir, capsys):
    """converted_queries.jsonしか無いフォルダはchain_queries.jsonが無いためスキップされる。"""
    start = "クエリO"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "converted_queries.json", [{"クエリ名": "クエリO", "SQL": "SELECT * FROM T_受注明細"}])
    write_json(workdir / "input" / "table.json", [{"テーブル名": "T_受注明細", "カラム": [{"名前": "a", "型": "int"}]}])

    tre.main()

    captured = capsys.readouterr()
    assert "見つかりません" in captured.out
    out = json.loads((workdir / "output" / "table_references.json").read_text(encoding="utf-8"))
    assert all(e["クエリ名"] != "クエリO" for e in out)


def test_main_outputs_all_expected_fields_per_query(workdir):
    start = "クエリP"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "chain_queries.json",
        [
            {"クエリ名": "クエリ基本", "SQL": "SELECT * FROM T_受注明細", "呼び出し元": []},
            {"クエリ名": "クエリP", "SQL": "SELECT * FROM クエリ基本", "呼び出し元": ["クエリ基本"]},
        ],
    )
    write_json(workdir / "input" / "table.json", [{"テーブル名": "T_受注明細", "カラム": [{"名前": "a", "型": "int"}]}])

    tre.main()

    out = json.loads((workdir / "output" / "table_references.json").read_text(encoding="utf-8"))
    by_name = {e["クエリ名"]: e for e in out}
    assert set(by_name["クエリ基本"].keys()) == {"クエリ名", "参照テーブル", "参照サブクエリ", "未解決", "パース失敗"}
    assert by_name["クエリ基本"]["参照テーブル"] == ["T_受注明細"]
    assert by_name["クエリP"]["参照サブクエリ"] == ["クエリ基本"]
    assert by_name["クエリP"]["参照テーブル"] == []
