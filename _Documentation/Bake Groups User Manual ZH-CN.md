# Bake Groups - 用户手册

## 1. 工具用途

Bake Groups 用于在 Maya 中为烘焙和最终 FBX 导出准备 HP/LP 几何体。

从美术实际工作角度来说，这个工具可以帮助你把场景整理成清晰的烘焙组：将高模与低模匹配，对导出的 HP 几何体应用 Smooth，跳过 ZBrush 网格，生成名称正确的最终三角化 Low 网格，并将它们导出，使 Marmoset 能自动识别 HP/LP 配对。

主要流程:

```text
Prepare scene -> Create Pair -> Find ZBrush -> Analyze HP
-> adjust subgroups -> Assign LP -> Combine Fin -> Final Group -> Export
```

## 2. 术语

### HP / High Poly

用于烘焙法线、曲率以及其他贴图的高细节几何体。在工具中，它位于 HP 根组内。

### LP / Low Poly

游戏就绪或最终使用的低模几何体。在工具中，它位于 LP 根组内，之后会被合并为最终的 `_low` 网格。

### HP Root 和 LP Root

用于存放高模和低模几何体的主组。创建配对后，工具会将它们重命名为:

```text
<ChapterName>_HP
<ChapterName>_LP
```

### Chapter

一个工作用的 HP/LP 配对。通常对应一个资产、一个模型部分，或一组逻辑上相关的零件。

### Book

`TABLE OF CONTENTS` 中的一组 Chapter。Book 用于批量导出。

### Subgroup

HP 根组和 LP 根组内部的一组配对组。例如:

```text
Bolts_001_HP
Bolts_001_LP
```

`Bolts_001_HP` 中的 HP 应该与 `Bolts_001_LP` 中的 LP 进行烘焙匹配。

### ZBrush Mesh

来自 ZBrush 或类似软件的重型三角化 HP 几何体。它通常不应该像普通细分 HP 一样被 Smooth。请将真正的 ZBrush 网格放入名称包含 `zbrush` 的显示层中。

### Final Group

`Combine Fin` 后的最终检查模式。在这里你可以检查最终组、设置 Smooth 级别并进行导出。

## 3. 启动

安装后，点击 `BAKE GROUPS` shelf 按钮。主窗口会打开。

右下角按钮:

- `Save` - 保存会话；
- `Load` - 加载会话；
- `Language` - 选择界面语言。

所选语言会保存到 Maya 首选项中，并在下次启动时恢复。

## 4. 先准备场景

在分析之前，请务必执行以下操作:

1. 使用 Maya 的 `Mesh > Separate` 或 `Separate` 按钮拆分复合网格。
2. 使用 `Modify > Freeze Transformations` 冻结变换。

实用规则:

```text
一个物理零件 = 一个独立的 transform 网格。
Translate = 0, Rotate = 0, Scale = 1。
```

这很重要，因为 `Analyze HP` 和 `Assign LP` 依赖每个 transform 的真实尺寸、位置和包围盒。

## 5. 创建工作 Chapter

1. 选择 HP 根组并点击 `Pick HP`。
2. 选择 LP 根组并点击 `Pick LP`。
3. 点击 `Create Pair`。

工具会创建一个 Chapter，将根组重命名为 `<ChapterName>_HP` 和 `<ChapterName>_LP`，把该 Chapter 添加到 `TABLE OF CONTENTS`，并准备根组内的网格。

如果你的 HP 层级已经手动整理好了，请在创建配对前或分析前启用 `Keep HP`。如果 `Keep HP` 关闭，工具可能会展平并重建 HP 结构。

## 6. 可见性与导航

### TABLE OF CONTENTS

右侧面板列出 Chapter 和 Book。点击某个 Chapter 会激活它，并在左侧显示它的 Subgroup。

### Auto-Isolate

`Auto-Isolate` 会在 Maya 视口中自动隔离当前激活的 Chapter。

### HP Visible / HP Hidden

显示或隐藏当前激活 Chapter 的 HP 根组。

### LP Visible / LP Hidden

显示或隐藏 LP 根组。在 `Final Group` 模式下，它会控制最终合并后的 Low 网格，并变为 `Low Visible / Low Hidden`。

### Groups Vis / Groups Hidden

显示或隐藏当前激活 Chapter 中的所有 Subgroup。

## 7. 查找 ZBrush 几何体

点击 `Find ZBrush`。工具会在当前激活的 Chapter 中选择三角面比例较高的 HP 网格。

