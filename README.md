# SilkPorter

SilkPorter 是 PC 皮肤到 Nintendo Switch 运行时皮肤包的转换和组装项目。
它读取 PC 皮肤、Switch 资源分析结果和当前 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 构建结果，生成
`Mods/Skin` 资源、完整 ExeFS IPS32 补丁，以及可直接复制到 SD 卡的 LayeredFS
输出包。

[SilkPorter](https://github.com/nice2cu1/SilkPorter) 不编译 C++ 运行时，不生成 `subsdk9`，也不生成 `main.npdm`。
这四个运行时文件必须来自同一次 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 构建：

```text
SilkRuntime/output/main.npdm
SilkRuntime/output/deploy/subsdk9
SilkRuntime/output/SilkModLoader.elf
SilkRuntime/output/build/SilkModLoader.lst
```

## 范围


当前目标：

- Title ID：`010013C00E930000`
- 游戏 Build ID：`FC9EA4CCC955D5799F37752B2D730B31`
- 已验证成功的游戏版本：`ver. 1.0.30000`
- 已验证成功的 NS 系统版本：`22.5.0`
- 已验证成功的 Atmosphère（AMS）版本：`1.11.2`
- 统一输出目录：`SilkPorter/output/`
- 皮肤根目录：`romfs/SilkModLoader/Mods/Skin/<皮肤目录名>/`
- 激活方式：当前 Loader 发现 `Skin/` 下的皮肤目录后直接读取


## SilkPorter 的处理流程

一次完整 staging 包含以下阶段：

1. 检查当前 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 的 `main.npdm`、`subsdk9`、ELF 和符号清单；
2. 检查目标游戏 `main` 的文本段、Build ID 和调用点原始指令；
3. 读取 PC 皮肤 PNG 以及 Switch/PC Texture2D、Collection、Atlas、Sprite
   分析结果；
4. 按对象名、Collection、Atlas、尺寸和格式匹配资源；
5. 按 Switch 端文件名编码规则写入 `Mods/Skin`；
6. 根据当前 ELF 的符号地址和文本段，生成完整的多记录 IPS32 补丁；
7. 将 `main.npdm`、`subsdk9`、IPS 和转换后的皮肤资源复制到最终包；
8. 写出 `manifest.json` 和 `README.txt`，记录哈希、Build ID、Hook 地址和
   资源统计。

最终 IPS 不是 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 的 `make` 产物，也不能使用旧 ELF 生成的补丁。当前
完整补丁由 `stage_runtime_skin.py` 在构建阶段生成；[SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 中的
`generate_exefs_tk2d_prelaunch_patch.py` 只是单调用点诊断工具。

## 输入目录

在双项目工作区布局下，默认分析输入位于：

```text
<workspace>/
├─ romfs/                           # 原版 Nintendo Switch 游戏 RomFS
│  └─ Data/
│     ├─ boot.config
│     ├─ globalgamemanagers
│     ├─ globalgamemanagers.assets
│     ├─ globalgamemanagers.assets.resS
│     ├─ level0
│     ├─ resources.assets
│     ├─ resources.assets.resS
│     ├─ RuntimeInitializeOnLoads.json
│     ├─ ScriptingAssemblies.json
│     ├─ sharedassets0.assets
│     ├─ Managed/
│     │  ├─ Metadata/
│     │  │  └─ global-metadata.dat
│     │  └─ Resources/*.dat
│     ├─ Resources/
│     │  ├─ unity default resources
│     │  └─ unity_builtin_extra
│     └─ StreamingAssets/
│        ├─ *.mp4
│        ├─ StreamingAssets.txt
│        ├─ BuildMetadata.json
│        ├─ aa/
│        │  ├─ catalog.bin
│        │  ├─ catalog.hash
│        │  ├─ settings.json
│        │  ├─ AddressablesLink/*
│        │  └─ Switch/
│        │     ├─ *.bundle
│        │     ├─ atlases_assets_assets/sprites/_atlases/*.bundle
│        │     ├─ cinematics_assets_assets/cinematics/data/*.bundle
│        │     ├─ dataassets_assets_assets/dataassets/**/*.bundle
│        │     ├─ scenes_assets_scenes/*.bundle
│        │     └─ scenes_scenes_scenes/*.bundle
│        └─ 其他原版 StreamingAssets 文件
├─ samples/                         # 原始 ExeFS 与 PC 原版 Bundle 样本
│  ├─ exefs/
│  │  ├─ main                         # 原版 main NSO，无扩展名
│  │  ├─ main.npdm                    # 原版 main NPDM
│  │  ├─ rtld                         # 原版 rtld NSO
│  │  ├─ sdk                          # 原版 sdk NSO
│  │  └─ subsdk0                      # 原版 subsdk0 NSO
│  └─ pc-original/
│     ├─ abyss_last_dive.spriteatlas.bundle
│     ├─ beast_slash.spriteatlas.bundle
│     ├─ core.spriteatlas.bundle
│     ├─ heart_deaths.spriteatlas.bundle
│     ├─ hornet.spriteatlas.bundle
│     ├─ memory.spriteatlas.bundle
│     ├─ peak.spriteatlas.bundle
│     └─ tools.spriteatlas.bundle
├─ SilkRuntime/
├─ SilkPorter/
│  └─ output/                        # 唯一的最终部署包目录
├─ output/
│  ├─ exefs-unpacked-<版本>/          # 从 samples/exefs/ 原始 NSO 解压的分析中间产物
│  │  └─ main/text.bin               # 当前 staging 用于生成 IPS 的目标代码段
│  └─ reports/                       # 由上述游戏资源生成的对象和地址分析报告
│     ├─ all-targets.json
│     └─ spriteatlas-targets.json
└─ pc-mods/                           # 可放置一个或多个 PC 皮肤根目录
   └─ <pc-skin-root>/
      ├─ <Collection>/atlas*.png
      └─ Texture2D/*.png
```

### `romfs/` 的作用

`romfs/` 必须是与目标 Build ID 对应的原版 Nintendo Switch 游戏 RomFS。它不是
最终皮肤包中的 `romfs/SilkModLoader/`，而是分析阶段使用的原版游戏资源。

其中最重要的内容是：

- `Data/StreamingAssets/aa/Switch/**/*.bundle`：Unity AssetBundle 原文件，包含
  TK2D Collection、Texture2D、SpriteAtlas、Sprite、场景、Prefab、动画和其他
  Addressables 资源；
- `atlases_assets_assets/sprites/_atlases/*.bundle`：SpriteAtlas 排布和
  RenderData 分析来源；
- `tk2dcollections_assets_silkshared.bundle`：共享 TK2D Collection 和图集分析来源；
- `herocollections_assets_shared.bundle`、`localpoolprefabs_assets_shared.bundle`、
  `animations_assets_shared.bundle` 以及各区域的动画/场景 Bundle：用于确认对象、
  Collection、Sprite 和运行时引用关系；
- `Data/Managed/Metadata/global-metadata.dat`：IL2CPP 类型、方法和字段分析来源；
- `Data/StreamingAssets/aa/catalog.*`、`settings.json` 和 `AddressablesLink/`：
  用于确认 Addressables 文件与资源路径关系；
- `Data/` 下的 Unity 全局资源、`*.assets`、`*.resS` 和其他 StreamingAssets：
  作为完整 RomFS 快照保留，供需要时进行交叉验证。视频文件通常不参与当前皮肤
  staging，但属于原版 RomFS 的一部分。

当前仓库快照中的 `romfs/` 约包含 2,068 个 Bundle，实际文件数量会随游戏版本和
提取方式变化。不要只挑选几个 Bundle 拼成一个“RomFS”；需要重新生成报告时，
应保留 `Data/` 的原始目录结构。

### `samples/` 的作用

`samples/` 保存从同一目标版本提取的原始 ExeFS 和 PC 原版 SpriteAtlas Bundle，
用于生成分析报告和验证资源匹配：

- `samples/exefs/main`：原版 main NSO。通过
  [SilkRuntime/tools/inspect_nso.py](https://github.com/nice2cu1/SilkRuntime/blob/main/tools/inspect_nso.py)
  解压后生成 `output/exefs-unpacked-<版本>/main/text.bin`，SilkPorter 用它确认
  Build ID、调用点原始指令和 IPS 记录位置；
- `samples/exefs/main.npdm`：原版 NPDM，用于与运行时 NPDM 做差异分析和保留原版
  启动基线；
- `samples/exefs/rtld`、`sdk`、`subsdk0`：用于崩溃报告、模块布局、ABI 和运行时
  兼容性分析，当前皮肤 staging 不直接读取它们；
- `samples/pc-original/*.spriteatlas.bundle`：PC 原版 SpriteAtlas Bundle，用于
  与 PC 皮肤 Bundle 对照、Sprite 名称/区域匹配和离线重排。当前仓库快照包含
  `abyss_last_dive`、`beast_slash`、`core`、`heart_deaths`、`hornet`、`memory`、
  `peak`、`tools` 八个 Bundle。

如果分析报告已经生成，`romfs/` 和 `samples/` 不需要参与日常的最终 staging；但
它们是从零重建报告、验证资源来源或处理新的游戏版本时的原始输入。

### 游戏资源与 Git 范围

`romfs/` 和 `samples/` 中的文件全部属于原版游戏或原始 PC 游戏资源，不属于
SilkPorter 的源代码和发布内容，也不应上传到 Git。根目录和 `SilkPorter/`
目录的 `.gitignore` 已排除以下本地输入：

```text
romfs/
samples/
pc-mods/
```

不要通过强制 Git 添加、压缩进仓库或提交这些目录来绕过排除规则。分享项目时只
提交 [SilkPorter](https://github.com/nice2cu1/SilkPorter)、[SilkRuntime](https://github.com/nice2cu1/SilkRuntime)、脚本、报告格式和文档；资源使用者必须自行从其
合法拥有的游戏版本提取对应文件。

### 推荐获取方案

必须使用与当前目标 Build ID
`FC9EA4CCC955D5799F37752B2D730B31` 相匹配的游戏版本。建议按以下顺序准备：

1. 从自己合法拥有的 Nintendo Switch 游戏本体/更新中，在 Switch 上使用
   `nxdumptool` 的 ExeFS/RomFS 导出功能，导出解密后的 ExeFS 和 RomFS。将
   ExeFS 中的 `main`、`main.npdm`、`rtld`、`sdk`、`subsdk0` 放入
   `samples/exefs/`，将 RomFS 的 `Data/` 原样放入 `romfs/Data/`。如果本体和
   更新分开导出，应使用最终实际运行版本对应的那一份资源。
2. 从同一目标版本的 PC 游戏资源中提取 PC 原版 SpriteAtlas Bundle，放入
   `samples/pc-original/`。至少需要当前报告涉及的八个 `*.spriteatlas.bundle`；
   如果要分析新的 Atlas，应同时补充对应 Bundle。
3. 用 `samples/exefs/main` 检查 NSO 的 Build ID，并生成解压段：

   ```powershell
   python SilkRuntime/tools/inspect_nso.py `
     samples/exefs/main `
     output/exefs-unpacked-FC9EA4CCC955D5799F37752B2D730B31/main
   ```

4. 使用 `romfs/`、`samples/` 和 `pc-mods/` 运行 SilkPorter 的分析工具，生成：

   ```text
   output/reports/all-targets.json
   output/reports/spriteatlas-targets.json
   ```

5. 日常运行时 PNG 转换需要这些报告、`main/text.bin`、`pc-mods/`、
   `SilkPorter/` 和已经构建的 `SilkRuntime/`。如果皮肤包含死亡茧或地图死亡
   图标资源，还要保留对应原始 `romfs/` Bundle；处理茧内部与背后丝线时还需
   `samples/pc-original/core.spriteatlas.bundle`。静态结果缓存会重新检查输入
   和生成物哈希，不能用旧缓存替代不同版本的原始资源。

该获取方案只描述资源的本地提取和整理，不提供或分发游戏文件本身。不同游戏版本
不能混用；如果 `main` 的 Build ID、Bundle 内容或 PC 资源版本不一致，必须重新
生成报告，不能继续使用旧报告。

当前测试用的星见雅皮肤资源来自 bilibili [终焉苍龙](https://space.bilibili.com/7054977)，
没有他就没有这个项目，十分感谢 UP 主的皮肤！该资源名称不是 SilkPorter 的固定输入。


如果 SilkPorter 与输入资料不在同一目录，可通过环境变量指定：

```powershell
$env:SILK_WORKSPACE_ROOT = 'D:\SilksongSwitchLocalInputs'
$env:SILK_RUNTIME_ROOT = 'D:\SilksongSwitchRuntime\SilkRuntime'
```

`SILK_WORKSPACE_ROOT` 应包含 `output/`、`pc-mods/` 以及分析报告；最终包仍写入
SilkPorter 项目自己的 `output/`；
`SILK_RUNTIME_ROOT` 应包含 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime) 的 `output/`。

## 生成运行时皮肤输出包

### 第 1 步：先构建 [SilkRuntime](https://github.com/nice2cu1/SilkRuntime)

按照 [SilkRuntime 构建说明](https://github.com/nice2cu1/SilkRuntime/blob/main/README.md) 完成构建，并确认以下四个
文件存在：

```text
SilkRuntime/output/main.npdm
SilkRuntime/output/deploy/subsdk9
SilkRuntime/output/SilkModLoader.elf
SilkRuntime/output/build/SilkModLoader.lst
```

四个文件必须属于同一次构建。特别是 `subsdk9` 和 ELF 必须对应，否则 IPS 中
写入的地址可能与实际运行时模块不一致。

### 第 2 步：选择 PC 皮肤并执行构建

`stage_runtime_skin.py` 不绑定某个皮肤名称。`--skin-dir` 指向一个 PC 皮肤根目录，
该目录下应同时包含 TK2D Collection 目录和 `Texture2D/`。`--skin-name` 是输出包在
`Mods/Skin/` 下使用的目录名，可以与 PC 皮肤目录名不同；省略时使用 PC 皮肤根目录
名称。分析报告必须与指定的 PC 皮肤资源对应。

例如：

```powershell
uv run --with-requirements SilkPorter/requirements.txt --python 3.12 `
  python SilkPorter/scripts/stage_runtime_skin.py `
  --skin-dir 'pc-mods/<pc-skin-root>' `
  --skin-name MySkin
```

如果工作区的 `pc-mods/` 下只有一个包含 `Texture2D/` 的皮肤根目录，
`--skin-dir` 可以省略；存在多个候选目录时必须显式指定。`--main-text` 同样可以在
工作区存在多个游戏构建时显式指定，`--reports-dir` 用于切换分析报告目录。

脚本会先验证 `main.npdm` 的固定哈希和目标 Build ID，再读取当前 ELF/map，
生成 IPS，并转换皮肤资源。任何输入缺失、哈希不匹配或调用点数量不符合预期
都会停止，不会静默生成一个看似完整但互不匹配的包。默认输出目录固定为
`SilkPorter/output/`，不会再创建阶段编号目录。

### 第 3 步：检查输出结果

当前输出目录应为：

```text
SilkPorter/output/
├─ README.txt
├─ manifest.json
└─ atmosphere/
   ├─ exefs_patches/
   │  └─ SilkModLoader/
   │     └─ FC9EA4CCC955D5799F37752B2D730B31.ips
   └─ contents/
      └─ 010013C00E930000/
         ├─ exefs/
         │  ├─ main.npdm
         │  └─ subsdk9
         └─ romfs/
            └─ SilkModLoader/
               └─ Mods/
                  └─ Skin/
                     └─ <任意皮肤目录名>/
                        ├─ <Collection>/atlas*.png
                        ├─ standalone/*.png
                        └─ sprite/*.png
```


### 第 4 步：部署到 Switch

将包中的 `atmosphere/` 目录整体复制到 SD 卡根目录：

```text
SD:/atmosphere/exefs_patches/SilkModLoader/...
SD:/atmosphere/contents/010013C00E930000/exefs/main.npdm
SD:/atmosphere/contents/010013C00E930000/exefs/subsdk9
SD:/atmosphere/contents/010013C00E930000/romfs/SilkModLoader/Mods/Skin/...
```

不能只复制 `contents/010013C00E930000`，因为 IPS 文件位于
`atmosphere/exefs_patches/`。

## 测试

运行 SilkPorter 单元测试：

```powershell
uv run --with-requirements SilkPorter/requirements.txt --python 3.12 `
  python -m unittest discover -s SilkPorter/tests -v
```

测试和 staging 都不应修改原始 PC 皮肤或游戏 Bundle。修改 Bundle 的实验工具
必须把结果写入 `output/` 。

## PC 资源匹配规则

纹理匹配使用对象上下文、资源名称和结构信息：

| 资源类别 | 匹配依据 |
| --- | --- |
| TK2D 集合 | 集合名称、图集索引及目标尺寸 |
| Material 使用的 Texture2D | 精确 Unity 对象名及已确认尺寸，放入 `standalone/` |
| Sprite 引用的 Texture2D | 根据 Switch 序列化引用确认对象关系，精确匹配对象名和尺寸，放入 `sprite/` |
| SpriteAtlas | PC/Switch RenderDataKey、Sprite 名称及纹理区域，整张替换纹理放入 `sprite/` |

PC PNG 必须经过目标尺寸、格式和运行时访问路径验证后，才能加入部署清单。
排布不同的 SpriteAtlas 必须完成离线重排；未确认目标对象或缩放行为的辅助纹理
不纳入已验证覆盖范围。

## SpriteAtlas 重排规则

PC 与 Switch 的部分 SpriteAtlas 采用不同排布，直接覆盖整张图集可能导致 Sprite
使用错误的纹理区域。已验证的离线处理流程是：

1. 读取两端 SpriteAtlas RenderData 元数据；
2. 使用 RenderDataKey 和稳定名称匹配 Sprite；
3. 根据旋转及 pivot 规则提取 PC Sprite 区域；
4. 将提取区域写入符合 Switch 布局和尺寸的画布；
5. 重新打开生成的同尺寸 PNG，检查 Sprite 显示区域和非目标像素；
6. 将生成的 PNG 作为完整纹理替换输入写入部署目录。

缺少可靠 PC 源纹理的区域保留 Switch 原始内容。重排在离线阶段完成，运行时
保持 SpriteAtlas 元数据不变。

## Switch 皮肤目录格式

Switch 端皮肤目录不使用 `skin.json`。Loader 根据目录名和文件名直接解析资源：

```text
Skin/
└── <Skin Name>/
    ├── <Collection Name>/
    │   └── atlas0.png
    ├── standalone/                 # Material.set_mainTexture 路径
    │   └── <encoded Unity Texture2D name>.png
    └── sprite/                     # Sprite.get_texture 路径及 SpriteAtlas 整图
        └── <encoded Switch Texture2D name>.png
```

`Skin/` 下只放一个由本次构建生成的皮肤目录；目录名可任意命名。集合目录名和
`atlasN.png` 编号必须与 Switch 的 TK2D 集合一致。
`standalone/` 和 `sprite/` 中的文件名由 SilkPorter 使用与 Loader 相同的可逆编码
生成，避免 Unity 名称中的特殊字符破坏 SD 路径。Sprite、Material 和已加载纹理扫描
统一先查 `standalone/`，再查 `sprite/`；按实际纹理名精确匹配，并共用对象身份去重。

`active.txt` 属于 PC Mod 的启用标记，Switch 端不读取，也不会写入输出包。



## 依赖与许可证

直接 Python 依赖的版本固定在 `requirements.txt`：

- UnityPy 1.25.3 — MIT；用于 UnityFS/SerializedFile 访问、Texture2D 转换和
  Unity Bundle 重建。项目地址：<https://github.com/K0lb3/UnityPy>
- Pillow 12.3.0 — MIT-CMU；用于 PNG 解码、图像比较、纹理修复和 staging 校验。
  项目地址：<https://github.com/python-pillow/Pillow>

UnityPy 的传递依赖由 `uv` 解析；SilkPorter 不再分发依赖二进制文件，也没有复制
自定义 Unity 二进制解析器或 Nintendo Switch swizzle 实现。

本地使用的 PC 游戏文件、Switch 游戏文件和皮肤压缩包不属于本项目的发布内容，
不会随 SilkPorter 上传。
