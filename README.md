# lineage-matrix

Accessクエリを SQL Server 用 SQL に変換した後の SQL 群を解析し、各クエリの出力カラムが
どのテーブル・カラムに由来するかをフラットテーブル形式で Excel 出力するツール。

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
`input/query_dependencies.json`・`input/table.json`（全クエリ・全テーブルのフラットな一覧）を読み込み、
`input/` 直下のサブフォルダ名で指定された起点クエリごとに `output/<起点クエリ名>/` へ結果を生成する。

---

## `input/` のインターフェース仕様

```
input/
├── query_dependencies.json   # 全クエリのフラットな一覧（VBAスクリプト①が出力、Access SQLのまま）
├── table.json                # 全テーブルのフラットな一覧（VBAスクリプト②が出力）
├── クエリMain/                # フォルダ名が起点クエリを指定する。中身は空でよい
└── クエリ入札/                # （converted_queries.json を置く場合はこの下に置く）
```

チェーン検出（起点クエリから芋づる式にクエリ依存関係を辿ること）は、以前はVBA側が担い
起点クエリ単位のフォルダにあらかじめ切り分け済みのデータを出力していたが、現在は
lineage-matrix 側（`src/sql_expand.py` の `detect_chain()`）が sqlglot のAST解析で行う。
VBAはチェーン追跡を一切行わず、Accessの全クエリ・全テーブルをフラットに出力するだけでよい。

### `query_dependencies.json`（クエリ一覧・全件フラット）

Accessの全クエリと、そのSQL本文（Access SQLのまま、変換不要）を並べた配列。起点クエリ／親クエリの
情報は持たない（`起点クエリ`はどのクエリがどのフォルダに属するかではなく、`input/`直下の
サブフォルダ名そのもので指定される）。

```json
[
  { "クエリ名": "クエリMain",     "SQL": "SELECT ..." },
  { "クエリ名": "クエリ工事集計", "SQL": "SELECT [工事ID], SUM([発注金額]) AS [発注合計] FROM クエリ工事基本 GROUP BY [工事ID]" },
  { "クエリ名": "クエリ工事基本", "SQL": "SELECT ..." }
]
```

| フィールド | 型 | 内容 |
|---|---|---|
| `クエリ名` | string | Accessクエリ名。`src/loader.py` の `load_queries()` がこれをキーとして辞書化する。 |
| `SQL` | string | Access SQLのまま（変換不要）。`detect_chain()` はこれを `dialect="tsql"` でパースしてFROM/JOIN句のテーブル・クエリ参照を検出する（構造抽出が目的で、Access固有関数などが未変換でも通常は問題ない）。 |

**クエリ間参照はそのままでよい**：AI変換はAccess固有構文（Transform・IIF等）をSQL Server用に変換するだけで、クエリ間の参照（FROM句に他のAccessクエリ名がそのまま残る）は解決しない。この参照解決には2つの側面がある。

1. **どのクエリを起点クエリのチェーンに含めるか**：`src/sql_expand.py` の `detect_chain()` が、起点クエリから`find_all(exp.Table)`でFROM/JOIN句の参照を再帰的に辿り、全クエリ名の集合（`query_name_set`）と照合してクエリ参照かテーブル参照かを判別する。訪問済み集合を持つため循環参照があっても無限ループにはならない。
2. **リネージ解決時のインライン展開**：独自のAST操作ではなく、`sqlglot.lineage()` 標準の `sources=` 引数（内部で `sqlglot.expressions.expand()` を呼ぶ）に `クエリ名 → SQL` の辞書をそのまま渡すことで行っている（`src/lineage_extract.py`）。`exp.expand()` は展開したサブクエリに `/* source: 元のクエリ名 */` というコメントを自動付与し、`lineage()` はこれを検出して各ノードの `source_name` にセットする。この仕組みにより、FROM句で `AS 任意のエイリアス` のように別名を付けていても、そのホップが実際にどの登録済みクエリの内部かをテキスト一致や独自ロジックなしで判定できる（`src/sql_expand.py` の `expand_query_ast()` は `analysis.json` 用のログ生成にのみ使用し、実際のリネージ解決には使っていない）。

