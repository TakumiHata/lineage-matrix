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
`input/` 配下の各グループフォルダを読み込み、`output/<起点クエリ名>/` にグループごとの結果を生成する。

---

## `input/` のインターフェース仕様

`input/` は**起点クエリ**（クエリ連鎖の一番外側のクエリ）ごとにフォルダ分けされている。
1フォルダ = VBAが1回のエクスポート操作で出力する単位に対応し、それぞれ独立して処理される
（フォルダ間で内容を突き合わせたり重複を除去したりはしない。同じクエリ定義が
複数のフォルダに重複して存在していても問題ない）。

```
input/
├── クエリA/
│   ├── query_dependencies.json
│   └── table.json
└── クエリMain/
    ├── query_dependencies.json
    └── table.json
```

### `query_dependencies.json`（クエリ一覧）

その起点クエリから芋づる式に辿った関連クエリ一式をまとめた配列。

```json
[
  {
    "クエリ名": "クエリMain",
    "起点クエリ": "クエリMain",
    "親クエリ": null,
    "SQL": "SELECT ..."
  },
  {
    "クエリ名": "クエリ工事集計",
    "起点クエリ": "クエリMain",
    "親クエリ": "クエリMain",
    "SQL": "SELECT [工事ID], SUM([発注金額]) AS [発注合計] FROM クエリ工事基本 GROUP BY [工事ID]"
  }
]
```

| フィールド | 型 | 内容 |
|---|---|---|
| `クエリ名` | string | Accessクエリ名。`src/loader.py` の `load_queries()` がこれをキーとして辞書化する。 |
| `起点クエリ` | string | この連鎖の一番外側のクエリ名。現状のパイプラインでは未使用（メタデータとして保持のみ、フォルダ名と一致させる）。 |
| `親クエリ` | string \| null | このクエリをFROM句で参照している親クエリ名。独立クエリ、または連鎖の最上位クエリは `null`。現状のパイプラインでは未使用（メタデータとして保持のみ）。 |
| `SQL` | string | AI変換済みのSQL Server用SQL（tsqlダイアレクトでパース）。 |

**クエリ間参照はそのままでよい**：AI変換はAccess固有構文（Transform・IIF等）をSQL Server用に変換するだけで、クエリ間の参照（FROM句に他のAccessクエリ名がそのまま残る）は解決しない。この展開は独自のAST操作ではなく、`sqlglot.lineage()` 標準の `sources=` 引数（内部で `sqlglot.expressions.expand()` を呼ぶ）に `クエリ名 → SQL` の辞書をそのまま渡すことで行っている（`src/lineage_extract.py`）。`exp.expand()` は展開したサブクエリに `/* source: 元のクエリ名 */` というコメントを自動付与し、`lineage()` はこれを検出して各ノードの `source_name` にセットする。この仕組みにより、FROM句で `AS 任意のエイリアス` のように別名を付けていても、そのホップが実際にどの登録済みクエリの内部かをテキスト一致や独自ロジックなしで判定できる（`src/sql_expand.py` の `expand_query_ast()`/`expand_sql()` は `analysis.json` 用のログ生成にのみ使用し、実際のリネージ解決には使っていない）。

例えば「クエリ工事集計」は `FROM クエリ工事基本` とだけ書けばよく、`クエリ工事基本` の中身をあらかじめサブクエリとして埋め込んでおく必要はない（`lineage()` が `sources=` 経由で自動的に展開する）。

### `table.json`（テーブル情報）

物理テーブルのカラム定義一覧。`src/loader.py` の `load_schema()` が `sqlglot.lineage()` に渡すスキーマ辞書に変換する。

```json
[
  {
    "テーブル名": "工事台帳",
    "カラム": [
      { "名前": "工事ID",   "型": "int" },
      { "名前": "工事名称", "型": "varchar" }
    ]
  }
]
```

