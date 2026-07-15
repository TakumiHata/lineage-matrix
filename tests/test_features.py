"""機能要件網羅テスト（テスト観点A〜H、README/背景の記述に基づく）。

各テスト関数のdocstring先頭に観点番号（例: A1, C14）を付与している。
グルーピング機能（旧・観点E）は方針転換により撤去されたため、対応するテストは存在しない。
G（代表クエリ・重複チェーン除外）は現在のsrc/に対応する実装が
一切存在しない（representative_queries.json/skipped_queries.json関連の
コードもgit履歴も見つからない）ため、テストを書かず本ファイル末尾のコメントで
その旨を記録するに留める。

lineage-matrixは「クエリ内の参照テーブル収集ツール」に特化する方針転換により、
カラム単位のリネージ解析（旧・観点C）とAI変換済みSQLの事前検証（旧・観点B、
validate.py）は撤去された。それに伴い、観点B・Cは
「テーブル参照抽出とグラフ伝播」（table_reference_extract.py / table_usage.py）に
割り当て直している。
"""

import json

import config
import loader
import main
import matrix
import table_reference_extract
import table_usage


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_usage_from_queries(
    queries: dict[str, str], known_tables: set[str], error_log: list[dict] | None = None
) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[str]]]:
    """テストヘルパー：main.process_group()と同じ経路（classify_queries() →
    resolve_table_usage()）でSQL文字列からusage/childrenを求める。
    """
    if error_log is None:
        error_log = []
    classified = table_reference_extract.classify_queries(queries, known_tables)
    direct = {name: set(e["参照テーブル"]) for name, e in classified.items()}
    children = {name: set(e["参照サブクエリ"]) for name, e in classified.items()}
    usage = table_usage.resolve_table_usage(direct, children, error_log)
    return usage, children


# ---------------------------------------------------------------------------
# A. 入力読み込み（loader.py / config.py）
# ---------------------------------------------------------------------------


def test_a2_chain_queries_format_is_loadable(tmp_path):
    """A2: chain_queries.json形式（呼び出し元付き）のクエリ名・SQLパース。"""
    data = [
        {"クエリ名": "クエリ工事基本", "SQL": "SELECT [a] FROM [dbo].[T]", "呼び出し元": []},
        {"クエリ名": "クエリ工事集計", "SQL": "SELECT [a] FROM クエリ工事基本", "呼び出し元": ["クエリ工事基本"]},
    ]
    path = tmp_path / "chain_queries.json"
    write_json(path, data)

    queries = loader.load_queries(path)

    assert queries == {
        "クエリ工事基本": "SELECT [a] FROM [dbo].[T]",
        "クエリ工事集計": "SELECT [a] FROM クエリ工事基本",
    }


def test_a3_chain_queries_used_for_processing(workdir):
    """A3: chain_queries.jsonが解析に使われる。"""
    start = "クエリK"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "chain_queries.json", [{"クエリ名": "クエリK", "SQL": "SELECT [a] FROM T", "呼び出し元": []}])

    main.process_group(start, {"T"}, {})

    df = matrix_long_from_output(workdir, start)
    assert df.iloc[0]["テーブル名"] == "T"


def test_a4_skip_when_chain_queries_missing(workdir, capsys):
    """A4: chain_queries.jsonが存在しない場合はエラーメッセージを出して当該フォルダをスキップする。"""
    start = "クエリZ"
    (workdir / "input" / start).mkdir(parents=True)

    main.process_group(start, {"T"}, {})

    captured = capsys.readouterr()
    assert "見つかりません" in captured.out
    assert not (workdir / "output" / start).exists()


def test_a5_bom_and_no_bom_utf8(tmp_path):
    """A5: utf-8-sig（BOM付き）・BOMなしUTF-8の両方が読み込めること。"""
    data = [{"クエリ名": "Q", "SQL": "SELECT 1 AS a"}]
    raw = json.dumps(data, ensure_ascii=False)

    p_bom = tmp_path / "bom.json"
    p_bom.write_bytes(b"\xef\xbb\xbf" + raw.encode("utf-8"))
    p_nobom = tmp_path / "nobom.json"
    p_nobom.write_bytes(raw.encode("utf-8"))

    assert loader.load_queries(p_bom) == {"Q": "SELECT 1 AS a"}
    assert loader.load_queries(p_nobom) == {"Q": "SELECT 1 AS a"}