例えば「クエリ工事集計」は `FROM クエリ工事基本` とだけ書けばよく、`クエリ工事基本` の中身をあらかじめサブクエリとして埋め込んでおく必要はない。

### `converted_queries.json`（AI変換済みSQLの任意配置・起点クエリごと）

`detect_chain()` はAccess SQLのままでもFROM/JOIN句の構造抽出はできるが、`sqlglot.lineage()` による
本格的なカラム単位のリネージ解決には、AI変換済みのSQL Server用SQLの方が正確な結果になる。
そのためのワークフローとして：

1. `python3 src/main.py` を実行すると、チェーン検出で特定した各起点クエリのクエリ一覧が
   `output/<起点クエリ名>/chain_queries.json`（Access SQLのまま）として出力される。
2. これをAI変換にかけ、結果を `input/<起点クエリ名>/converted_queries.json`
   （`chain_queries.json` と同じ `[{ "クエリ名": ..., "SQL": ... }, ...]` 形式）として配置する。
3. 再度 `python3 src/main.py` を実行すると、`src/main.py` の `resolve_queries_for_analysis()` が
   このファイルの存在を検知し、リネージ解析（`sql_expand`→`lineage_extract`）に使うSQLとして
   優先的に読み込む。存在しなければ、従来通りチェーン検出結果のSQLがそのまま使われる。

チェーン検出自体（`detect_chain()`）は常に `input/query_dependencies.json` の内容で行われ、
`converted_queries.json` の有無には影響されない。

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
| `テーブル名` | string | Accessクエリ上で参照される論理名。`lineage()` のスキーマ辞書のキーになる。また `find_query_table_collisions()` がクエリ名とテーブル名の衝突を検知する際にも使われる。 |
| `種別` | string | 「ローカルテーブル」／「リンクテーブル」等。現状のパイプラインでは未使用（メタデータとして保持のみ）。 |
| `接続先` | string | リンクテーブルの接続先ファイルパス等。現状のパイプラインでは未使用。 |
| `物理名` | string | 接続先における実際のオブジェクト名。現状のパイプラインでは未使用（`テーブル名`と異なる場合がある）。 |
| `カラム` | array | そのテーブルが持つカラムの配列。 |
| `カラム[].名前` | string | カラム名。 |
| `カラム[].型` | string（省略可） | カラムの型。**省略時は `"text"` として扱われる**。型の値自体は突合には使われず、`lineage()` に「このテーブルにこのカラムが存在する」ことを伝えるためだけに使われる。 |

**注意点（大文字小文字）**：T-SQLは識別子の大文字小文字を区別しないため、`lineage()` がブラケットなしの識別子を解決する際、ASCII文字を含む名前（例：`工事ID`、`クエリ工事CTE集計`）が内部的に小文字化されることがある（`工事id`、`クエリ工事cte集計`）。`src/lineage_extract.py` の `restore_schema_casing()` / `restore_query_casing()` がこれを検知し、`table.json` / `query_dependencies.json` に定義された正しい表記に復元してから出力する。

---

## 出力：`output/<起点クエリ名>/`

フォルダごとに以下の5ファイルを生成する。

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

### `table_usage.xlsx`

`lineage.xlsx`はSELECT出力カラムの由来だけを辿るため、WHERE句やJOIN条件だけで参照されSELECT結果には一切現れないテーブル（例：`WHERE 工事ID IN (SELECT 工事ID FROM 発注台帳 WHERE ...)`のようなフィルタ条件にのみ使われるテーブル）が漏れてしまう。このファイルはそれを補い、各クエリが実際に触れている物理テーブルを漏れなく記録する。

| 列名 | 内容 |
|---|---|
| 開始クエリ | `クエリ名` |
| 参照テーブル | そのクエリのSQLで実際に参照されている物理テーブル名（SELECT/WHERE/JOIN/GROUP BY/HAVING/ORDER BYを問わない） |

クエリ参照（他クエリをFROM句で呼んでいる箇所）は展開済みのASTに対して`find_all(exp.Table)`するため、登録済みクエリ名ではなく物理テーブル名のみが残る。またWITH句のCTE名も`exp.Table`として現れるため、`exp.CTE`の別名と一致するものは物理テーブルではないとして除外している。

