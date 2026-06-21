# Bake Groups - ユーザーマニュアル

## 1. ツールの役割

Bake Groups は、Maya でのベイク準備と最終 FBX 書き出しのために、HP/LP ジオメトリを整理するツールです。

アーティスト向けに言うと、このツールはシーンを明確なベイクグループに整理するためのものです。ハイポリとローポリを対応付け、書き出し対象の HP ジオメトリに Smooth を適用し、ZBrush メッシュを除外し、正しい名前の最終三角化済み Low メッシュを作成し、Marmoset が HP/LP ペアを自動的に認識できるように書き出します。

主なワークフロー:

```text
Prepare scene -> Create Pair -> Find ZBrush -> Analyze HP
-> adjust subgroups -> Assign LP -> Combine Fin -> Final Group -> Export
```

## 2. 用語

### HP / High Poly

ノーマル、カーブチャ、その他のマップをベイクするために使用する詳細なジオメトリです。ツール内では HP ルートの中に配置されます。

### LP / Low Poly

ゲーム用、または最終用のローポリジオメトリです。ツール内では LP ルートの中に配置され、後で最終的な `_low` メッシュに結合されます。

### HP Root と LP Root

ハイポリジオメトリとローポリジオメトリを格納するメイングループです。ペア作成後、ツールはそれらの名前を次の形式に変更します。

```text
<ChapterName>_HP
<ChapterName>_LP
```

### Chapter

1 つの作業用 HP/LP ペアです。通常は 1 つのアセット、1 つのモデルセクション、または論理的にまとまったパーツグループを指します。

### Book

`TABLE OF CONTENTS` 内の Chapter のセットです。Book は一括書き出しに使用されます。

### Subgroup

HP ルートと LP ルートの中にある、対応付けられたグループです。例:

```text
Bolts_001_HP
Bolts_001_LP
```

`Bolts_001_HP` 内の HP は、`Bolts_001_LP` 内の LP に対してベイクされる想定です。

### ZBrush Mesh

ZBrush、または類似ソフトから来た重い三角化済み HP ジオメトリです。通常のサブディビジョン HP のように Smooth を適用すべきではありません。本物の ZBrush メッシュは、名前に `zbrush` を含む表示レイヤーに入れてください。

### Final Group

`Combine Fin` 後の最終確認モードです。ここでは最終グループを確認し、Smooth レベルを設定して書き出します。

## 3. 起動

インストール後、`BAKE GROUPS` シェルフボタンをクリックします。メインウィンドウが開きます。

右下のボタン:

- `Save` - セッションを保存します。
- `Load` - セッションを読み込みます。
- `Language` - インターフェース言語を選択します。

選択した言語は Maya のプリファレンスに保存され、次回起動時に復元されます。

## 4. 最初にシーンを準備する

解析前に、必ず次を行ってください。

1. Maya の `Mesh > Separate`、または `Separate` ボタンで複合メッシュを分割します。
2. `Modify > Freeze Transformations` でトランスフォームをフリーズします。

実用上のルール:

```text
1 つの物理パーツ = 1 つの独立した transform メッシュ。
Translate = 0, Rotate = 0, Scale = 1。
```

これは重要です。`Analyze HP` と `Assign LP` は、各 transform の実際のサイズ、位置、バウンディングボックスに依存しているためです。

## 5. 作業用 Chapter を作成する

1. HP ルートを選択して `Pick HP` をクリックします。
2. LP ルートを選択して `Pick LP` をクリックします。
3. `Create Pair` をクリックします。

ツールは Chapter を作成し、ルート名を `<ChapterName>_HP` と `<ChapterName>_LP` に変更し、その Chapter を `TABLE OF CONTENTS` に追加し、ルート内のメッシュを準備します。

HP 階層がすでに手動で整理されている場合は、ペア作成前または解析前に `Keep HP` を有効にしてください。`Keep HP` がオフの場合、ツールは HP 構造をフラット化して再構築することがあります。

## 6. 表示とナビゲーション

### TABLE OF CONTENTS

右側のパネルには Chapter と Book が表示されます。Chapter をクリックするとそれがアクティブになり、左側にその Subgroup が表示されます。

### Auto-Isolate

`Auto-Isolate` は、アクティブな Chapter を Maya ビューポート内で自動的にアイソレートします。

### HP Visible / HP Hidden

アクティブな Chapter の HP ルートを表示または非表示にします。

### LP Visible / LP Hidden

LP ルートを表示または非表示にします。`Final Group` モードでは、これは最終結合済み Low メッシュを制御し、`Low Visible / Low Hidden` になります。

### Groups Vis / Groups Hidden

アクティブな Chapter 内のすべての Subgroup を表示または非表示にします。

## 7. ZBrush ジオメトリを検出する

`Find ZBrush` をクリックします。ツールは、三角面の割合が高いアクティブ Chapter 内の HP メッシュを選択します。