def test_a6_discover_start_queries_requires_chain_queries_json(workdir):
    """A6: 起点クエリ検出。input/ 直下のサブフォルダのうち、chain_queries.jsonが
    存在するものだけが対象になる（converted_queries.json関連の優先読み込みは撤去済み）。
    """
    (workdir / "input" / "クエリA").mkdir(parents=True)
    write_json(workdir / "input" / "クエリA" / "chain_queries.json", [])

    (workdir / "input" / "空フォルダ").mkdir(parents=True)

    # input/table.json はサブフォルダの兄弟に同居するファイルであり、フォルダではないため対象外
    (workdir / "input" / "table.json").write_text("[]", encoding="utf-8")

    result = config.discover_start_queries()

    assert result == ["クエリA"]


# ---------------------------------------------------------------------------
# B. テーブル参照抽出とグラフ伝播
# （table_reference_extract.classify_queries / table_usage.resolve_table_usage）
#
# extract_referenced_tables()・normalize_jet_sql() 自体の詳細な観点は
# tests/test_table_reference_extract.py でカバーする。ここでは classify_queries()
# の出力が table_usage.resolve_table_usage() の入力として正しく機能することを確認する。
# ---------------------------------------------------------------------------


def test_b_classify_queries_output_feeds_resolve_table_usage_directly():
    """B: classify_queries()の「参照テーブル」「参照サブクエリ」が、そのまま
    resolve_table_usage()のdirect/children入力として使え、間接参照が正しく積み上がる。
    """
    known_tables = {"T"}
    queries = {
        "Base": "SELECT [a] FROM T",
        "Mid": "SELECT [a] FROM Base",
        "Main": "SELECT [a] FROM Mid",
    }
    usage, children = resolve_usage_from_queries(queries, known_tables)

    assert usage["Base"] == {"direct": {"T"}, "indirect": set()}
    assert usage["Mid"] == {"direct": set(), "indirect": {"T"}}
    assert usage["Main"] == {"direct": set(), "indirect": {"T"}}
    assert children == {"Base": set(), "Mid": {"Base"}, "Main": {"Mid"}}


def test_b_resolve_table_usage_pure_graph_propagation_no_sql_parsing():
    """B: resolve_table_usage()は事前に計算されたdirect/childrenのみを使う
    純粋なグラフ伝播であり、SQL文字列やスキーマを一切必要としない。
    """
    direct = {"Base": {"T"}, "Mid": set(), "Main": set()}
    children = {"Base": set(), "Mid": {"Base"}, "Main": {"Mid"}}

    usage = table_usage.resolve_table_usage(direct, children, [])

    assert usage["Base"] == {"direct": {"T"}, "indirect": set()}
    assert usage["Mid"] == {"direct": set(), "indirect": {"T"}}
    assert usage["Main"] == {"direct": set(), "indirect": {"T"}}


def test_b_circular_reference_detected_and_logged():
    """B: クエリ呼び出しグラフに循環がある場合、無限再帰にならず error_log に
    「循環参照警告」を記録したうえで、その経路の探索だけを打ち切る。
    """
    direct = {"A": set(), "B": set()}
    children = {"A": {"B"}, "B": {"A"}}
    error_log: list[dict] = []

    usage = table_usage.resolve_table_usage(direct, children, error_log)

    assert any(e["種別"] == "循環参照警告" for e in error_log)
    assert usage["A"]["indirect"] == set()
    assert usage["B"]["indirect"] == set()


# ---------------------------------------------------------------------------
# D. エラーログ（output/<起点クエリ名>/error.json）
# ---------------------------------------------------------------------------


def test_d_error_json_records_unresolved_table_reference(workdir):
    """D: テーブル・登録済みクエリのいずれにも一致しない参照がある場合、
    error.jsonに「未解決テーブル参照」として記録される（他のクエリの処理は継続する）。
    """
    start = "クエリL"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "chain_queries.json", [{"クエリ名": "クエリL", "SQL": "SELECT * FROM 存在しないテーブル", "呼び出し元": []}])

    main.process_group(start, {"T"}, {})

    error_log = json.loads((workdir / "output" / start / "error.json").read_text(encoding="utf-8"))
    assert any(e["種別"] == "未解決テーブル参照" and e["クエリ"] == "クエリL" for e in error_log)


def test_d_error_json_records_parse_failure(workdir):
    """D: 前処理後もsqlglotでパースできないSQLがある場合、error.jsonに
    「パース失敗」として記録され、処理全体は継続する。
    """
    start = "クエリN"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "chain_queries.json", [{"クエリ名": "クエリN", "SQL": "SELECT FROM WHERE (", "呼び出し元": []}])

    main.process_group(start, {"T"}, {})

    error_log = json.loads((workdir / "output" / start / "error.json").read_text(encoding="utf-8"))
    assert any(e["種別"] == "パース失敗" and e["クエリ"] == "クエリN" for e in error_log)


