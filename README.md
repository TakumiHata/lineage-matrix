# lineage-matrix

VBA が全クエリそれぞれを起点として多階層の依存チェーンをフルに展開したもの
（`chain_queries.json`）、またはそれをAI変換した SQL Server 用 SQL
（`converted_queries.json`）を解析し、各クエリの出力カラムがどのテーブル・カラムに
由来するかをフラットテーブル形式で Excel 出力するツール。

**AI変換済みSQL Server用SQL を対象** にした、リネージ解析と影響範囲分析に特化したツール。

カラムの由来解決には [SQLGlot](https://github.com/tobymao/sqlglot) の `lineage()` を使用し、
サブクエリ・CTE・集計関数（SUM等）・クエリ間参照を含む複雑な構造にも対応する。

全クエリを個別に、代表クエリへの絞り込みや重複チェーンの除外なしにマトリックス表へ反映する
（クエリの類似度によるグルーピング・代表選出は行わない）。

## セットアップ

```bash
pip install -r requirements.txt
```

## 実行方法

```bash
python3 src/main.py
```

リポジトリルートから実行する（`input/`・`output/` は実行時の
カレントディレクトリ基準の相対パス）。
`input/table.json`（全テーブルの一覧）と `input/<起点クエリ名>/`（VBA出力の `chain_queries.json`、
AI変換済みなら `converted_queries.json`）を読み込み、起点クエリごとに `output/<起点クエリ名>/` へ結果を生成する。

起点クエリは `input/` 直下の全サブフォルダから **自動検出** される。設定ファイルは不要。

---

## `input/` のインターフェース仕様

```
input/
├── table.json                                # 全テーブルのフラットな一覧（VBAが出力）
├── クエリ工事委託分析/
│   ├── chain_queries.json                 # VBA出力（多階層フル展開済み、Access SQL）
│   └── converted_queries.json             # AI変換／SSMA変換済みSQL（あれば優先使用、オプション）
└── クエリ入札委託集計分析/
    └── chain_queries.json                 # AI変換がまだの場合はこれだけでも解析対象になる
```

**起点クエリは自動検出される**：`input/` 直下の全サブフォルダのうち、
`chain_queries.json` または `converted_queries.json` が存在するサブフォルダ名が
起点クエリとして自動的に認識される（`input/table.json`はファイルなので対象外）。
`chain_queries.json` のみ（AI変換がまだのクエリ）のフォルダも解析対象に含まれる。
設定ファイル（`start_queries.json` など）は不要。

### 運用フロー

1. **VBA実行**: 全クエリそれぞれを起点として、多階層の依存チェーンを重複除外なしにフル展開
   → `input/<起点クエリ名>/chain_queries.json` 出力
2. **AI変換**（任意）: `chain_queries.json` をAI変換サービスで SQL Server SQL に変換
3. **配置**: 変換結果を `input/<起点クエリ名>/converted_queries.json` に配置
4. **実行**: `python3 src/main.py` → 自動検出・リネージ解析 → 結果出力
   （`converted_queries.json` があればそちらを優先、なければ `chain_queries.json` を使用）

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

### `input/table.json`（テーブル情報・全件フラット）

物理テーブルのカラム定義一覧。`src/loader.py` の `load_schema()` が `sqlglot.lineage()` に渡す
スキーマ辞書に変換し、`load_table_info()` が「物理テーブル名」組み立てに使う`物理名`・`スキーマ`を
（`スキーマ取得方法`とあわせて）読み込む。

```json
[
  {
    "テーブル名": "工事台帳",
    "種別": "ローカルテーブル",
    "接続先": "",
    "物理名": "工事台帳",
    "スキーマ": "PO",
    "スキーマ取得方法": "ODBCリンクテーブル定義から自動取得",
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
| `物理名` | string | 接続先における実際のオブジェクト名。「参照物理テーブル名」列（後述）の組み立てに使用する。 |
| `スキーマ` | string（省略可） | 物理テーブルのスキーマ名。省略時は空文字扱いとなり、物理テーブル名は`物理名`のみになる。 |
| `スキーマ取得方法` | string（省略可） | スキーマ名の取得方法（例：ODBCリンクテーブル定義から自動取得）。メタデータとして保持。 |
| `カラム` | array | そのテーブルが持つカラムの配列。 |
| `カラム[].名前` | string | カラム名。 |
| `カラム[].型` | string（省略可） | カラムの型。省略時は `"text"` として扱われる。 |

**物理テーブル名の組み立て**（`src/loader.py` の `build_physical_table_name()`）：
`スキーマ`が存在する場合は `スキーマ.物理名`（例：`PO.工事台帳`）、存在しない場合は `物理名` のみ。
「テーブル参照マトリクス」シートの「参照物理テーブル名」列（後述）はこの形式で表示される。

---

## 出力：`output/<起点クエリ名>/`

フォルダごとに以下の3ファイルを生成する。

### `lineage.xlsx`

2枚のシートを持つ1つのブック。どちらも同じ `analysis_log`（後述の `analysis.json` の中身そのもの）から生成される2つのビューであり、別ロジックで再計算されたものではない。

#### シート「テーブル参照マトリクス」

クエリ階層×参照物理テーブル名のワイド形式。行は起点クエリから呼び出し可能な各クエリ（登録済みクエリのみ。ローカルな無名サブクエリは含まない）で、同じクエリが複数の親から呼び出されている場合は呼び出し元ごとに別行になる。

| 列名 | 内容 |
|---|---|
| 開始クエリ, サブクエリ1, サブクエリ2, ... | その行のクエリの、起点クエリからのパンくず（外側→内側）。列数はそのフォルダ内の最大ネスト数に応じて動的に決まる。自分自身の名前は自分の深さの列に入り、それより内側の列は空欄。 |
| 参照物理テーブル名 | その行のクエリが参照している物理テーブル名を、直接参照（○）・間接参照（◎）を区別してカンマ区切りでまとめたもの（例：`節(○), 工事台帳基本(◎)`）。物理テーブル名は`table.json`の`スキーマ`・`物理名`から組み立てた形式（`build_physical_table_name()`、前述）。 |

「参照物理テーブル名」列内の`(○)`/`(◎)`の意味：

- `(○)`：その行のクエリが、自分自身のSQL（FROM/JOIN/WHERE/GROUP BY/HAVING/ORDER BYのいずれでも）で直接参照している物理テーブル
- `(◎)`：その行のクエリ自身は直接参照していないが、呼び出しているサブクエリ（の、さらに下位のサブクエリ…）を辿ると参照しているテーブル。開始クエリ、および間に挟まる上位のサブクエリすべてに付く
- どちらでもない物理テーブルは列挙されない（該当が1件もなければ「参照物理テーブル名」は空欄）

○/◎の判定自体はPython側（`table_usage.py` / `matrix.py`）で確定させた値であり、Excelの数式では計算し直さない（後述）。

#### シート「カラム単位リネージ」

そのフォルダに含まれる各クエリについて、SELECTの出力カラムごとに1行のフラットテーブル（旧 `lineage.xlsx` 相当）。オートフィルタが有効になっており、開始クエリ・参照テーブルで絞り込んで、マトリクス側の○/◎の根拠を人手で確認できる。

| 列名 | 内容 |
|---|---|
| 開始クエリ | `クエリ名`（SQL自体をパースした対象クエリ） |
| 最終出力カラム | 開始クエリのSELECTで実際に出力されるカラム名（集計・エイリアス後の名前） |
| 参照クエリ1, 参照クエリ2, ... | 出力カラムの由来を辿る過程で経由したものを、外側→内側の順に並べたもの。2種類の要素が混在しうる：(1) 登録済みクエリ名（`source_name` 由来。FROM句で別名エイリアスを付けていても実際のクエリ名で表示される）、(2) SQL内に直接書かれたローカルな無名サブクエリのエイリアス（`reference_node_name` 由来。例：サブクエリ1）。列数はそのフォルダ内で実際に現れる最大ネスト数に応じて動的に決まる（ネストがなければ1列のみ）。直接参照の場合は `参照クエリ1` に「（直接）」、それ以外の列は空欄。 |
| 参照テーブル | 最終的に辿り着いた物理テーブル名。解決できない場合は「不明」 |
| 参照カラム | 参照テーブル上の実際のカラム名。解決できない場合は「不明」 |

ネストがどれだけ深くても最終的に行き着く先は物理テーブル1つ・カラム1つなので、動的に列展開されるのは「参照クエリ」だけで、「参照テーブル」「参照カラム」は常に単一列。

このシートはSELECT出力カラムのリネージだけを追うため、JOIN条件やWHERE句のサブクエリだけで参照されSELECT結果には現れないテーブルは載らない。それを補うのが「テーブル参照マトリクス」の○/◎判定であり、`table_usage.py` が展開前の各クエリのSQLを直接 `find_all(exp.Table)` で走査して別経路で求めている（このシートの「参照テーブル」列とは判定ロジックが異なる）。

### `analysis.json`

クエリ名ごとの解析結果そのもの。**`lineage.xlsx` の2シートはいずれもこの中身を
連結・変換して整形しただけのビュー**であり、別ロジックで再計算されたものではない
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
    ],
    "参照テーブル_直接": ["T_工事"],
    "参照テーブル_間接": []
  }
}
```

- `extract_select_columns`：そのクエリのSELECT出力カラム一覧。`SELECT *`（テーブル修飾ありの`t.*`も含む）が含まれる場合は、`sqlglot.optimizer.qualify.qualify(expand_stars=True)`（`table.json`のスキーマを使用）で実際のカラム名リストに展開してから記録する。展開先が`table.json`に登録されていない参照（他クエリへの`*`等）の場合は展開できず`"*"`のまま残る
- `expand_query_ast_repr`：クエリ参照展開後のASTの `repr()` を1行ずつ配列化したもの（`repr()` 自体はSQLGlot標準の多行整形。JSON文字列1本に詰めると改行がエスケープされて読みにくくなるため、`splitlines()` で配列にしている）
- `expand_sql`：展開後の最終的なSQLテキスト
- `lineage`：出力カラムごとのリネージ行（「カラム単位リネージ」シートの1行に対応する未整形のフラット行。「参照クエリパス」は外側→内側の順の配列で、シート側では動的に「参照クエリ1, 参照クエリ2, ...」列に展開される）
- `参照テーブル_直接` / `参照テーブル_間接`：`table_usage.resolve_table_usage()` が求めた、そのクエリ自身が直接参照する物理テーブルと、呼び出しているサブクエリ経由で間接的に参照する物理テーブル（「テーブル参照マトリクス」シートの○/◎の元データ）

`expand_query_ast()` または `qualify()` が例外を送出した場合、`expand_query_ast_repr` / `expand_sql` は `null` になり、`lineage` にはそのクエリの全出力カラム分の失敗行（`参照クエリパス: ["解析失敗"]`）が入る（`error.json`側は `expand_sql失敗` としてクエリ単位で1件のみ記録され、カラムごとに重複しない）。`参照テーブル_直接` / `参照テーブル_間接` はこの失敗とは独立した経路（`table_usage.py`）で求めているため、`expand_sql失敗` が起きたクエリでも算出される。

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

`種別` は以下のいずれか。

- `クエリ名衝突警告`：クエリ名とテーブル名が同名で衝突
- `expand_sql失敗`：`expand_query_ast`によるクエリ参照展開、または展開後の`qualify(validate_qualify_columns=True)`の例外（クエリ単位で1件のみ記録）
- `lineage失敗`：展開・qualify自体は成功した後、個別の出力カラムを`to_node()`で解決する際の例外（カラム単位）
- `循環参照警告`：`table_usage.py`/`matrix.py`がクエリの呼び出しグラフを辿る際に循環を検出（通常のAccessクエリのチェーンでは起きない想定の異常系。検出した経路の探索はそこで打ち切り、他のクエリの処理は継続する）

`expand_sql失敗`・`lineage失敗`の場合、対応する行は「カラム単位リネージ」シート側に「参照クエリ1」=「解析失敗」、「参照テーブル」「参照カラム」=「不明」として記録され、1クエリ・1カラムの失敗が他の処理を止めないようになっている。

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

## 軽量パス：`table_reference_extract.py`

マトリックス表に必要なのは「クエリ内での参照テーブル情報」（テーブル単位のみ、カラム単位は不要）であり、テーブル参照の抽出だけであれば sqlglot は関数の意味を理解する必要がなく構文的にパースできればよい。そのため `converted_queries.json`（AI変換済みT-SQL）を必須とせず、`chain_queries.json` のAccess SQL（Jet-SQL）から直接、参照テーブル一覧をテーブル単位で抽出できる軽量な代替パス。

AI変換はボトルネックであり、SSMAは`Format`関数・パラメータクエリ・クロスタブクエリを変換できないため、これらを経由せずに済む。

```bash
python3 src/table_reference_extract.py
```

`main.py`と同様に `input/` 直下を自動検出するが、**`converted_queries.json`の有無を問わず必ず`chain_queries.json`のAccess SQLを使う**（`chain_queries.json`が存在しないフォルダはスキップする）。結果は `output/table_references.json` に1ファイルへまとめて出力する。

Jet-SQL特有の以下2構文だけを正規表現で前処理して除去してから `sqlglot.parse_one(sql, read="tsql")` でパースする（`tsql`はJet-SQL自体の方言がsqlglotに存在しないため、構文的に近いものとして採用）。

- 先頭の `PARAMETERS ... ;` 宣言
- `TRANSFORM <式> SELECT ... PIVOT <式>` のクロスタブ構文（`TRANSFORM`句と`PIVOT`句を除去し、中間の`SELECT ... FROM ... GROUP BY ...`部分のみ残す）

パースした結果 `find_all(exp.Table)` で取得した各テーブル名を、`table.json`由来のテーブル名一覧・そのクエリグループ内の登録済みクエリ名一覧と突き合わせて分類する。

```json
[
  {
    "クエリ名": "Q_Test_Crosstab",
    "参照テーブル": ["T_受注明細"],
    "参照サブクエリ": [],
    "未解決": [],
    "パース失敗": false
  }
]
```

- `参照テーブル`：`table.json`に存在する物理テーブルへの参照
- `参照サブクエリ`：他の登録済みクエリへの参照（VBA側のチェーン検出で既に捕捉されている想定のため、`参照テーブル`には含めない）
- `未解決`：テーブル・クエリのどちらにも一致しない参照名（目視確認の対象）
- `パース失敗`：前処理後もsqlglotがパースできなかった場合。他のクエリの処理は止めない

カラム単位の詳細なリネージ（`lineage_extract.py` が行う出力位置ベースの解決等）は対象外。今後AI変換・SSMA変換したクエリに対して、より詳細な検証が必要になった際は `lineage_extract.py`（`main.py`経由）を使う。

---

## `src/` のモジュール構成

```
src/
├── main.py             # エントリポイント：起点クエリの発見、ログ組み立て
├── validate.py           # main.pyとは別の事前検証コマンド（構文・スキーマチェック）
├── table_reference_extract.py # main.pyとは別の軽量パス：chain_queries.jsonのAccess SQLから直接、テーブル単位の参照一覧を抽出
├── config.py            # パス関連の定数、起点クエリ（input/配下のフォルダ）の発見
├── loader.py             # input/*.json（クエリ）・input/table.json（全テーブル・物理テーブル名）の読み込み
├── casing.py             # qualify()通過後に失われる識別子の大文字小文字をschema/queriesの表記へ復元するヘルパー
├── sql_expand.py         # クエリ参照のインライン展開＋qualify検証（main.pyとvalidate.pyで共通利用）
├── lineage_extract.py    # lineage() を辿り、SELECT出力カラム単位のフラット行を組み立てる
├── table_usage.py        # クエリ単位の直接/間接の物理テーブル参照を、to_node()とは別経路（find_all(exp.Table)）で求める
├── matrix.py             # クエリ呼び出し木を辿り、クエリ階層×参照物理テーブル名のマトリックス（○/◎）を組み立てる
└── report.py             # DataFrame化とファイル出力（Excel / JSON）
```

`src/` に `__init__.py` は置いていない。`python3 src/main.py` として直接実行する前提で、
各モジュールはスクリプトと同じディレクトリにある兄弟モジュールとして素朴に import している
（Pythonが実行スクリプトのディレクトリを自動的に `sys.path` に加えるため、パッケージ化は不要）。

`input/` 直下に起点クエリのフォルダが1件も見つからない場合、`main()` は
エラーメッセージを表示して終了コード1で終了する。

## ディレクトリ構成

```
.
├── src/
│   ├── main.py
│   ├── validate.py
│   ├── table_reference_extract.py
│   ├── config.py
│   ├── loader.py
│   ├── casing.py
│   ├── sql_expand.py
│   ├── lineage_extract.py
│   ├── table_usage.py
│   ├── matrix.py
│   └── report.py
├── input/
│   ├── table.json                    # 全テーブルのフラットな一覧（VBAが出力）
│   └── <起点クエリ名>/
│       ├── chain_queries.json       # VBA出力（多階層フル展開済み、Access SQL）
│       └── converted_queries.json   # AI変換／SSMA変換済みSQL（あれば優先使用、オプション）
├── output/                          # 生成される成果物（git管理外）
│   ├── <起点クエリ名>/
│   │   ├── lineage.xlsx
│   │   ├── analysis.json
│   │   └── error.json
│   └── table_references.json       # table_reference_extract.pyの出力（全起点クエリ分をまとめて1ファイル）
└── requirements.txt
```