| フィールド | 型 | 内容 |
|---|---|---|
| `テーブル名` | string | 物理テーブル名。`lineage()` のスキーマ辞書のキーになる。また `find_query_table_collisions()` がクエリ名とテーブル名の衝突を検知する際にも使われる。 |
| `カラム` | array | そのテーブルが持つカラムの配列。 |
| `カラム[].名前` | string | カラム名。 |
| `カラム[].型` | string（省略可） | カラムの型。**省略時は `"text"` として扱われる**。型の値自体は突合には使われず、`lineage()` に「このテーブルにこのカラムが存在する」ことを伝えるためだけに使われる。 |

**注意点（大文字小文字）**：T-SQLは識別子の大文字小文字を区別しないため、`lineage()` がブラケットなしの識別子を解決する際、ASCII文字を含む名前（例：`工事ID`、`クエリ工事CTE集計`）が内部的に小文字化されることがある（`工事id`、`クエリ工事cte集計`）。`src/lineage_extract.py` の `restore_schema_casing()` / `restore_query_casing()` がこれを検知し、`table.json` / `query_dependencies.json` に定義された正しい表記に復元してから出力する。

---

## 出力：`output/<起点クエリ名>/`

フォルダごとに以下の3ファイルを生成する。

### `lineage.xlsx`

そのフォルダに含まれる各クエリについて、SELECTの出力カラムごとに1行のフラットテーブル。

| 列名 | 内容 |
|---|---|
| 開始クエリ | `クエリ名`（SQL自体をパースした対象クエリ） |
| 最終出力カラム | 開始クエリのSELECTで実際に出力されるカラム名（集計・エイリアス後の名前） |
| 参照クエリ1, 参照クエリ2, ... | 出力カラムの由来を辿る過程で経由したものを、外側→内側の順に並べたもの。2種類の要素が混在しうる：(1) 登録済みクエリ名（`source_name` 由来。FROM句で別名エイリアスを付けていても実際のクエリ名で表示される）、(2) SQL内に直接書かれたローカルな無名サブクエリのエイリアス（`reference_node_name` 由来。例：サブクエリ1）。列数はそのフォルダ内で実際に現れる最大ネスト数に応じて動的に決まる（ネストがなければ1列のみ）。直接参照の場合は `参照クエリ1` に「（直接）」、それ以外の列は空欄。 |
| 参照テーブル | 最終的に辿り着いた物理テーブル名。解決できない場合は「不明」 |
| 参照カラム | 参照テーブル上の実際のカラム名。解決できない場合は「不明」 |

ネストがどれだけ深くても最終的に行き着く先は物理テーブル1つ・カラム1つなので、動的に列展開されるのは「参照クエリ」だけで、「参照テーブル」「参照カラム」は常に単一列。

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

- `extract_select_columns`：そのクエリのSELECT出力カラム一覧
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
├── main.py             # エントリポイント：グループの発見とループ、ログ組み立て
├── config.py            # パス関連の定数（INPUT_DIR, OUTPUT_DIR, ファイル名）
├── loader.py             # input/<起点クエリ>/ の JSON 読み込み
├── sql_expand.py         # クエリ参照のインライン展開（AST操作）
├── lineage_extract.py    # lineage() を辿ってフラット行を組み立てる
└── report.py             # DataFrame化とファイル出力（Excel / JSON）
```

`src/` に `__init__.py` は置いていない。`python3 src/main.py` として直接実行する前提で、
各モジュールはスクリプトと同じディレクトリにある兄弟モジュールとして素朴に import している
（Pythonが実行スクリプトのディレクトリを自動的に `sys.path` に加えるため、パッケージ化は不要）。

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
│   └── <起点クエリ名>/
│       ├── query_dependencies.json
│       └── table.json
├── output/                          # 生成される成果物（git管理外）
│   └── <起点クエリ名>/
│       ├── lineage.xlsx
│       ├── analysis.json
│       └── error.json
└── requirements.txt
```