def test_d_error_json_empty_when_no_problems(workdir):
    """D: 問題がゼロのケースで、error.jsonは空配列になる。"""
    start = "クエリM"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "chain_queries.json",
        [
            {"クエリ名": "クエリ基本", "SQL": "SELECT [a] FROM T", "呼び出し元": []},
            {"クエリ名": "クエリM", "SQL": "SELECT [a] FROM クエリ基本", "呼び出し元": ["クエリ基本"]},
        ],
    )

    main.process_group(start, {"T"}, {})

    error_log = json.loads((workdir / "output" / start / "error.json").read_text(encoding="utf-8"))
    assert error_log == []


# ---------------------------------------------------------------------------
# F. マトリックス表
#
# 横持ち形式（「参照物理テーブル名」列にテーブル名とマークをカンマ区切りで
# 詰め込む形式、旧・matrix.build_matrix_dataframe()）は撤去され、縦持ち形式
# （1行1テーブル参照）に一本化された。対応するテスト観点は I セクションに
# 統合している。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# G. 代表クエリ・重複チェーン除外
# ---------------------------------------------------------------------------
#
# representative_queries.json / skipped_queries.json / チェーンの重複除外に
# relatedするコードは src/ 配下のどのモジュールにも存在せず、git log --all -S
# でも該当する実装がコミットされた形跡が見つからない。むしろ方針転換により
# 「全クエリを個別にマトリックス表へ反映する」（開始クエリの事前絞り込みをしない）
# ことが明示的に決定されたため、実装対象が存在しない。


# ---------------------------------------------------------------------------
# H. table.jsonの物理テーブル名（loader.load_table_info / build_physical_table_name）
# ---------------------------------------------------------------------------


def test_h35_load_table_info_reads_schema_and_acquisition_method(tmp_path):
    """H35: table.jsonの「スキーマ」「スキーマ取得方法」フィールドが読み込める。
    フィールドが省略された場合は空文字として扱われる。カラム定義は現在の
    パイプラインでは使用しないため、load_table_info()は「カラム」フィールドの
    有無に関わらず動作する。
    """
    table_json = [
        {
            "テーブル名": "工事台帳",
            "物理名": "T_工事台帳",
            "スキーマ": "PO",
            "スキーマ取得方法": "ODBCリンクテーブル定義から自動取得",
            "カラム": [{"名前": "工事ID", "型": "int"}],
        },
        {
            "テーブル名": "機関マスタ",
            "物理名": "機関マスタ",
        },
    ]
    path = tmp_path / "table.json"
    write_json(path, table_json)

    info = loader.load_table_info(path)

    assert info["工事台帳"] == {
        "物理名": "T_工事台帳",
        "スキーマ": "PO",
        "スキーマ取得方法": "ODBCリンクテーブル定義から自動取得",
    }
    assert info["機関マスタ"] == {"物理名": "機関マスタ", "スキーマ": "", "スキーマ取得方法": ""}


def test_h36_build_physical_table_name_with_and_without_schema():
    """H36: スキーマがある場合は「スキーマ.物理名」、無い場合は物理名のみを返す。"""
    assert loader.build_physical_table_name({"物理名": "節", "スキーマ": "PO"}) == "PO.節"
    assert loader.build_physical_table_name({"物理名": "工事台帳基本", "スキーマ": ""}) == "工事台帳基本"


# ---------------------------------------------------------------------------
# I. マトリックス表のロング形式（縦持ち）出力（matrix.py / output/matrix_long.csv）
# ---------------------------------------------------------------------------


def test_i37_long_entries_direct_and_indirect_marks_correct():
    """I37: 開始クエリが参照するサブクエリがさらに別テーブルを参照する場合、
    開始クエリ・間に挟まる上位のサブクエリすべての行に間接参照（◎）が、
    実際にテーブルへ触れている末端のクエリの行には直接参照（○）が付く。
    """
    known_tables = {"T"}
    queries = {
        "Base": "SELECT [a] FROM T",
        "Mid": "SELECT [a] FROM Base",
        "Main": "SELECT [a] FROM Mid",
    }
    usage, children = resolve_usage_from_queries(queries, known_tables)
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {"T": "T"})

    by_path = {(tuple(e["path"]), e["テーブル名"]): e["マーク"] for e in entries}
    assert by_path[(("Main",), "T")] == "◎"
    assert by_path[(("Main", "Mid"), "T")] == "◎"
    assert by_path[(("Main", "Mid", "Base"), "T")] == "○"
    assert len(entries) == 3  # Mainより浅い行は存在しないため、参照テーブルは各行1件ずつ


