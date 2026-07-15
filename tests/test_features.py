"""機能要件網羅テスト（テスト観点A〜H、README/背景の記述に基づく）。

各テスト関数のdocstring先頭に観点番号（例: A1, C14）を付与している。
グルーピング機能（旧・観点E）は方針転換により撤去されたため、対応するテストは存在しない。
G（代表クエリ・重複チェーン除外）は現在のsrc/に対応する実装が
一切存在しない（representative_queries.json/skipped_queries.json関連の
コードもgit履歴も見つからない）ため、テストを書かず本ファイル末尾のコメントで
その旨を記録するに留める。
"""

import json

import config
import lineage_extract
import loader
import main
import matrix
import report
import table_usage
import validate


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# A. 入力読み込み（loader.py / config.py）
# ---------------------------------------------------------------------------


def test_a1_table_json_parsing(tmp_path):
    """A1: table.jsonの読み込み（種別/接続先/物理名/カラム型）。"""
    table_json = [
        {
            "テーブル名": "工事台帳",
            "種別": "ローカルテーブル",
            "接続先": "",
            "物理名": "T_工事台帳",
            "カラム": [
                {"名前": "工事ID", "型": "int"},
                {"名前": "工事名称", "型": "varchar"},
            ],
        },
        {
            "テーブル名": "機関マスタ",
            "種別": "リンクテーブル",
            "接続先": "C:\\DB\\a.accdb",
            "物理名": "機関マスタ",
            "カラム": [
                {"名前": "機関コード"},  # 型省略 -> "text"
            ],
        },
    ]
    path = tmp_path / "table.json"
    write_json(path, table_json)

    schema = loader.load_schema(path)

    assert schema == {
        "工事台帳": {"工事ID": "int", "工事名称": "varchar"},
        "機関マスタ": {"機関コード": "text"},
    }
    # 種別・接続先・物理名は load_schema() のdocstring通り、スキーマ辞書には含まれない
    # （読み込み自体はできるが、パイプラインの他のどこにも保持されず捨てられる）


def test_a1_table_json_parsing_ignores_schema_fields(tmp_path):
    """A1続き: table.jsonに スキーマ／スキーマ取得方法 フィールドが含まれていても
    load_schema() の返すスキーマ辞書には影響しない（load_table_info()側で扱う）。
    """
    table_json = [
        {
            "テーブル名": "工事台帳",
            "物理名": "T_工事台帳",
            "スキーマ": "PO",
            "スキーマ取得方法": "ODBCリンクテーブル定義から自動取得",
            "カラム": [{"名前": "工事ID", "型": "int"}],
        }
    ]
    path = tmp_path / "table.json"
    write_json(path, table_json)

    schema = loader.load_schema(path)

    assert schema == {"工事台帳": {"工事ID": "int"}}


def test_a2_chain_queries_format_is_generically_loadable(tmp_path):
    """A2: chain_queries.json形式（呼び出し元付き）のクエリ名・SQLパース。

    ※ 注記: load_queries() 自体はファイル名に依存せず動く汎用関数のため
    chain_queries.json形式でも問題なく読める。
    """
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