右键点击 `Find ZBrush` 可打开 `Triangular faces` 阈值滑块。默认值为 50%。右键菜单中也包含 `Find ZBrush now`。

重要:

- `Find ZBrush` 只会选择候选对象；
- 不会更改显示层链接；
- 真正的 ZBrush 网格必须手动放入名称包含 `zbrush` 的显示层。

导出时，位于 ZBrush 显示层中的网格会跳过 Smooth。

## 8. 算法设置

`Algorithm` 区域控制 `Analyze HP` 如何对 HP 网格进行分组。

主要设置:

- `HP Clustering Strategy` - 分组策略；
- `Spatial Volume Match` - 按体积和位置分组；
- `PCA Shape Alignment` - 形状和方向比较；
- `Topology Fingerprint` - 拓扑比较；
- `Calculate Symmetry Score (.pyd)` - 使用 C++ 核心计算对称性分数；
- `HP Collision (%)` - 允许的重叠比例；
- `HP Link Vtx` 和 `HP Link Dist (%)` - 通过邻近顶点检测复合 HP 元素；
- `Ignore Floaters` - 当带孔小网格被错误吸附到较大的组中时，跳过 floater/decal 处理；
- `Bolt Elongation (<)`, `Bolt Symmetry (<)`, `Wire Elongation (>)` - 螺栓和线缆启发式规则。

默认设置通常足以应对正常工作。

## 9. Analyze HP

点击 `Analyze HP` 前，请检查:

- 网格已使用 `Separate` 或 `Mesh > Separate` 拆分；
- 变换已冻结；
- ZBrush 网格已放入 ZBrush 显示层；
- 当前激活的是正确的 Chapter。

`Analyze HP` 会分析 HP 并创建 Subgroup。开始时，工具会检查可能的 ZBrush 候选对象。如果它发现看起来像 ZBrush 但不在 ZBrush 层中的网格，会显示警告:

- `Skip` - 继续分析；
- `Select` - 选择可疑网格并停止分析。

通常更建议点击 `Select`，检查这些网格，并将真正的 ZBrush 几何体放入正确的层中。

如果带孔的小细节、floaters 或 decals 被分到错误的组中，请在 `Algorithm` 中启用 `Ignore Floaters`，然后再次运行 `Analyze HP`。这会跳过将这类网格自动附着到附近大型 HP 部件的流程，同时保留其他 HP 分析功能。

## 10. 检查和编辑 Subgroup

分析后，左侧面板会显示 Subgroup 列表。

每个 Subgroup 行包含:

- `Vis` - 显示或隐藏该 Subgroup；
- Subgroup 名称 - 双击选择内容，右键重命名；
- `Add` - 将选中的网格添加到该 Subgroup；
- 锁定图标 - 防止该 Subgroup 被重建；
- `X` - 删除该 Subgroup。

### Create Group

在 `Algorithm` 中，使用 `Group name` 和 `Create Group` 手动创建配对的 HP/LP Subgroup。

### Add To Active

`Add` 会将选中的网格移动到当前激活的 Subgroup 中。可用于分析后的手动清理。

## 11. Assign LP

当 HP Subgroup 看起来正确后，点击 `Assign LP`。

工具会分析 LP 网格，并基于 HP 结构将它们分配到 LP Subgroup 中。场景准备越干净、HP 分组越准确，结果就越稳定。

如果 LP 分配不正确，请检查:

- 是否已经运行 `Analyze HP`；
- HP Subgroup 是否为空；
- LP 网格是否放置在对应 HP 几何体附近；
- 变换是否已冻结。

## 12. 手动相似性搜索

### Find Sim

`Find Sim` 会结合布局信息搜索相似网格。适用于重复元素。

### Find All

`Find All` 会进行更广泛的搜索，并且对布局的依赖更少。

## 13. GT Matcher

右上方面板用于手动解决复杂的 HP/LP 匹配情况。

主要控件:

- `Find LP Groups` - 查找可能的 LP 组；
- `Tolerance (%)` - 搜索容差；
- `Min HP/LP` - 最小匹配数量；
- `Strict Geo Check (Resolve Overlaps)` - 严格重叠检查；
- `Link` - 保存选中的匹配；
- `Unlink` - 移除匹配；
- `New` - 创建自定义手动链接；
- `Relocate HP` - 根据已保存的链接移动 HP。

## 14. Combine Fin