def test_i38_long_entries_sorted_within_same_mark():
    """I38: 同一行・同一マークに複数テーブルがある場合、テーブル名でソートされる。"""
    known_tables = {"T", "U"}
    queries = {"Main": "SELECT a.[a], u.[b] FROM T a, U u"}
    usage, children = resolve_usage_from_queries(queries, known_tables)
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {"T": "T", "U": "U"})

    direct_entries = [e for e in entries if e["マーク"] == "○"]
    assert [e["テーブル名"] for e in direct_entries] == ["T", "U"]


def test_i_long_entries_physical_table_name_uses_schema_qualified_form():
    """I: table_physical_names（loader.build_physical_table_name()の出力）が
    「スキーマ.物理名」形式のとき、縦持ちレコードの「テーブル名」にもその形式で反映される。
    """
    known_tables = {"節"}
    queries = {"Main": "SELECT [a] FROM 節"}
    usage, children = resolve_usage_from_queries(queries, known_tables)
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {"節": "PO.節"})

    assert entries == [{"path": ["Main"], "テーブル名": "PO.節", "マーク": "○"}]


def test_i_long_entries_empty_when_no_table_references():
    """I: どのテーブルも参照していないクエリでは、縦持ちレコードは1件も作られない
    （build_matrix_long_dataframe()は開始クエリ・テーブル名・マークの列だけを持つ
    0行のDataFrameを返す）。
    """
    queries = {"Main": "SELECT 1 AS a"}
    usage, children = resolve_usage_from_queries(queries, set())
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {})

    assert entries == []
    df = matrix.build_matrix_long_dataframe(entries)
    assert list(df.columns) == ["開始クエリ", "テーブル名", "マーク"]
    assert len(df) == 0


def test_i39_long_dataframe_column_layout_single_group():
    """I39: 単一グループの場合の列構成（開始クエリ／サブクエリN.../テーブル名／マーク）。"""
    known_tables = {"T"}
    queries = {
        "Base": "SELECT [a] FROM T",
        "Main": "SELECT [a] FROM Base",
    }
    usage, children = resolve_usage_from_queries(queries, known_tables)
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {"T": "T"})
    df = matrix.build_matrix_long_dataframe(entries)

    assert list(df.columns) == ["開始クエリ", "サブクエリ1", "テーブル名", "マーク"]
    assert len(df) == 2  # Main行(◎)・Base行(○)


def test_i40_long_dataframe_matches_prompt_example_for_single_group():
    """I40: プロンプト記載の出力例（クエリ業者入札集計相当の2クエリ構成）と一致する。"""
    known_tables = {"入札明細台帳", "業者台帳"}
    queries = {
        "クエリ業者入札明細": "SELECT A.[業者ID], A.[業者名], B.[金額] FROM 業者台帳 A JOIN 入札明細台帳 B ON A.[業者ID] = B.[業者ID]",
        "クエリ業者入札集計": "SELECT [業者ID], [業者名], SUM([金額]) AS [入札合計] FROM クエリ業者入札明細 GROUP BY [業者ID], [業者名]",
    }
    usage, children = resolve_usage_from_queries(queries, known_tables)
    rows = matrix.build_matrix_rows("クエリ業者入札集計", children, usage, [])
    entries = matrix.build_matrix_long_entries(rows, {"入札明細台帳": "入札明細台帳", "業者台帳": "業者台帳"})
    df = matrix.build_matrix_long_dataframe(entries)

    assert df.to_dict("records") == [
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "入札明細台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "業者台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "入札明細台帳", "マーク": "○"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "業者台帳", "マーク": "○"},
    ]


def test_i41_long_dataframe_pads_columns_across_groups_of_different_depth():
    """I41: 複数の起点クエリ（ネスト数が異なる）分のentriesをまとめて1つの
    DataFrameに渡すと、列数は全体の最大ネスト数に揃えられ、浅いグループの行は
    余った列が空欄になる（output/matrix_long.csv が全起点クエリ分を1ファイルに
    まとめる際の列揃えの検証）。
    """
    known_tables = {"T"}
    shallow_queries = {"Shallow": "SELECT [a] FROM T"}
    deep_queries = {
        "Base": "SELECT [a] FROM T",
        "Mid": "SELECT [a] FROM Base",
        "Deep": "SELECT [a] FROM Mid",
    }

    usage1, children1 = resolve_usage_from_queries(shallow_queries, known_tables)
    rows1 = matrix.build_matrix_rows("Shallow", children1, usage1, [])
    entries1 = matrix.build_matrix_long_entries(rows1, {"T": "T"})

    usage2, children2 = resolve_usage_from_queries(deep_queries, known_tables)
    rows2 = matrix.build_matrix_rows("Deep", children2, usage2, [])
    entries2 = matrix.build_matrix_long_entries(rows2, {"T": "T"})

    df = matrix.build_matrix_long_dataframe(entries1 + entries2)

    assert list(df.columns) == ["開始クエリ", "サブクエリ1", "サブクエリ2", "テーブル名", "マーク"]
    shallow_row = df[df["開始クエリ"] == "Shallow"].iloc[0]
    assert shallow_row["サブクエリ1"] == ""
    assert shallow_row["サブクエリ2"] == ""