def test_a3_converted_queries_used_when_present(workdir):
    """A3: converted_queries.jsonが存在する場合、それが解析に使われる。"""
    start = "クエリI"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "converted_queries.json", [{"クエリ名": "クエリI", "SQL": "SELECT [a] FROM [dbo].[T]"}])
    schema = {"T": {"a": "int", "b": "int"}}

    main.process_group(start, schema, {})

    analysis = json.loads((workdir / "output" / start / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["クエリI"]["extract_select_columns"] == ["a"]


def test_a3_converted_queries_takes_priority_over_chain_queries(workdir):
    """A3続き: converted_queries.jsonとchain_queries.jsonが両方存在する場合、
    converted_queries.jsonの内容が使われ、chain_queries.jsonは使われない。
    """
    start = "クエリJ"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "converted_queries.json", [{"クエリ名": "クエリJ", "SQL": "SELECT [a] FROM [dbo].[T]"}])
    write_json(d / "chain_queries.json", [{"クエリ名": "クエリJ", "SQL": "SELECT [b] FROM [dbo].[T]", "呼び出し元": []}])
    schema = {"T": {"a": "int", "b": "int"}}

    main.process_group(start, schema, {})

    analysis = json.loads((workdir / "output" / start / "analysis.json").read_text(encoding="utf-8"))
    # converted_queries.json の "a" が使われ、chain_queries.json の "b" は使われない
    assert analysis["クエリJ"]["extract_select_columns"] == ["a"]


def test_a4_chain_queries_used_as_fallback_when_converted_missing(workdir):
    """A4: converted_queries.jsonが存在せずchain_queries.jsonのみの場合、
    chain_queries.json（VBA出力・AI変換前）がフォールバックとして使われ、
    リネージ解析が実行される。
    """
    start = "クエリK"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(d / "chain_queries.json", [{"クエリ名": "クエリK", "SQL": "SELECT [a] FROM [dbo].[T]", "呼び出し元": []}])
    schema = {"T": {"a": "int"}}

    main.process_group(start, schema, {})

    analysis = json.loads((workdir / "output" / start / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["クエリK"]["extract_select_columns"] == ["a"]


def test_a4_skip_when_neither_chain_queries_nor_converted_exists(workdir, capsys):
    """A4続き: chain_queries.json・converted_queries.jsonのどちらも存在しない場合は
    エラーメッセージを出して当該フォルダをスキップする。
    """
    start = "クエリZ"
    (workdir / "input" / start).mkdir(parents=True)
    schema = {"T": {"a": "int"}}

    main.process_group(start, schema, {})

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


def test_a6_discover_start_queries_under_input_dir(workdir):
    """A6: 起点クエリ検出。input/ 直下のサブフォルダのうち、
    chain_queries.json またはconverted_queries.jsonが存在するものだけが対象になる。
    AI変換前の chain_queries.json のみのフォルダも対象に含まれる
    （従来の「converted_queries.jsonが存在するフォルダのみ対象」という制約は緩和された）。
    """
    (workdir / "input" / "クエリA").mkdir(parents=True)
    write_json(workdir / "input" / "クエリA" / "converted_queries.json", [])

    (workdir / "input" / "クエリB").mkdir(parents=True)
    write_json(workdir / "input" / "クエリB" / "chain_queries.json", [])  # AI変換前のみ

    (workdir / "input" / "空フォルダ").mkdir(parents=True)

    # input/table.json はサブフォルダの兄弟に同居するファイルであり、フォルダではないため対象外
    (workdir / "input" / "table.json").write_text("[]", encoding="utf-8")

    result = config.discover_start_queries()

    assert result == ["クエリA", "クエリB"]


# ---------------------------------------------------------------------------
# B. AI変換済みSQLの事前検証（validate.py）
# ---------------------------------------------------------------------------


def test_b7_valid_sql_passes():
    """B7: 正常なSQLがエラーなく通過する。"""
    schema = {"T": {"a": "int", "b": "int"}}
    sql = "SELECT [a], [b] FROM [dbo].[T]"
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "OK"
    assert result["問題"] == []


def test_b8_syntax_error_detected():
    """B8: 構文エラーを含むSQLが検知される。"""
    schema = {"T": {"a": "int"}}
    sql = "SELEC [a] FROM [dbo].[T]"  # SELECTのタイポ
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "NG"
    assert any(p["種別"] == "構文エラー" for p in result["問題"])


def test_b9_multiple_statements_detected():
    """B9: 1ファイルに複数ステートメントが混入している場合の検知。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT [a] FROM [dbo].[T]; SELECT [a] FROM [dbo].[T]"
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "NG"
    assert any(p["種別"] == "複数ステートメント" for p in result["問題"])


def test_b10_unconverted_access_function_detected():
    """B10: Access固有関数（未変換）が残存している場合の検知。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT Nz([a], 0) AS a FROM [dbo].[T]"
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "NG"
    assert any(p["種別"] == "未知の関数" and "Nz" in p["メッセージ"] for p in result["問題"])


def test_b11_schema_mismatch_detected_unknown_column():
    """B11: 存在しないカラムを参照した場合のスキーマ不整合検知。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT [nonexistent_col] FROM [dbo].[T]"
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "NG"
    assert any(p["種別"] == "スキーマ不整合" for p in result["問題"])


def test_b11_schema_mismatch_detected_unknown_table():
    """B11続き: 存在しないテーブルを参照した場合のスキーマ不整合検知。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT [a] FROM [dbo].[NonExistentTable]"
    result = validate.validate_query("Q", sql, {"Q": sql}, schema)
    assert result["判定"] == "NG"
    assert any(p["種別"] == "スキーマ不整合" for p in result["問題"])


# ---------------------------------------------------------------------------
# C. リネージ解析（lineage_extract.py / sql_expand.py）
# ---------------------------------------------------------------------------


def test_c12_single_table_reference():
    """C12: 単一テーブル参照クエリでテーブル・カラムの由来が解決される。"""
    schema = {"T": {"a": "int", "b": "int"}}
    sql = "SELECT [a], [b] FROM [dbo].[T]"
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, [])
    rows = {r["最終出力カラム"]: r for r in entry["lineage"]}
    assert rows["a"]["参照テーブル"] == "T"
    assert rows["a"]["参照カラム"] == "a"
    assert rows["b"]["参照テーブル"] == "T"
    assert rows["b"]["参照カラム"] == "b"


def test_c13_unqualified_column_single_candidate():
    """C13: 未修飾カラムで単一候補の場合の正しい解決。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT a FROM [dbo].[T]"
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, [])
    row = entry["lineage"][0]
    assert row["参照テーブル"] == "T"
    assert row["参照カラム"] == "a"


def test_c14_ambiguous_unqualified_column_raises_error_not_silent_unknown():
    """C14: 未修飾カラムが複数JOIN先に同名で存在する場合、無警告で「不明」に
    ならず例外・エラーログとして検知される（validate_qualify_columns=True）。
    """
    schema = {
        "T1": {"id": "int", "name": "varchar"},
        "T2": {"id": "int", "name": "varchar"},
    }
    sql = "SELECT name FROM [dbo].[T1] JOIN [dbo].[T2] ON [dbo].[T1].[id] = [dbo].[T2].[id]"
    error_log = []
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, error_log)

    assert any(e["種別"] == "expand_sql失敗" and e["クエリ"] == "Q" for e in error_log)
    # 無警告で「不明」に落ちるのではなく、"解析失敗"経路の行になる
    assert entry["lineage"][0]["参照クエリパス"] == ["解析失敗"]
    assert entry["lineage"][0]["参照テーブル"] == "不明"


def test_c15_duplicate_output_column_names_resolved_by_position():
    """C15: 同名カラムが複数存在するクエリで位置ベースに区別して解決される。"""
    schema = {
        "T1": {"id": "int", "name": "varchar"},
        "T2": {"id": "int", "name": "varchar"},
    }
    sql = (
        "SELECT [dbo].[T1].[name], [dbo].[T2].[name] "
        "FROM [dbo].[T1] JOIN [dbo].[T2] ON [dbo].[T1].[id] = [dbo].[T2].[id]"
    )
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, [])
    assert entry["lineage"][0]["参照テーブル"] == "T1"
    assert entry["lineage"][1]["参照テーブル"] == "T2"


def test_c16_one_level_subquery():
    """C16: 1階層のサブクエリで参照テーブル・カラムが正しく追跡される。"""
    schema = {"T": {"a": "int"}}
    sql = "SELECT [x].[a] FROM (SELECT [a] FROM [dbo].[T]) AS [x]"
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, [])
    row = entry["lineage"][0]
    assert row["参照テーブル"] == "T"
    assert row["参照カラム"] == "a"


def test_c17_nested_two_level_registered_query_chain():
    """C17: 2階層以上ネストした（登録済み）クエリ参照を再帰的に追跡する。"""
    schema = {"T": {"a": "int"}}
    queries = {
        "Base": "SELECT [a] FROM [dbo].[T]",
        "Mid": "SELECT [a] FROM Base",
        "Main": "SELECT [a] FROM Mid",
    }
    entry = lineage_extract.analyze_query("Main", queries["Main"], schema, queries, [])
    row = entry["lineage"][0]
    assert row["参照テーブル"] == "T"
    assert row["参照カラム"] == "a"
    assert row["参照クエリパス"] == ["Mid", "Base"]


def test_c18_intermediate_query_direct_usage_excludes_descendant_tables():
    """C18: 中間クエリの「直接参照」は自分自身のSQLが触れるものだけに限られ、
    子孫クエリ経由の間接的なテーブル参照は「間接」側にのみ計上される。
    ○/◎の判定ロジック自体（table_usage.resolve_table_usage）は出力形式変更の
    影響を受けず、従来通りの結果を返す（マトリックス表の出力形式変更に対する回帰確認）。
    """
    schema = {"T": {"a": "int"}}
    queries = {
        "Base": "SELECT [a] FROM [dbo].[T]",
        "Mid": "SELECT [a] FROM Base",
        "Main": "SELECT [a] FROM Mid",
    }
    usage, children = table_usage.resolve_table_usage(queries, schema, [])

    assert usage["Base"] == {"direct": {"T"}, "indirect": set()}
    # Mid は自分のSQL上ではBase（クエリ）しか参照していないため直接テーブル参照は空
    assert usage["Mid"] == {"direct": set(), "indirect": {"T"}}
    assert usage["Main"] == {"direct": set(), "indirect": {"T"}}
    assert children == {"Base": set(), "Mid": {"Base"}, "Main": {"Mid"}}


def test_c19_ascii_identifier_casing_restored():
    """C19: ASCII識別子で、qualify()による小文字化後も元の大文字小文字が復元される。"""
    schema = {"OrderTable": {"OrderID": "int", "OrderName": "varchar"}}
    sql = "SELECT [OrderID], [OrderName] FROM [dbo].[OrderTable]"
    entry = lineage_extract.analyze_query("Q", sql, schema, {"Q": sql}, [])
    rows = {r["最終出力カラム"]: r for r in entry["lineage"]}
    assert rows["OrderID"]["参照テーブル"] == "OrderTable"
    assert rows["OrderID"]["参照カラム"] == "OrderID"
    assert rows["OrderName"]["参照テーブル"] == "OrderTable"
    assert rows["OrderName"]["参照カラム"] == "OrderName"


# ---------------------------------------------------------------------------
# D. レポート出力（report.py）
# ---------------------------------------------------------------------------


def test_d20_lineage_dataframe_column_layout():
    """D20: lineage.xlsx（カラム単位リネージ）の列構成。

    ※ 注記: 実装（およびREADMEの仕様表）での列順は
    開始クエリ／最終出力カラム／参照クエリ1.../参照テーブル／参照カラム であり、
    「最終出力カラム」は2列目に来る。
    """
    schema = {"T": {"a": "int"}}
    queries = {
        "Base": "SELECT [a] FROM [dbo].[T]",
        "Main": "SELECT [a] FROM Base",
    }
    analysis_log = {
        name: lineage_extract.analyze_query(name, sql, schema, queries, [])
        for name, sql in queries.items()
    }
    df = report.build_lineage_dataframe(analysis_log)

    assert list(df.columns) == ["開始クエリ", "最終出力カラム", "参照クエリ1", "参照テーブル", "参照カラム"]


def test_d21_error_json_records_failures(workdir):
    """D21: 解析失敗・不明行がある場合、error.jsonに正しく記録される。"""
    start = "クエリL"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "converted_queries.json",
        [{"クエリ名": "クエリL", "SQL": "SELECT [nonexistent_col] FROM [dbo].[T]"}],
    )
    schema = {"T": {"a": "int"}}

    main.process_group(start, schema, {})

    error_log = json.loads((workdir / "output" / start / "error.json").read_text(encoding="utf-8"))
    assert len(error_log) >= 1
    assert any(e["種別"] == "expand_sql失敗" and e["クエリ"] == "クエリL" for e in error_log)

    lineage_rows = json.loads((workdir / "output" / start / "analysis.json").read_text(encoding="utf-8"))
    assert lineage_rows["クエリL"]["lineage"][0]["参照テーブル"] == "不明"


def test_d22_analysis_json_complete_when_zero_failures(workdir):
    """D22: 解析失敗・不明行がゼロのケースで、analysis.jsonに全件正しく出力される。"""
    start = "クエリM"
    d = workdir / "input" / start
    d.mkdir(parents=True)
    write_json(
        d / "converted_queries.json",
        [
            {"クエリ名": "クエリ基本", "SQL": "SELECT A.[a], A.[b] FROM [dbo].[T] A"},
            {"クエリ名": "クエリM", "SQL": "SELECT [a], [b] FROM クエリ基本"},
        ],
    )
    schema = {"T": {"a": "int", "b": "int"}}

    main.process_group(start, schema, {})

    error_log = json.loads((workdir / "output" / start / "error.json").read_text(encoding="utf-8"))
    assert error_log == []

    analysis = json.loads((workdir / "output" / start / "analysis.json").read_text(encoding="utf-8"))
    assert set(analysis.keys()) == {"クエリ基本", "クエリM"}
    assert len(analysis["クエリ基本"]["lineage"]) == 2
    assert len(analysis["クエリM"]["lineage"]) == 2
    for row in analysis["クエリM"]["lineage"]:
        assert row["参照テーブル"] == "T"
        assert row["参照カラム"] in ("a", "b")


# ---------------------------------------------------------------------------
# F. マトリックス表（参照物理テーブル名列、○/◎表記）
# ---------------------------------------------------------------------------


def test_f29_direct_reference_listed_with_maru():
    """F29: 開始クエリが直接参照するテーブルが「参照物理テーブル名」列に
    「物理名(○)」の形式で載る。
    """
    schema = {"T": {"a": "int"}, "U": {"b": "int"}}
    queries = {"Main": "SELECT [a] FROM [dbo].[T]"}
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {"T": "T", "U": "U"})

    row = df[df["開始クエリ"] == "Main"].iloc[0]
    assert row["参照物理テーブル名"] == "T(○)"


def test_f30_indirect_reference_listed_with_nijuumaru_on_all_ancestor_rows():
    """F30: 開始クエリが参照するサブクエリがさらに別テーブルを参照する場合、
    開始クエリ・間に挟まる上位のサブクエリすべての行の「参照物理テーブル名」に
    「物理名(◎)」の形式で載る。○/◎の判定ロジック自体（rowsのdirect/indirect）は
    テーブル列形式時代と変わらない（出力形式のみが変わったことの回帰確認）。
    """
    schema = {"T": {"a": "int"}}
    queries = {
        "Base": "SELECT [a] FROM [dbo].[T]",
        "Mid": "SELECT [a] FROM Base",
        "Main": "SELECT [a] FROM Mid",
    }
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {"T": "T"})

    by_path = {tuple(r["path"]): r for r in rows}
    assert by_path[("Main",)]["indirect"] == {"T"}
    assert by_path[("Main", "Mid")]["indirect"] == {"T"}
    assert by_path[("Main", "Mid", "Base")]["direct"] == {"T"}

    main_row = df[(df["開始クエリ"] == "Main") & (df.get("サブクエリ1", "") == "")].iloc[0]
    mid_row = df[df.get("サブクエリ1", "") == "Mid"].iloc[0]
    base_row = df[df.get("サブクエリ2", "") == "Base"].iloc[0]

    assert main_row["参照物理テーブル名"] == "T(◎)"
    assert mid_row["参照物理テーブル名"] == "T(◎)"
    assert base_row["参照物理テーブル名"] == "T(○)"


def test_f31_direct_and_indirect_combined_in_one_cell_comma_separated():
    """F31: 同じ行に直接参照テーブルと間接参照テーブルが両方ある場合、
    「参照物理テーブル名」列に直接→間接の順でカンマ区切りにまとめて記載される。
    """
    schema = {"T": {"a": "int"}, "U": {"b": "int"}}
    queries = {
        "Sub": "SELECT [b] FROM [dbo].[U]",
        "Main": "SELECT a.[a], s.[b] FROM [dbo].[T] a, Sub s",
    }
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {"T": "T", "U": "U"})

    main_row = df[(df["開始クエリ"] == "Main") & (df.get("サブクエリ1", "") == "")].iloc[0]
    assert main_row["参照物理テーブル名"] == "T(○), U(◎)"


def test_f32_physical_table_name_uses_schema_qualified_form():
    """F32: table_physical_names（loader.build_physical_table_name()の出力）が
    「スキーマ.物理名」形式のとき、マトリックス表の「参照物理テーブル名」列にも
    その形式で反映される。
    """
    schema = {"節": {"a": "int"}}
    queries = {"Main": "SELECT [a] FROM [dbo].[節]"}
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {"節": "PO.節"})

    row = df[df["開始クエリ"] == "Main"].iloc[0]
    assert row["参照物理テーブル名"] == "PO.節(○)"


def test_f33_no_table_column_enumeration_in_matrix():
    """F33: マトリックス表にテーブルを全列挙する列群は存在せず、
    列は 開始クエリ／サブクエリN.../参照物理テーブル名 のみになる。
    """
    schema = {"T": {"a": "int"}, "未使用テーブル": {"x": "int"}}
    queries = {"Main": "SELECT [a] FROM [dbo].[T]"}
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {"T": "T", "未使用テーブル": "未使用テーブル"})

    assert list(df.columns) == ["開始クエリ", "参照物理テーブル名"]


def test_f34_empty_when_no_table_references():
    """F34: どのテーブルも参照していない行では「参照物理テーブル名」が空文字になる。"""
    schema = {}
    queries = {"Main": "SELECT 1 AS a"}
    usage, children = table_usage.resolve_table_usage(queries, schema, [])
    rows = matrix.build_matrix_rows("Main", children, usage, [])
    df = matrix.build_matrix_dataframe(rows, {})

    row = df[df["開始クエリ"] == "Main"].iloc[0]
    assert row["参照物理テーブル名"] == ""


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
    フィールドが省略された場合は空文字として扱われる。
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
            "カラム": [{"名前": "機関コード"}],
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
