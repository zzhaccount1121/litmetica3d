# Litematica 3D

## 0.6.3 preview · Windows 原生 WinUI 3

Litematica 3D 使用 C# / XAML 工作台并复用 Python 转换引擎，提供批量导入、打印/视觉/渲染/自定义预设、高级参数、进度、取消、日志、报告和主题设置。

下载 [0.6.3 WinUI 便携版](https://github.com/zzhaccount1121/litmetica3d/releases/download/v0.6.3/Litematica3D-WinUI-v0.6.3-win-x64.zip)，完整解压后运行 `Litmetica3D.WinUI.exe`。便携版带有运行所需的 .NET、Python 和 Minecraft 26.2 资源，无需安装 Minecraft。

```powershell
.\setup_winui.ps1
.\run_winui.ps1
```

源码运行需要 Windows x64、.NET 10 SDK 和 Python 3.10+。详细说明见 [WinUI 前端文档](frontend/README.md)。原 PySide6 使用说明保留在后文。

> 将 Minecraft Litematica 投影转换为适合 **3D 打印**、**模型预览**和 **Blender 渲染**的 STL / OBJ 模型。

![Version](https://img.shields.io/badge/version-0.6.3--preview-f59e0b)
![Platform](https://img.shields.io/badge/platform-Windows-2563eb)
![Minecraft](https://img.shields.io/badge/Minecraft-26.2-16a34a)
![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776ab)
![License](https://img.shields.io/badge/license-Non--Commercial%20Share--Alike-dc2626)

Litematica 3D 可以读取 `.litematic` 文件中的区域、坐标、方块名称和完整方块状态，并使用内置 Minecraft 26.2 模型资源生成三维模型。软件提供现代化桌面界面，无需安装 Minecraft；便携版也不需要安装 Python。

## 核心功能

- 将 `.litematic` 转换为 STL 或 OBJ。
- 根据原版 `blockstates` 和方块模型解析朝向、连接、开关、层数等状态。
- 打印模式执行实体化、布尔并集、壳体与空腔处理，目标是输出无破面的可打印模型。
- 视觉模式生成 UV、原版贴图、透明像素几何和动态染色，适合 Blender 渲染。
- 支持水体 `cube`、`drop`、`level` 三种处理方式。
- 支持 `none`、`material`、`exact`、`clustered` 四种 Blender 发光模式。
- 未知方块可回落为立方体，也可以忽略，并在转换报告中记录。
- 内置 Minecraft 26.2 资源，不依赖本地 Minecraft JAR。

> [!IMPORTANT]
> **3D 打印请选择打印模式。** 打印模式以封闭、水密和无内部穿插为目标。  
> **彩色展示请选择视觉模式并输出 OBJ。** 视觉模型重视贴图和透明效果，可能存在开放边，不保证可直接打印。

## 快速下载与使用

1. 下载 [v0.6.3 WinUI 预览版便携包](https://github.com/zzhaccount1121/litmetica3d/releases/download/v0.6.3/Litematica3D-WinUI-v0.6.3-win-x64.zip)。
2. 完整解压 ZIP，不要直接在压缩软件中运行。
3. 双击 `Litmetica3D.WinUI.exe`。
4. 选择一个 `.litematic` 文件和总输出位置；界面会自动按 `L3D_output/投影名/` 分类。
5. 选择“打印”“视觉”“渲染”预设，或在“高级”页面自定义参数。
6. 点击“开始转换”。

> [!WARNING]
> v0.6.1 存在已确认的启动崩溃。v0.6.0 在当前测试机可以启动；若你的电脑启动失败，请使用上方的最新便携包。

WinUI 便携版包含 Python 运行环境、Manifold3D、Pillow、NumPy、.NET/WinUI 运行文件和 Minecraft 26.2 模型资源。复制到另一台 Windows 电脑并完整解压后即可运行。

## 版本说明

- [0.6.0 合并版说明](RELEASE_NOTES_v0.6.0.md)：旧预览版；启动失败时请使用最新便携包。
- [0.6.1 修复版说明](RELEASE_NOTES_v0.6.1.md)：有已确认的启动崩溃，请使用最新便携包。
- [0.6.2 预览版说明](RELEASE_NOTES_v0.6.2.md)。
- [0.6.3 预览版说明](RELEASE_NOTES_v0.6.3.md)。
- [Issue #2 逐项核验](ISSUE_2_VERIFICATION.md)。

## 目录

- [转换模式](#转换模式)
- [界面说明](#界面说明)
- [详细功能](#详细功能)
  - [Litematic 与方块状态解析](#litematic-与方块状态解析)
  - [水体处理](#水体处理)
  - [未知方块回落](#未知方块回落)
  - [打印实体流程](#打印实体流程)
  - [视觉贴图与透明像素](#视觉贴图与透明像素)
  - [Blender 发光](#blender-发光)
  - [壳体与封闭空腔](#壳体与封闭空腔)
  - [面数优化](#面数优化)
- [输出文件](#输出文件)
- [在 Blender 中使用](#在-blender-中使用)
- [从源码运行](#从源码运行)
- [命令行使用](#命令行使用)
- [转换报告](#转换报告)
- [已知限制](#已知限制)
- [项目结构](#项目结构)
- [参与开发](#参与开发)
- [许可证](#许可证)
- [作者](#作者)

## 转换模式

### 打印

推荐用于 3D 打印和切片软件。

默认预设：

| 参数 | 值 |
|---|---|
| 格式 | STL |
| 水体 | `drop` |
| 未知方块 | `ignore` |
| 模型优化 | 自动执行 |
| 输出用途 | `print` |
| 独立壳体 | `main` |
| 封闭空腔 | `fill` |

打印流程不会读取贴图、UV 或发光数据。零厚度植物、珊瑚扇和锁链会按最小厚度转换为封闭实体，相交部件通过 Manifold3D 执行布尔并集。

### 视觉

推荐用于 Blender、Maya 等三维软件中的彩色预览和渲染。

默认预设：

| 参数 | 值 |
|---|---|
| 格式 | OBJ |
| 水体 | `level` |
| 未知方块 | `ignore` |
| 模型优化 | 自动执行 |
| 输出用途 | `visual` |
| 独立壳体 | `keep` |
| 封闭空腔 | `preserve` |
| 发光模式 | `material` |

视觉模式固定带有原版贴图。透明像素不会生成几何，半透明材质会写入 OBJ/MTL 配套资源。

### 渲染

推荐用于需要 Blender 可编辑灯光的场景。

该预设使用 OBJ、视觉用途和 `exact` 发光模式，为每个识别到的发光方块创建可编辑的 Blender Light。

### 自定义

在高级页面修改任何参数后，预设会自动切换为“自定义”。预设页会显示当前完整配置；配置框支持鼠标滚轮和滚动条。

## 界面说明

桌面界面使用 PySide6 构建，默认启用暗色主题，并支持浅色主题。

左侧页面：

- **预设**：输入文件、输出文件夹、模式选择、转换进度和自定义配置摘要。
- **高级**：水体、回落、优化、尺寸、壳体、空腔、贴图和发光等参数。
- **日志**：实时转换日志和转换完成后的 JSON 报告。

左下角显示：

- 内置 Minecraft 资源版本；
- 软件版本；
- 作者：b站@ZZHaccount；
- 当前任务总耗时；
- 深浅色切换按钮。

### 参数联动

- STL 固定使用 `print`，不携带贴图和发光。
- `visual` 使用 OBJ 并固定带有原版贴图。
- 打印用途会禁用视觉与发光区域。
- 发光模式选择 `none` 时保留贴图，但不生成发光材质或 Blender Light。
- 最小壳体体积只在 `remove-small` 模式下生效。
- 独立壳体、封闭空腔和并集失败策略会保留用户选择。

## 详细功能

### Litematic 与方块状态解析

软件读取 NBT 数据和 Litematica 区域信息，并直接从 LongArray 提取方块索引。位流按照 LSB-first 规则解析，支持跨 64 位边界。

方块处理顺序：

```text
空气 → 水体 → 26.2 方块名单 → 完整状态模型 → 回落 → 网格生成
```

模型系统支持：

- `variants` 和 `multipart`；
- 条件组合、列表条件和加权模型；
- 父模型递归继承；
- element 旋转和 blockstate 旋转；
- 朝向、轴向、半部、形状、开关和连接状态；
- 楼梯、半砖、门、活板门、栅栏门、红石、玻璃板、墙、栅栏、作物等模型；
- 基于“命名空间＋方块名称＋完整属性集合”的模型缓存；
- 由世界坐标和完整状态生成的稳定随机模型选择。

空气、洞穴空气和虚空空气始终忽略，不进入回落流程。

### 水体处理

| 模式 | 行为 |
|---|---|
| `cube` | 水、水草及含水方块整体输出为 1×1×1 方块 |
| `drop` | 删除所有水；水草和含水方块只保留自身模型 |
| `level` | 普通水按水位高度生成；含水方块去水后保留自身模型 |

`level` 模式支持水源、流动水、落水和相邻水位形成的四角高度。

### 未知方块回落

以下情况进入回落流程：

- 方块不在内置 26.2 方块名单中；
- 方块存在，但状态属性无法匹配；
- blockstate、模型、父模型或 element 数据缺失；
- 模型 JSON 损坏或解析失败。

| 模式 | 行为 |
|---|---|
| `cube` | 在原坐标生成标准 1×1×1 立方体 |
| `ignore` | 完全跳过，不生成几何 |

每次回落都会写入报告，不会静默处理。

### 打印实体流程

打印模式以拓扑完整性为优先级：

1. 每个 Minecraft element 转换为封闭长方体。
2. 零厚度面片按最小厚度扩展为封闭板。
3. 完整方块预合并为较大的轴对齐长方体。
4. 特殊方块内部相交部件执行精确并集。
5. 按空间区块执行平衡树布尔并集。
6. 分析独立壳体和封闭空腔。
7. 检查开放边、非流形边、方向冲突、自相交和退化三角形。
8. 通过检查后导出 STL 或无贴图 OBJ。

精确并集失败时可选择：

- `voxel32`：对失败方块执行 32 级局部体素实体化回退；
- `fail`：立即停止转换并报告错误。

### 视觉贴图与透明像素

视觉模式读取内置原版 PNG 纹理并生成 UV：

- Alpha 为 0 的像素不生成几何；
- Alpha 大于 0 的像素保留；
- OBJ 通过 MTL 和透明度贴图表现半透明材质；
- 动画纹理使用第一帧；
- 草、藤蔓、蕨、树叶使用固定平原群系颜色；
- 红石根据 `power=0..15` 动态染色；
- 玻璃透明中心不生成体积；
- 箱子、潜影盒、旗帜和头颅等使用实体贴图资源；
- 玩家头没有离线皮肤时使用内置默认皮肤，不联网下载。

透明裁切几何可能包含开放边或薄片，因此视觉 OBJ 不应直接用于 3D 打印。

箱子使用独立的实体 UV 展开规则，分别处理单箱、双箱左右半边以及四个朝向。箱盖顶面、侧面和锁扣使用各自对应的贴图区域，双箱的两个半边在接缝处连续拼接；此规则仅用于视觉贴图，不更改打印模型。

#### 半透明无缝玻璃

在“高级 → 视觉与发光”中调整 **半透明无缝玻璃**。“渲染”预设默认开启，“视觉”预设默认关闭；此开关仅在视觉用途下可调整，打印模式不受影响。可以在高级页面手动更改，修改后进入自定义配置。

- **关闭**：使用原版玻璃模型和贴图，保留原版边框。
- **开启**：普通玻璃与 16 色染色玻璃使用无边框、均匀半透明贴图；染色玻璃保留各自颜色。同色相邻玻璃的内部接触面会删除，玻璃板仍按连接状态生成，异色之间保留颜色边界。
- 含水玻璃在 `cube` 水体模式下仍遵循整格立方体规则；需要玻璃外观时选择 `drop` 或 `level`。

OBJ 材质写入不透明度，同时生成 Blender 配置脚本。请重新转换并导入新 OBJ，保留配套 MTL 与贴图文件夹；运行同目录的 `模型名_blender_setup.py` 可明确设置玻璃 Alpha。请在材质预览或渲染视图查看，而不是实体视图。此效果使用表面透明度，不是物理厚玻璃折射；多层外表面仍会叠加透明度。

### Blender 发光

发光系统分为两部分：

1. **像素级发光材质**：只有灯芯、火焰、红石线路等识别到的纹理像素发光。
2. **Blender Light**：在 Cycles 中照亮周围物体，可在 Blender 内编辑功率、颜色和半径。

| 模式 | 发光材质 | Blender Light | 适合场景 |
|---|---:|---:|---|
| `none` | 否 | 否 | 保留贴图但不需要发光 |
| `material` | 是 | 否 | 仅材质发光，场景对象最少 |
| `exact` | 是 | 每个光源一个 | 小型场景、特写、逐灯编辑 |
| `clustered` | 是 | 邻近同类光源合并 | 大型建筑、城市和红石机器 |

发光状态根据方块名称和完整属性判断，例如：

- 红石灯、熔炉和营火读取 `lit`；
- 红石粉读取 `power`；
- 重生锚读取充能数量；
- 洞穴藤蔓检查是否带发光浆果；
- 固定光源使用对应原版发光等级。

视觉 OBJ 会生成 `_blender_setup.py`。脚本负责：

- 切换到 Cycles；
- 加载像素发光遮罩；
- 配置发光材质；
- 创建 `Minecraft Lights` 集合；
- 设置基础采样和降噪；
- 在接口兼容时配置可选泛光。

可提供 JSON 发光规则，按方块名称、状态、区域或投影原始坐标覆盖强度和颜色。`position` 使用 `区域 Position + 区域局部坐标`，在模型移到原点、居中或缩放之前匹配；它不假定游戏世界的绝对原点。报告的 `coordinate_origin` 记录导出时减去的坐标。

### 壳体与封闭空腔

独立壳体：

| 模式 | 行为 |
|---|---|
| `keep` | 保留全部连通实体并报告 |
| `remove-small` | 删除低于指定体积阈值的实体 |
| `main` | 只保留体积最大的主要壳体 |

封闭空腔：

| 模式 | 行为 |
|---|---|
| `preserve` | 保留模型内部完全封闭的空气空间 |
| `fill` | 填充与外界不连通的封闭空腔 |

门洞、窗户、隧道和其他与外界连通的空间不会被当作封闭空腔。

### 面数优化

视觉和渲染预设导出的 OBJ 会自动进行保形压缩，无需额外勾选：

- 分块复用坐标完全相同的顶点，UV 独立索引，保留贴图接缝。
- 将轴对齐且 UV 连续的矩形恢复为四边面，减少 Blender 中的多边形和边记录；斜面、旋转面、非连续 UV 仍使用原三角形。
- 保留平面着色、材质、透明度、发光数据、灯光数量和表面顺序，不降采样贴图，不通过删除透明层或近似减面换取速度。
- 日志和转换报告显示优化前后的顶点、多边形数量。按三角形计算的渲染面数不变，效果提升取决于模型的重复顶点和矩形占比；大量逐点灯光带来的开销不会被此优化消除。

打印模型和 STL 输出不受这项 OBJ 结构优化影响。

没有可切换的 `raw/safe/experimental` 等级。打印模式通过实体布尔并集处理内部面；视觉模式自动复用顶点并恢复 UV 连续的矩形。转换报告中的 `optimize_mode=automatic` 表示实际采用的自动流程。

## 输出文件

图形界面会在选择的总输出位置下建立 `L3D_output`，再按投影文件名分别建立子文件夹。单个投影的模型、材质、贴图及 Blender 辅助文件都保存在同一子文件夹，例如：

```text
所选位置/
└── L3D_output/
    ├── 城堡/
    │   └── 城堡.stl
    └── 农场/
        ├── 农场.obj
        ├── 农场.mtl
        └── 农场_textures/
```

如果选择的已经是 `L3D_output` 文件夹，不会重复创建同名文件夹。再次转换同名投影时会使用 `投影名 (2)`、`投影名 (3)` 等新子文件夹，保留以前的结果。命令行明确指定的输出文件路径仍按原路径写入。

### 打印 STL

```text
模型.stl
```

STL 不包含颜色、UV、材质或贴图。

### 视觉 OBJ

```text
模型.obj
模型.mtl
模型_textures/
模型.blender_emission.json
模型_blender_setup.py
```

转换报告显示在软件“日志 → 转换报告”页面，默认不导出 `.report.json`。

## 在 Blender 中使用

1. 使用“视觉”或“渲染”预设生成 OBJ。
2. 保持 OBJ、MTL、贴图目录和 `_blender_setup.py` 位于同一文件夹。
3. 在 Blender 中选择“文件 → 导入 → Wavefront (.obj)”。
4. 保持默认 `Forward -Z / Up Y`。
5. 打开 Scripting 工作区并运行 `_blender_setup.py`。
6. 切换到 Cycles 渲染预览。
7. 如果使用 `exact` 或 `clustered`，在 `Minecraft Lights` 集合中编辑灯光。

`material` 不创建灯光对象，照明依靠 Cycles 对发光表面的路径追踪；小面积光源可能需要更高采样数。

## 从源码运行

要求：

- Python 3.10 或更高版本；
- Windows、Linux 或 macOS 的 Python 环境；
- 图形界面需要可用的 Qt 桌面环境。

```bash
git clone https://github.com/zzhaccount1121/litmetica3d.git
cd litmetica3d
python -m venv .venv
```

Windows：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
litmetica3d-gui
```

Linux / macOS：

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
litmetica3d-gui
```

运行测试：

```bash
python -m pytest -q
```

## 命令行使用

```text
litmetica3d <输入.litematic> <输出.stl或obj> [参数]
```

示例：

```powershell
# 默认打印 STL
litmetica3d building.litematic building.stl

# 视觉 OBJ
litmetica3d building.litematic building.obj --geometry visual --textures

# 删除水并忽略未知方块
litmetica3d building.litematic building.stl --water drop --fallback ignore

# 只保留主要壳体并填充封闭空腔
litmetica3d building.litematic building.stl --components main --cavities fill

# 视觉 OBJ，仅材质发光
litmetica3d building.litematic building.obj --geometry visual --blender-lights material
```

常用参数：

| 参数 | 可选值 | 说明 |
|---|---|---|
| `--water` | `cube/drop/level` | 水体处理 |
| `--fallback` | `cube/ignore` | 未知方块处理 |
| `--geometry` | `print/visual` | 输出用途 |
| `--components` | `keep/remove-small/main` | 独立壳体处理 |
| `--cavities` | `preserve/fill` | 封闭空腔处理 |
| `--boolean-fallback` | `voxel32/fail` | 布尔失败处理 |
| `--blender-lights` | `none/material/exact/clustered` | Blender 发光模式 |
| `--emission-strength` | 浮点数 | 全局发光强度倍率 |
| `--center` | 开关 | 模型居中 |
| `-r, --region` | 区域名称 | 选择指定区域，可重复使用 |

查看完整参数：

```bash
litmetica3d --help
```

## 转换报告

报告包括：

- 输入和输出路径；
- 资源版本和方块数量；
- 成功渲染、忽略和回落数量；
- 名单外方块、未知状态和模型解析错误；
- 相关方块属性、区域和坐标示例；
- 水体、透明裁切、染色和发光统计；
- 顶点和三角形数量；
- 独立壳体、封闭空腔和布尔回退统计；
- 可打印性检查结果；
- 各转换阶段耗时。

GUI 不直接导出报告文件，报告可以在界面中选择和复制。

## 已知限制

- 当前内置资源版本为 Minecraft 26.2；其他版本或模组方块可能进入回落流程。
- 视觉模型可能包含开放边、透明薄片和独立表面，不保证水密。
- 动画贴图只使用第一帧。
- 群系染色固定使用平原配色。
- 玩家头不会联网下载皮肤。
- `exact` 在大型投影中可能创建大量 Blender 灯光对象。
- `clustered` 会减少灯光数量，但属于近似照明。
- 超大型投影的精确布尔并集仍可能消耗较多时间和内存。

## 项目结构

```text
litmetica3d/
├─ litmetica3d/
│  ├─ conversion.py       # 统一转换服务
│  ├─ litematic.py        # Litematic 与位流解析
│  ├─ model_loader.py     # 原版方块状态与模型加载
│  ├─ visual_mesh.py      # 视觉网格、UV 与贴图
│  ├─ solid.py            # 打印实体、布尔并集与壳体分析
│  ├─ emission.py         # 发光识别与规则
│  ├─ gui_app.py          # PySide6 桌面界面
│  ├─ exporters/          # STL / OBJ 导出器
│  └─ mc_assets/          # 内置 Minecraft 26.2 资源
├─ tests/                 # 自动化测试
├─ pyproject.toml         # Python 项目配置
├─ run_gui.py             # GUI 启动入口
└─ README.md
```

## 参与开发

欢迎提交 Issue、修复建议和 Pull Request。提交前建议：

1. 说明问题对应的方块名称、完整状态和 Minecraft 版本。
2. 尽可能附上最小 `.litematic` 样例或截图。
3. 修改模型解析、UV 或布尔流程时添加回归测试。
4. 运行 `python -m pytest -q` 并确认测试通过。
5. 不要提交构建目录、虚拟环境、缓存或便携版二进制文件。

## 转换性能与结果一致性

转换器按实际选中的模型组合复用局部几何；随机外观仍由原坐标和完整状态决定，不会因为缓存复用而统一朝向或随机样式。视觉网格直接写入平移后的顶点，减少临时面片对象；发光处理只复制需要修改的面片元数据，不修改共享的几何与UV。仅在水位或可编辑灯光需要时建立邻居查询表。

视觉网格按批次检查和写入，每个临时缓冲区最多8192面，减少逐面创建小数组；最终几何仍保存在内存中，总内存会随模型增大。确定没有方块光级、模型显式发光属性及发光纹理时，跳过不必要的发光计算；坐标发光规则仍保留。多个材质共用同一贴图时，只编码和写出一次，不合并材质，也不增加图像缓存。

这些内部优化自动生效，不增加并行进程或扩大网格分块，也不执行额外减面、坐标吸附、贴图替换或透明度近似。它们与下方会改变镂空几何的可选「特殊优化」不同。性能验证同时检查各类模型耗时和峰值内存，不以牺牲某一类模型的明显性能换取平均值。

## 特殊优化：填平贴图镂空

在「高级 → 模型与输出」中勾选红色的 `**特殊优化：填平贴图镂空**`。默认关闭，打印、视觉和渲染预设及自定义配置都可以使用；调整后界面会切换到自定义配置。重新选择预设会恢复关闭。

开启后，不再根据贴图的透明像素切割模型。树叶保留完整方块，草、藤蔓、珊瑚扇等保留完整的增厚板，原模型的旋转、位置和最小厚度设置仍然有效。它不会把所有特殊方块都变成1×1×1立方体，也不会填充建筑空腔。

带贴图时，完整几何上仍映射原贴图，颜色、UV与贴图透明度保留：**视觉上的透明不等于几何上的孔洞**。导出不带贴图的模型时，这些区域呈实心。打印流程原本就不按透明像素挖孔，因此开启该选项不会改变已有打印几何。

适用于树叶、藤蔓等像素镂空几何过多的大型投影。它以牺牲真实孔洞换取更少的面数，不属于外观严格等价优化；边缘、阴影和透明显示可能不同，也不保证所有大型模型都能流畅运行。原有透明玻璃规则保持独立。

透明遮罩同时包含黑白颜色与真实 Alpha 通道，兼容 Blender 的 OBJ 透明度导入。旧模型若显示黑底，可在材质节点中将黑白遮罩的「颜色」输出连接到 Principled BSDF 的「Alpha」，替换原先错误的 Alpha 输出连接；也可重新转换并导入新模型。

命令行参数：`--solid-textures`。转换报告的 `solid_textures` 字段记录是否开启。

## 许可证

本项目使用 **Litematica3D 非商业同许可许可证 1.0**：

- 允许个人学习、研究、修改和非商业分发；
- 禁止商业使用、收费分发、付费服务以及商业产品集成；
- 发布修改版或衍生作品时，必须提供对应完整源代码；
- 衍生作品必须使用相同许可证，并保留作者和版权声明；
- 商业用途必须事先取得作者的独立书面授权。

完整条款见 [LICENSE](LICENSE)。由于禁止商业使用，该许可证属于**源码可用许可证**，不是 OSI 认可的开源许可证。

## 作者

**作者：b站@ZZHaccount**

如果这个项目对你有帮助，欢迎在 GitHub 提交反馈，也欢迎前往 B 站关注作者。