def test_i42_end_to_end_matrix_long_csv_output(workdir):
    """I42: main.main() 実行で output/matrix_long.csv が全起点クエリ分をまとめて
    出力され、utf-8-sig（Excelで文字化けしないBOM付きUTF-8）で読み込める。
    """
    write_json(
        workdir / "input" / "クエリ業者入札集計" / "chain_queries.json",
        [
            {
                "クエリ名": "クエリ業者入札明細",
                "SQL": "SELECT A.[業者ID], A.[業者名], B.[金額] FROM 業者台帳 A JOIN 入札明細台帳 B ON A.[業者ID] = B.[業者ID]",
                "呼び出し元": [],
            },
            {
                "クエリ名": "クエリ業者入札集計",
                "SQL": "SELECT [業者ID], [業者名], SUM([金額]) AS [入札合計] FROM クエリ業者入札明細 GROUP BY [業者ID], [業者名]",
                "呼び出し元": ["クエリ業者入札明細"],
            },
        ],
    )
    write_json(
        workdir / "input" / "table.json",
        [
            {"テーブル名": "入札明細台帳", "物理名": "入札明細台帳"},
            {"テーブル名": "業者台帳", "物理名": "業者台帳"},
        ],
    )

    main.main()

    import pandas as pd

    df = pd.read_csv(workdir / "output" / "matrix_long.csv", encoding="utf-8-sig", keep_default_na=False)
    assert list(df.columns) == ["開始クエリ", "サブクエリ1", "テーブル名", "マーク"]
    assert df.to_dict("records") == [
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "入札明細台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "業者台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "入札明細台帳", "マーク": "○"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "業者台帳", "マーク": "○"},
    ]


def test_i43_lineage_xlsx_has_single_long_format_sheet(workdir):
    """I43: 各グループの lineage.xlsx は「テーブル参照マトリクス」（縦持ち形式）の
    1シートのみを持つ（横持ちシート・カラム単位リネージシートはいずれも撤去済み）。
    内容は output/matrix_long.csv のこのグループ分と一致する。
    """
    write_json(
        workdir / "input" / "クエリ業者入札集計" / "chain_queries.json",
        [
            {
                "クエリ名": "クエリ業者入札明細",
                "SQL": "SELECT A.[業者ID], A.[業者名], B.[金額] FROM 業者台帳 A JOIN 入札明細台帳 B ON A.[業者ID] = B.[業者ID]",
                "呼び出し元": [],
            },
            {
                "クエリ名": "クエリ業者入札集計",
                "SQL": "SELECT [業者ID], [業者名], SUM([金額]) AS [入札合計] FROM クエリ業者入札明細 GROUP BY [業者ID], [業者名]",
                "呼び出し元": ["クエリ業者入札明細"],
            },
        ],
    )
    write_json(
        workdir / "input" / "table.json",
        [
            {"テーブル名": "入札明細台帳", "物理名": "入札明細台帳"},
            {"テーブル名": "業者台帳", "物理名": "業者台帳"},
        ],
    )

    main.main()

    import pandas as pd

    xlsx_path = workdir / "output" / "クエリ業者入札集計" / "lineage.xlsx"
    sheets = pd.read_excel(xlsx_path, sheet_name=None)
    assert list(sheets.keys()) == ["テーブル参照マトリクス"]

    df_long = sheets["テーブル参照マトリクス"].fillna("")
    assert list(df_long.columns) == ["開始クエリ", "サブクエリ1", "テーブル名", "マーク"]
    assert df_long.to_dict("records") == [
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "入札明細台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "", "テーブル名": "業者台帳", "マーク": "◎"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "入札明細台帳", "マーク": "○"},
        {"開始クエリ": "クエリ業者入札集計", "サブクエリ1": "クエリ業者入札明細", "テーブル名": "業者台帳", "マーク": "○"},
    ]


def matrix_long_from_output(workdir, start_query):
    import pandas as pd

    return pd.read_excel(workdir / "output" / start_query / "lineage.xlsx", sheet_name="テーブル参照マトリクス")