`Find ZBrush` を右クリックすると、`Triangular faces` しきい値スライダーが開きます。デフォルトは 50% です。コンテキストメニューには `Find ZBrush now` もあります。

重要:

- `Find ZBrush` は候補を選択するだけです。
- 表示レイヤーのリンクは変更されません。
- 本物の ZBrush メッシュは、名前に `zbrush` を含む表示レイヤーへ手動で配置する必要があります。

書き出し時、ZBrush 表示レイヤー内のメッシュには Smooth が適用されません。

## 8. アルゴリズム設定

`Algorithm` セクションは、`Analyze HP` が HP メッシュをどのようにグループ化するかを制御します。

主な設定:

- `HP Clustering Strategy` - グループ化戦略。
- `Spatial Volume Match` - 体積と位置によるグループ化。
- `PCA Shape Alignment` - 形状と向きの比較。
- `Topology Fingerprint` - トポロジ比較。
- `Calculate Symmetry Score (.pyd)` - C++ コアを使った対称性スコア計算。
- `HP Collision (%)` - 許容される重なり量。
- `HP Link Vtx` と `HP Link Dist (%)` - 近接頂点による複合 HP 要素の検出。
- `Ignore Floaters` - 穴のある小さなメッシュが誤って大きなグループに引き込まれる場合に、フローター/デカール処理をスキップします。
- `Bolt Elongation (<)`, `Bolt Symmetry (<)`, `Wire Elongation (>)` - ボルトとワイヤー用のヒューリスティック。

通常の作業では、デフォルト設定で十分です。

## 9. Analyze HP

`Analyze HP` をクリックする前に、次を確認してください。

- メッシュが `Separate` または `Mesh > Separate` で分割されている。
- トランスフォームがフリーズされている。
- ZBrush メッシュが ZBrush 表示レイヤーに入っている。
- 正しい Chapter がアクティブになっている。

`Analyze HP` は HP を解析し、Subgroup を作成します。開始時に、ツールは ZBrush 候補の可能性をチェックします。ZBrush のように見えるメッシュが ZBrush レイヤーに入っていない場合、警告を表示します。

- `Skip` - 解析を続行します。
- `Select` - 疑わしいメッシュを選択し、解析を停止します。

通常は `Select` をクリックし、メッシュを確認して、本物の ZBrush ジオメトリを正しいレイヤーに配置するのがおすすめです。

穴のある小さなディテール、フローター、デカールが誤ったグループに入る場合は、`Algorithm` で `Ignore Floaters` を有効にして、もう一度 `Analyze HP` を実行してください。これにより、そのようなメッシュを近くの大きな HP パーツへ自動的に接続する処理だけをスキップし、それ以外の HP 解析は有効なままになります。

## 10. Subgroup の確認と編集

解析後、左側のパネルに Subgroup リストが表示されます。

各 Subgroup 行には次があります。

- `Vis` - Subgroup を表示または非表示にします。
- Subgroup 名 - ダブルクリックで内容を選択し、右クリックで名前を変更します。
- `Add` - 選択したメッシュをこの Subgroup に追加します。
- ロックアイコン - Subgroup が再構築されないよう保護します。
- `X` - Subgroup を削除します。

### Create Group

`Algorithm` 内の `Group name` と `Create Group` を使用して、対応する HP/LP Subgroup を手動で作成できます。

### Add To Active

`Add` は、選択したメッシュをアクティブな Subgroup に移動します。解析後の手動クリーンアップに使用します。

## 11. Assign LP

HP Subgroup が正しく見えるようになったら、`Assign LP` をクリックします。

ツールは LP メッシュを解析し、HP 構造に基づいて LP Subgroup へ割り当てます。シーン準備がきれいで、HP グループが正確なほど、結果は安定します。

LP が正しく割り当てられない場合は、次を確認してください。

- `Analyze HP` が実行済みか。
- HP Subgroup が空ではないか。
- LP メッシュが対応する HP ジオメトリの近くに配置されているか。
- トランスフォームがフリーズされているか。

## 12. 手動類似検索

### Find Sim

`Find Sim` は、レイアウトを考慮して類似メッシュを検索します。繰り返し要素に使用します。

### Find All

`Find All` はより広範囲に検索し、レイアウトへの依存が少なくなります。

## 13. GT Matcher

右上のパネルは、複雑な HP/LP マッチングを手動で解決するためのものです。

主なコントロール:

- `Find LP Groups` - 可能性のある LP グループを検索します。
- `Tolerance (%)` - 検索許容値。
- `Min HP/LP` - 最小マッチ数。
- `Strict Geo Check (Resolve Overlaps)` - 厳密な重なりチェック。
- `Link` - 選択したマッチを保存します。
- `Unlink` - マッチを削除します。
- `New` - カスタム手動リンクを作成します。
- `Relocate HP` - 保存済みリンクに従って HP を移動します。

## 14. Combine Fin

HP と LP の Subgroup が準備できたら、`Combine Fin` をクリックします。

ツールは次を行います。

