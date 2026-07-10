# lineage-matrix

VBA が検出したAccess クエリのチェーン（`converted_queries.json`）を解析し、
各クエリの出力カラムがどのテーブル・カラムに由来するかをフラットテーブル形式で Excel 出力するツール。

**AI変換済みSQL Server用SQL を対象** にした、リネージ解析と影響範囲分析に特化したツール。

カラムの由来解決には [SQLGlot](https://github.com/tobymao/sqlglot) の `lineage()` を使用し、
サブクエリ・CTE・集計関数（SUM等）・クエリ間参照を含む複雑な構造にも対応する。

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行方法

```bash
python3 src/main.py
```

リポジトリルートから実行する（`input/`・`output/` は実行時のカレントディレクトリ基準の相対パス）。
`input/table.json`（全テーブルの一覧）と `input/<起点クエリ名>/converted_queries.json`（AI変換済みのクエリ一覧）
を読み込み、起点クエリごとに `output/<起点クエリ名>/` へ結果を生成する。

起点クエリは `input/` 直下のサブフォルダのうち、`converted_queries.json` が存在するものから **自動検出** される。
設定ファイルは不要。

---

## `input/` のインターフェース仕様

```
input/
├── table.json                           # 全テーブルのフラットな一覧（VBAが出力）
├── クエリ工事委託分析/
│   └── converted_queries.json          # AI変換済みSQL（必須）
└── クエリ入札委託集計分析/
    └── converted_queries.json          # AI変換済みSQL（必須）
```

**起点クエリは自動検出される**：`input/` 直下のサブフォルダのうち、
`converted_queries.json` が存在するサブフォルダ名が起点クエリとして自動的に認識される。
設定ファイル（`start_queries.json` など）は不要。

### 運用フロー

1. **VBA実行**: Access クエリを検出・チェーン検出 → `chain_queries.json` 出力
2. **AI変換**: `chain_queries.json` をAI変換サービスで SQL Server SQL に変換
3. **配置**: 変換結果を `input/<起点クエリ名>/converted_queries.json` に配置
4. **実行**: `python3 src/main.py` → 自動検出・リネージ解析 → 結果出力

### AI変換プロンプト

`chain_queries.json` → `converted_queries.json` のAI変換に使用するプロンプト。
JOIN構造の欠落防止と、SQL内コメント埋め込みによる列消失防止のための規定を含む
（詳細は本リポジトリでの検証結果を参照）。

```markdown
以下のJSONに含まれるSQLをAccess（Jet SQL）からSQL Server（T-SQL）に変換してください。

## 入力

添付するJSONファイル（chain_queries.json）には以下の構造でクエリが含まれています。

[
  { "クエリ名": "クエリMain",     "SQL": "SELECT ... （Access SQL）" },
  { "クエリ名": "クエリ工事集計", "SQL": "SELECT ... （Access SQL）" }
]

## 変換ルール

以下のAccess固有の構文をSQL Server用に変換してください。

| Access構文 | SQL Server変換後 |
|---|---|
| `IIf(条件, 真, 偽)` | `CASE WHEN 条件 THEN 真 ELSE 偽 END` |
| `Nz(値, 代替値)` | `ISNULL(値, 代替値)` |
| `Now()` | `GETDATE()` |
| `Date()` | `CAST(GETDATE() AS DATE)` |
| `DateAdd("d", n, 日付)` | `DATEADD(day, n, 日付)` |
| `DateDiff("d", 日付1, 日付2)` | `DATEDIFF(day, 日付1, 日付2)` |
| `Format(値, "書式")` | `FORMAT(値, '書式')`（書式文字列はAccessと.NETでトークンの大文字小文字の意味が異なるため、`mm`=月→`MM`、`nn`=分→`mm`など正しく変換すること） |
| `Mid(str, start, len)` | `SUBSTRING(str, start, len)` |
| `InStr(str, 検索文字)` | `CHARINDEX(検索文字, str)` |
| `文字列1 & 文字列2` | `ISNULL(文字列1, '') + ISNULL(文字列2, '')`（`&`はNULLを無視して結合するが`+`はNULLを伝播するため、NULLを許容し得るカラムには必ずISNULLを挟むこと） |
| `Like "*abc*"` / `Like "a?c"` | `LIKE '%abc%'` / `LIKE 'a_c'`（ワイルドカード `*`→`%`、`?`→`_`） |
| `#2024/01/01#` | `'2024-01-01'` |
| `True` / `False` | `1` / `0` |
| `SELECT DISTINCTROW` | T-SQLに直接対応する構文がない。単純な`DISTINCT`と意味が異なりうるため、機械的に置き換えず警告として残すこと（後述の`warnings`参照） |
| `PARAMETERS ... ;` 宣言 | SQL Serverでは不要のため削除し、警告として残すこと |
| `TRANSFORM ... PIVOT`（クロス集計） | PIVOTまたはCASE WHENで書き換え。ピボット列の値が静的に特定できない場合は機械的に変換せず警告として残すこと |

## 構造の完全性（最重要・省略厳禁）

- **SELECT / WHERE / ORDER BY / GROUP BY 句が参照する全てのテーブル（エイリアス含む）は、必ずFROM/JOIN句にも存在しなければならない。** 変換後、参照テーブルとFROM/JOIN句のテーブルを突き合わせて、一つでも欠落があれば変換を中断し、`warnings`にその旨を記録すること。
- **SQLは行数・文字数に関わらず、一字一句省略せず変換すること。** 長いSELECT句・深いJOINのネストであっても、要約・間引き・一部省略は禁止する。
- 変換前のJOIN数と変換後のJOIN数が一致するか必ず数えて確認すること。一致しない場合は理由を`warnings`に記録すること。

## 変換時の注意事項

- クエリ名は変更しないこと
- テーブル名・カラム名は変更しないこと（日本語のまま）
- ブラケット（`[]`）はSQL Serverでもそのまま使用可
- FROM句・JOIN句のクエリ名参照（例：`FROM クエリ工事集計`）はそのまま残すこと
  （クエリ参照の解決はツール側で行うため変換不要）
- 各SQLは単一のSELECT文とすること。文末にセミコロン（`;`）を付けないこと
  （文末セミコロンの重複や、セミコロン後のコメントが残ると、後続の解析ツールがSQLを複数ステートメントと誤認してエラーになるため）
- **変換が困難・不確かな箇所、あるいは暗黙の型変換に依存する箇所は、SQL文字列の中にコメントを埋め込まず、必ず出力JSONの`warnings`配列に記録すること。**
  （SQL内に`-- コメント`を混在させると、AIがコメントと実コードを同じ行に誤って配置した場合、後続のSQL解析ツールが気づかないまま該当箇所を丸ごと欠落させる恐れがあるため、SQL本文へのコメント埋め込みは一切禁止する）

## 出力

入力と同じJSON構造に加えて、クエリごとに`warnings`配列（問題なければ空配列）を持たせて出力してください。
出力は有効なJSON形式とし、SQL文字列内の改行・ダブルクォート・バックスラッシュは正しくエスケープすること。

[
  {
    "クエリ名": "クエリMain",
    "SQL": "SELECT ... （T-SQL、コメントなし）",
    "warnings": ["要確認：DISTINCTROWをDISTINCTに置換（意味が異なる可能性あり）"]
  },
  {
    "クエリ名": "クエリ工事集計",
    "SQL": "SELECT ... （T-SQL）",
    "warnings": []
  }
]

出力したJSONは `converted_queries.json` として保存し、
`input/<起点クエリ名>/converted_queries.json` に配置してください。
```

### `converted_queries.json`（AI変換済みSQLのフラット一覧）

VBA がチェーン検出した結果をAI変換したもの。配列形式で、各要素は`クエリ名` と `SQL` を持つ。

```json
[
  { "クエリ名": "クエリ工事基本",   "SQL": "SELECT A.[工事ID], A.[工事名称], B.[発注金額], B.[発注日] FROM [dbo].[工事台帳] A JOIN [dbo].[発注台帳] B ON A.[工事ID] = B.[工事ID]" },
  { "クエリ名": "クエリ工事集計",   "SQL": "SELECT [工事ID], [工事名称], SUM([発注金額]) AS [発注合計] FROM クエリ工事基本 GROUP BY [工事ID], [工事名称]" },
  { "クエリ名": "クエリ工事委託分析", "SQL": "SELECT クエリ工事集計.[工事名称], ... FROM クエリ工事集計 JOIN ..." }
]
```

| フィールド | 型 | 内容 |
|---|---|---|
| `クエリ名` | string | Accessクエリ名。`src/loader.py` の `load_queries()` がこれをキーとして辞書化する。 |
| `SQL` | string | AI変換済みの SQL Server 用 SQL。スキーマ修飾（`[dbo].[テーブル名]`）を含む。 |

**テーブル参照の形式**: スキーマ修飾を含む `[dbo].[テーブル名]` の形式。
sqlglot が正確に解析できるため、リネージ解決が正確になる。

**クエリ間参照はそのまま**：`FROM クエリ工事基本` のようにクエリ名で参照する。
リネージ解析時に `sources=` で展開される。

### `table.json`（テーブル情報・全件フラット）

物理テーブルのカラム定義一覧。`src/loader.py` の `load_schema()` が `sqlglot.lineage()` に渡すスキーマ辞書に変換する。

```json
[
  {
    "テーブル名": "工事台帳",
    "種別": "ローカルテーブル",
    "接続先": "",
    "物理名": "工事台帳",
    "カラム": [
      { "名前": "工事ID",   "型": "int" },
      { "名前": "工事名称", "型": "varchar" }
    ]
  },
  {
    "テーブル名": "機関マスタ",
    "種別": "リンクテーブル",
    "接続先": "C:\\DB\\名称.accdb",
    "物理名": "機関マスタ",
    "カラム": [
      { "名前": "機関コード", "型": "varchar" }
    ]
  }
]
```

| フィールド | 型 | 内容 |
|---|---|---|
| `テーブル名` | string | Accessクエリ上で参照される論理名。`lineage()` のスキーマ辞書のキーになる。 |
| `種別` | string | 「ローカルテーブル」／「リンクテーブル」等。メタデータとして保持。 |
| `接続先` | string | リンクテーブルの接続先ファイルパス等。メタデータとして保持。 |
| `物理名` | string | 接続先における実際のオブジェクト名。メタデータとして保持。 |
| `カラム` | array | そのテーブルが持つカラムの配列。 |
| `カラム[].名前` | string | カラム名。 |
| `カラム[].型` | string（省略可） | カラムの型。省略時は `"text"` として扱われる。 |

---

## 出力：`output/<起点クエリ名>/`

フォルダごとに以下の3ファイルを生成する。

### `lineage.xlsx`

そのフォルダに含まれる各クエリ（起点クエリから`detect_chain()`で辿り着いたチェーン全体）について、SELECTの出力カラムごとに1行のフラットテーブル。

| 列名 | 内容 |
|---|---|
| 開始クエリ | `クエリ名`（SQL自体をパースした対象クエリ） |
| 最終出力カラム | 開始クエリのSELECTで実際に出力されるカラム名（集計・エイリアス後の名前） |
| 参照クエリ1, 参照クエリ2, ... | 出力カラムの由来を辿る過程で経由したものを、外側→内側の順に並べたもの。2種類の要素が混在しうる：(1) 登録済みクエリ名（`source_name` 由来。FROM句で別名エイリアスを付けていても実際のクエリ名で表示される）、(2) SQL内に直接書かれたローカルな無名サブクエリのエイリアス（`reference_node_name` 由来。例：サブクエリ1）。列数はそのフォルダ内で実際に現れる最大ネスト数に応じて動的に決まる（ネストがなければ1列のみ）。直接参照の場合は `参照クエリ1` に「（直接）」、それ以外の列は空欄。 |
| 参照テーブル | 最終的に辿り着いた物理テーブル名。解決できない場合は「不明」 |
| 参照カラム | 参照テーブル上の実際のカラム名。解決できない場合は「不明」 |

ネストがどれだけ深くても最終的に行き着く先は物理テーブル1つ・カラム1つなので、動的に列展開されるのは「参照クエリ」だけで、「参照テーブル」「参照カラム」は常に単一列。

### `analysis.json`

クエリ名ごとの解析結果そのもの。**`lineage.xlsx` はこの中身（各クエリの `lineage`）を
連結して整形しただけのビュー**であり、別ロジックで再計算されたものではない
（`analyze_query()` が `expand_query_ast` + `qualify` を1回だけ実行し、その結果を
analysis.json用のログとlineage解決の両方に使い回している）。

```json
{
  "クエリ工事集計": {
    "extract_select_columns": ["工事ID", "工事名称", "発注合計"],
    "expand_query_ast_repr": ["Select(", "  expressions=[...", "..."],
    "expand_sql": "SELECT [工事ID], ... FROM (SELECT ...) AS [クエリ工事基本] GROUP BY ...",
    "lineage": [
      {
        "開始クエリ": "クエリ工事集計",
        "最終出力カラム": "工事ID",
        "参照クエリパス": ["クエリ工事基本"],
        "参照テーブル": "T_工事",
        "参照カラム": "工事ID"
      }
    ]
  }
}
```

- `extract_select_columns`：そのクエリのSELECT出力カラム一覧。`SELECT *`（テーブル修飾ありの`t.*`も含む）が含まれる場合は、`sqlglot.optimizer.qualify.qualify(expand_stars=True)`（`table.json`のスキーマを使用）で実際のカラム名リストに展開してから記録する。展開先が`table.json`に登録されていない参照（他クエリへの`*`等）の場合は展開できず`"*"`のまま残る
- `expand_query_ast_repr`：クエリ参照展開後のASTの `repr()` を1行ずつ配列化したもの（`repr()` 自体はSQLGlot標準の多行整形。JSON文字列1本に詰めると改行がエスケープされて読みにくくなるため、`splitlines()` で配列にしている）
- `expand_sql`：展開後の最終的なSQLテキスト
- `lineage`：出力カラムごとのリネージ行（`lineage.xlsx`の1行に対応する未整形のフラット行。「参照クエリパス」は外側→内側の順の配列で、`lineage.xlsx`側では動的に「参照クエリ1, 参照クエリ2, ...」列に展開される）

`expand_query_ast()` または `qualify()` が例外を送出した場合、`expand_query_ast_repr` / `expand_sql` は `null` になり、`lineage` にはそのクエリの全出力カラム分の失敗行（`参照クエリパス: ["解析失敗"]`）が入る（`error.json`側は `expand_sql失敗` としてクエリ単位で1件のみ記録され、カラムごとに重複しない）。

### `error.json`

処理中に検知したエラー・警告の配列（構造化ログ）。エラーがなければ `[]`。

```json
[
  {
    "クエリ": "クエリG",
    "種別": "lineage失敗",
    "対象カラム": "合計金額",
    "メッセージ": "..."
  }
]
```

`種別` は `クエリ名衝突警告`（クエリ名とテーブル名が同名で衝突）・`expand_sql失敗`（`expand_query_ast`によるクエリ参照展開、または展開後の`qualify(validate_qualify_columns=True)`の例外。クエリ単位で1件のみ記録される）・`lineage失敗`（展開・qualify自体は成功した後、個別の出力カラムを`to_node()`で解決する際の例外。カラム単位）のいずれか。どちらの場合も、対応する行は `lineage.xlsx` 側に「参照クエリ1」=「解析失敗」、「参照テーブル」「参照カラム」=「不明」として記録され、1クエリ・1カラムの失敗が他の処理を止めないようになっている。

---

## 事前検証：`validate.py`

AI変換の精度には波があるため、`main.py`本体のリネージ解析とは別に、
**変換済みSQLが構文的・スキーマ的に破綻していないか**だけを素早く判定する独立コマンド。

```bash
python3 src/validate.py
```

`main.py`と同様に `input/` 直下を自動検出して、起点クエリごとに `output/<起点クエリ名>/validation.json` を出力する。
NGが1件でもあれば終了コード1で終了する（CI等での自動チェックにそのまま使える）。

### 検知内容

`sqlglot`の構文解析と `qualify(validate_qualify_columns=True)` を使い、以下を検知する。

| 種別 | 検知方法 |
|---|---|
| 構文エラー | `sqlglot.parse(sql, dialect="tsql", error_level=ErrorLevel.RAISE)` の `ParseError` |
| 複数ステートメント | 同上のparse結果が2文以上（`converted_queries.json`の「単一SELECT文」ルール違反） |
| 未知の関数 | `exp.Anonymous` ノードの検出＝tsql方言が認識できない関数呼び出し（未変換のAccess関数の疑い。例：`Nz`の変換漏れ） |
| スキーマ不整合 | `expand_query_ast` + `qualify(validate_qualify_columns=True)` の例外（`table.json`に存在しないテーブル・カラム参照。`error.json`の`expand_sql失敗`と同じ検出経路を、本解析より前に単体で走らせている） |

```json
[
  {
    "クエリ": "テストクエリ",
    "判定": "NG",
    "問題": [
      { "種別": "未知の関数", "メッセージ": "tsql方言で認識できない関数呼び出し（未変換のAccess関数の疑い）: Nz" },
      { "種別": "スキーマ不整合", "メッセージ": "Column '名称' could not be resolved for table: 'po_発注業種'. ..." }
    ]
  }
]
```

### 限界

DBに接続して実際に実行するわけではないため、あくまで**sqlglotが文法・スキーマ整合性の面で破綻していないと判断できるか**の判定に留まる。型変換・権限等の実行時エラーまでは検知できない。

---

## `src/` のモジュール構成

```
src/
├── main.py             # エントリポイント：起点クエリの発見、チェーン検出（detect_chain）、ログ組み立て
├── validate.py           # main.pyとは別の事前検証コマンド（構文・スキーマチェック）
├── config.py            # パス関連の定数、起点クエリ（start_queries.json）の発見
├── loader.py             # input/*.json（フラットな全クエリ・全テーブル）の読み込み
├── sql_expand.py         # クエリ参照のインライン展開（AST操作。analysis.json用ログとlineage解決の両方で使う）
├── lineage_extract.py    # lineage() を辿ってフラット行を組み立てる
└── report.py             # DataFrame化とファイル出力（Excel / JSON）
```

`src/` に `__init__.py` は置いていない。`python3 src/main.py` として直接実行する前提で、
各モジュールはスクリプトと同じディレクトリにある兄弟モジュールとして素朴に import している
（Pythonが実行スクリプトのディレクトリを自動的に `sys.path` に加えるため、パッケージ化は不要）。

`input/start_queries.json` が存在しないか、起点クエリが1件も定義されていない場合、`main()` は
エラーメッセージを表示して終了コード1で終了する。

## ディレクトリ構成

```
.
├── src/
│   ├── main.py
│   ├── config.py
│   ├── loader.py
│   ├── sql_expand.py
│   ├── lineage_extract.py
│   └── report.py
├── input/
│   ├── query_dependencies.json
│   ├── table.json
│   ├── start_queries.json           # 起点クエリ名の配列
│   └── <起点クエリ名>/              # 任意。converted_queries.json を置く場合だけ作成する
├── output/                          # 生成される成果物（git管理外）
│   └── <起点クエリ名>/
│       ├── lineage.xlsx
│       ├── analysis.json
│       └── error.json
└── requirements.txt
```