### `chain_queries.json`

`detect_chain()`が起点クエリから辿り着いた全クエリの一覧（Access SQLのまま、変換不要）。

```json
[
  { "クエリ名": "クエリMain",     "SQL": "SELECT ..." },
  { "クエリ名": "クエリ工事集計", "SQL": "SELECT ..." }
]
```

このファイルをAI変換にかけ、結果を `input/<起点クエリ名>/converted_queries.json` として
配置すると、次回実行時のリネージ解析にそちらが優先して使われる（詳細は前述の
「`converted_queries.json`」の節を参照）。

### `analysis.json`

クエリ名ごとの解析情報（デバッグ・検証用）。

```json
{
  "クエリ工事集計": {
    "extract_select_columns": ["工事ID", "工事名称", "発注合計"],
    "expand_query_ast_repr": ["Select(", "  expressions=[...", "..."],
    "expand_sql": "SELECT [工事ID], ... FROM (SELECT ...) AS [クエリ工事基本] GROUP BY ..."
  }
}
```

- `extract_select_columns`：そのクエリのSELECT出力カラム一覧。`SELECT *`（テーブル修飾ありの`t.*`も含む）が含まれる場合は、`sqlglot.optimizer.qualify.qualify(expand_stars=True)`（`table.json`のスキーマを使用）で実際のカラム名リストに展開してから記録する。展開先が`table.json`に登録されていない参照（他クエリへの`*`等）の場合は展開できず`"*"`のまま残る
- `expand_query_ast_repr`：クエリ参照展開後のASTの `repr()` を1行ずつ配列化したもの（`repr()` 自体はSQLGlot標準の多行整形。JSON文字列1本に詰めると改行がエスケープされて読みにくくなるため、`splitlines()` で配列にしている）
- `expand_sql`：展開後の最終的なSQLテキスト

`expand_query_ast()` が例外を送出した場合は `expand_query_ast_repr` / `expand_sql` が `null` になる。

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

`種別` は `クエリ名衝突警告`（クエリ名とテーブル名が同名で衝突）・`expand_sql失敗`（クエリ参照展開自体の例外）・`lineage失敗`（`sqlglot.lineage()` 呼び出しの例外、列単位）のいずれか。`lineage失敗`の場合、対応する行は `lineage.xlsx` 側にも「参照クエリ1」=「解析失敗」、「参照テーブル」「参照カラム」=「不明」として記録され、1クエリ・1カラムの失敗が他の処理を止めないようになっている。

---

## `src/` のモジュール構成

```
src/
├── main.py             # エントリポイント：起点クエリの発見、チェーン検出（detect_chain）、ログ組み立て
├── config.py            # パス関連の定数、起点クエリ（サブフォルダ名）の発見
├── loader.py             # input/*.json（フラットな全クエリ・全テーブル）の読み込み
├── sql_expand.py         # クエリ参照のインライン展開（AST操作、analysis.json用ログ生成）
├── lineage_extract.py    # lineage() を辿ってフラット行を組み立てる
└── report.py             # DataFrame化とファイル出力（Excel / JSON）
```

`src/` に `__init__.py` は置いていない。`python3 src/main.py` として直接実行する前提で、
各モジュールはスクリプトと同じディレクトリにある兄弟モジュールとして素朴に import している
（Pythonが実行スクリプトのディレクトリを自動的に `sys.path` に加えるため、パッケージ化は不要）。

起点クエリのサブフォルダ（`input/<起点クエリ名>/`）が1つも見つからない場合、`main()` はエラー
メッセージを表示して終了コード1で終了する。

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
│   └── <起点クエリ名>/              # 中身は空でよい（converted_queries.json を置く場合はここ）
├── output/                          # 生成される成果物（git管理外）
│   └── <起点クエリ名>/
│       ├── lineage.xlsx
│       ├── table_usage.xlsx
│       ├── chain_queries.json
│       ├── analysis.json
│       └── error.json
└── requirements.txt
```