- 最終書き出し用に HP の名前を変更します。
- LP Subgroup を最終 `_low` メッシュに結合します。
- 最終 Low メッシュを三角化します。
- 結合済み Low メッシュを最終構造に配置します。

`Combine Fin` 後、最終 Low メッシュが非表示になることがあります。`Final Group` モードでは、これらは `Low Visible / Low Hidden` によって制御されます。

完了メッセージに `LP 0` と表示される場合、最終 Low メッシュは作成されていません。通常は `Assign LP` が実行されていないか、LP Subgroup が空です。

## 15. Final Group

`Final Group` をクリックすると、最終確認モードに入ります。

このモードでは次のようになります。

- `Smooth View` と `Export` が表示されます。
- `LP Visible / LP Hidden` は最終結合済み Low メッシュを制御します。
- Subgroup 行には HP Smooth レベルが表示されます。

`No final meshes. Run Combine Fin first.` と表示される場合は、戻って `Combine Fin` を実行してください。

`Back` は通常の Subgroup リストへ戻ります。

## 16. Smooth レベル

`Final Group` の各行には Smooth ドロップダウンがあります。

- `Smooth 0` - Smooth なし。
- `Smooth 1` - 軽い Smooth。
- `Smooth 2` - 標準 Smooth。
- `Smooth 3` - 強い Smooth。

`+` と `-` ボタンで Smooth レベルを増減できます。

`Smooth View` はビューポートで Smooth をプレビューします。これはプレビューのみです。書き出し時には再度 Smooth が適用され、その後ロールバックされます。ZBrush レイヤー内の ZBrush ジオメトリはスキップされます。

## 17. Chapter の書き出し

`Export` を通常クリックすると、アクティブな Chapter が書き出されます。

書き出し前に、次を確認してください。

- `Combine Fin` が実行済み。
- `Final Group` モードに入っている。
- Smooth レベルが設定されている。
- ZBrush メッシュが ZBrush 表示レイヤーに入っている。

HP 書き出しでは、通常の HP ジオメトリに Smooth を適用し、ZBrush レイヤー内のジオメトリはスキップし、トランスフォームをゼロ化した状態で HP を書き出します。LP 書き出しでは、最終三角化済み `_low` メッシュを使用します。

## 18. Export メニュー

`Export` を右クリックすると、書き出しオプションが開きます。

- `Export Book -> Separate HP and LP` - Book 内のすべての Chapter を、HP/LP 別ファイルとして一括書き出しします。
- `Export Book -> HP+LP single file` - Chapter ごとに HP と LP を 1 つの FBX にまとめて一括書き出しします。
- `Export LP` - LP のみを書き出します。
- `Export HP` - HP のみを書き出します。

LP 書き出しで結合済み LP メッシュが見つからないと表示される場合は、先に `Combine Fin` を実行してください。

## 19. Books

`TABLE OF CONTENTS` を使って、Chapter を Book に整理します。

コンテキストメニューでは次ができます。

- Book を作成する。
- Book の名前を変更する。
- `Add to` - 選択した Chapter を Book に追加する。
- `Extract from the book` - Chapter を Book から削除する。
- `Delete Selection` - リスト項目を削除する。

目のアイコンは、ビューポート内で Chapter または Book を表示/非表示にします。

## 20. セッション

### Save

`Save` は現在の Bake Groups セッションを保存します。一部のデータは自動保存されますが、大きな編集後には手動保存が便利です。

### Load

`Load` は JSON セッションを読み込みます。生の Chapter リストと、`pairs` キーを持つファイルの両方に対応しています。

## 21. よくある問題

### Analyze HP が変なグループを作成する

メッシュが `Separate` で分割されていること、トランスフォームがフリーズされていること、ZBrush メッシュが ZBrush レイヤーに入っていることを確認してください。HP がすでに手動で整理されている場合は、`Keep HP` を使用してください。

### ZBrush ジオメトリに Smooth が適用された

表示レイヤーを確認してください。Smooth をスキップするには、レイヤー名に `zbrush` が含まれている必要があります。

### ZBrush 名の Subgroup 内にある通常 HP に Smooth が適用されなかった

Smooth は ZBrush 表示レイヤーによってのみスキップされます。通常 HP に Smooth が適用されない場合は、それが誤って ZBrush レイヤーに入っていないか確認してください。

### Find ZBrush がすべてを検出しない

`Find ZBrush` を右クリックし、`Triangular faces` しきい値を下げてください。

### Assign LP が遅い

大きなシーンでは正常な場合があります。再実行する前に、LP が準備済みで、一時メッシュが削除されていることを確認してください。

### Final Group が空

先に `Combine Fin` を実行してください。`Final Group` は結合ステップ後の最終データのみを表示します。

## 22. クイックチェックリスト

```text
Separate
Freeze Transformations
Pick HP
Pick LP
Create Pair
Find ZBrush
Analyze HP
Review subgroups
Assign LP
Combine Fin
Final Group
Smooth View
Export
```