当 HP 和 LP Subgroup 准备好后，点击 `Combine Fin`。

工具会执行以下操作:

- 为最终导出重命名 HP；
- 将 LP Subgroup 合并为最终 `_low` 网格；
- 三角化最终 Low 网格；
- 将合并后的 Low 网格放入最终结构。

`Combine Fin` 后，最终 Low 网格可能会被隐藏。在 `Final Group` 模式中，它们由 `Low Visible / Low Hidden` 控制。

如果完成消息显示 `LP 0`，说明最终 Low 网格没有被构建。通常是因为没有运行 `Assign LP`，或者 LP Subgroup 为空。

## 15. Final Group

点击 `Final Group` 进入最终检查模式。

在此模式中:

- 会出现 `Smooth View` 和 `Export`；
- `LP Visible / LP Hidden` 控制最终合并后的 Low 网格；
- Subgroup 行会显示 HP Smooth 级别。

如果看到 `No final meshes. Run Combine Fin first.`，请返回并先运行 `Combine Fin`。

`Back` 会返回普通 Subgroup 列表。

## 16. Smooth 级别

`Final Group` 中的每一行都有一个 Smooth 下拉菜单:

- `Smooth 0` - 不进行平滑；
- `Smooth 1` - 轻度平滑；
- `Smooth 2` - 标准平滑；
- `Smooth 3` - 强平滑。

`+` 和 `-` 按钮用于提高或降低 Smooth 级别。

`Smooth View` 会在视口中预览平滑效果。它只是预览：导出时会再次应用 Smooth，然后回滚。位于 ZBrush 层中的 ZBrush 几何体会被跳过。

## 17. 导出 Chapter

普通点击 `Export` 会导出当前激活的 Chapter。

导出前，请检查:

- 已运行 `Combine Fin`；
- 当前处于 `Final Group` 模式；
- Smooth 级别已设置；
- ZBrush 网格已放入 ZBrush 显示层。

HP 导出会对普通 HP 几何体应用 Smooth，跳过 ZBrush 层中的几何体，并以归零变换导出 HP。LP 导出会使用最终三角化的 `_low` 网格。

## 18. Export 菜单

右键点击 `Export` 可打开导出选项:

- `Export Book -> Separate HP and LP` - 将 Book 中所有 Chapter 批量导出为单独的 HP/LP 文件；
- `Export Book -> HP+LP single file` - 每个 Chapter 批量导出为一个包含 HP 和 LP 的 FBX；
- `Export LP` - 仅导出 LP；
- `Export HP` - 仅导出 HP。

如果 LP 导出提示未找到合并后的 LP 网格，请先运行 `Combine Fin`。

## 19. Books

使用 `TABLE OF CONTENTS` 将 Chapter 组织到 Book 中。

右键菜单可以:

- 创建 Book；
- 重命名 Book；
- `Add to` - 将选中的 Chapter 添加到 Book；
- `Extract from the book` - 从 Book 中移除 Chapter；
- `Delete Selection` - 删除列表项。

眼睛图标用于在视口中显示或隐藏 Chapter 或 Book。

## 20. 会话

### Save

`Save` 会保存当前 Bake Groups 会话。部分数据会自动保存，但在进行较大编辑后，手动保存很有用。

### Load

`Load` 会加载 JSON 会话。它支持原始 Chapter 列表，也支持包含 `pairs` 键的文件。

## 21. 常见问题

### Analyze HP 创建了奇怪的组

请确认网格已用 `Separate` 拆分、变换已冻结，并且 ZBrush 网格位于 ZBrush 层中。如果 HP 已经手动整理好，请使用 `Keep HP`。

### ZBrush 几何体被 Smooth 了

检查显示层。若要跳过 Smooth，层名称必须包含 `zbrush`。

### 位于 ZBrush 命名 Subgroup 中的普通 HP 没有被 Smooth

Smooth 只会通过 ZBrush 显示层跳过。如果普通 HP 没有被 Smooth，请确认它没有被误放入 ZBrush 层。

### Find ZBrush 没有找到所有对象

右键点击 `Find ZBrush`，降低 `Triangular faces` 阈值。

### Assign LP 很慢

在大型场景中这可能是正常现象。再次运行前，请确保 LP 已准备好，并且临时网格已删除。

### Final Group 为空

请先运行 `Combine Fin`。`Final Group` 只会在合并步骤完成后显示最终数据。

## 22. 快速检查清单

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
