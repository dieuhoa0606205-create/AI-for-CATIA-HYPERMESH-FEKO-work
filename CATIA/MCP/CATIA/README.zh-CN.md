# CATIA Agent MCP

运行于 Windows 的 STDIO MCP 服务，用于控制 CATIA V5 的原生建模与 CATIA 内部 Analysis/ELFINI 仿真。

[English](README.md) · [安全导出](docs/safe-export.zh-CN.md) · [高级曲面](docs/advanced-surfaces.zh-CN.md) · [兼容性与架构](docs/compatibility-and-architecture.md)

服务通过 COM 控制正在运行的 CATIA V5 会话，不调用 Abaqus、ANSYS、CalculiX 或其他第三方求解器。

## 已实现范围

- CATPart 原生建模：草图、Pad、Pocket、恒定半径边倒圆、参数、材料、更新、保存、导出与模型树检查。
- 原生 GSD 线框与曲面：三维点、开闭 Spline、偏置平面、G0/G1/G2 Connect、`HybridShapeLoft`、`Join`、`Healing`、`Boundary`、`Close Surface` 与 `Thick Surface`。
- CATProduct 新建、零部件插入与装配结构检查。
- CATAnalysis：导入零件或装配、工况、Solution、Analysis Set、载荷与约束实体、支撑绑定、Mesh Part、网格规格、内部计算、结果图像与数据、HTML 报告。
- 55 个固定、类型化的 MCP 工具。不开放任意 Python、CATScript、Shell 或原始 COM 方法执行。
- CATIA COM 调用集中在单个 STA 工作线程；请求带 ID 以支持幂等，文件访问限定在配置的根目录内。

## 环境要求

- Windows，已安装并具备许可证的 CATIA V5。
- Python 3.10 及以上。
- CATIA 与 MCP 进程必须使用相同的 Windows 用户和相同的权限级别。若一个以管理员权限启动、另一个不是，可能看得到 `CNEXT.exe`，却无法从 ROT 取得 `CATIA.Application`。
- MCP SDK 1.x 或 2.x（`mcp>=1.7,<3`）。

## 安装与启动

将 [`examples/codex_config.example.toml`](examples/codex_config.example.toml) 复制到 MCP 客户端配置，并按实际安装修改：

- `CATIA_MCP_ENV_NAME`：已安装版本的 CATEnv 名称，例如 `CATIA_P3.V5-6R2023.B33`。
- `CATIA_MCP_ENV_DIR`：CATEnv 目录。
- `CATIA_MCP_WORKSPACE` 与 `CATIA_MCP_ALLOWED_ROOTS`：项目目录与允许访问的文件根目录。
- `CATIA_MCP_MIN_RELEASE_INDEX`：通常为 `28`。

通过启动包装脚本运行：

```powershell
& '.\scripts\run_server.ps1'
```

包装脚本会依次选择 `CATIA_MCP_PYTHON`、本地 `.venv` 或可用的 Codex Python 运行时，并避免日志写入 STDIO。

## 快速开始

1. 调用 `catia_health_check`，确认选中的版本与 Analysis TypeLib。
2. 调用 `catia_connect`。默认只连接已有会话，不擅自启动新的 CATIA 实例。
3. 以小粒度原生特征建模：`catia_create_part`、`catia_create_sketch`、`catia_add_pad` 或 `catia_add_pocket`，每次更新后调用 `catia_inspect_active` 检查模型树。
4. 创建 CATAnalysis 之前先分配具有力学属性的 CATIA 材料。
5. 只保存到 `CATIA_MCP_ALLOWED_ROOTS` 允许的目录。
6. 导出默认使用 `overwrite_policy="error"`。需要保留现有文件时使用 `versioned`，需要替换时使用 `replace`。导出先写入唯一的临时文件，STEP/IGES 默认会回读验证。

## 仿真流程

1. `catia_create_analysis_document`：导入零件或装配。
2. `catia_inspect_analysis`：检查 Model、Case、Set 与 Mesh Part 树。
3. 创建工况、Solution、Analysis Set、载荷、约束、实体与支撑。
4. 通过 `catia_add_analysis_mesh_part` 与 `catia_set_analysis_mesh_specification` 建立网格。
5. 先以 `mesh_only=true` 调用 `catia_compute_analysis`，检查网格后再执行完整计算。
6. 创建结果图像，导出数值数据，并生成 CATIA 原生 HTML 报告。

COM 调用返回只说明接口已经返回，不代表物理模型正确。应继续检查材料、单位、边界条件、网格、结果图像、位移与应力数据以及报告。

## 本地验证

```powershell
$env:PYTHONPATH=(Resolve-Path '.\src').Path
python -m unittest discover -s '.\tests' -v
python -m compileall -q '.\src'
python '.\scripts\stdio_smoke.py'
python '.\scripts\probe_environment.py'
python '.\scripts\probe_live.py'
python '.\scripts\validate_issue_fixes.py'
python '.\scripts\validate_edge_fillet.py'
```

仅在确需启动所选 CATIA 环境时使用 `probe_live.py --start-if-missing`。各测试文件的覆盖范围见
[`tests/README.md`](tests/README.md)。

## 版本支持

| 版本 | 状态 |
|---|---|
| B28 / V5-6R2018 及以上 | 目标基线；保留完整工具面，运行时探测只对实际缺失的操作做能力门控 |
| B33 / V5-6R2023 | 本机验证版本 |
| B21 / V5R21 SP6+HF5 | 已完成适配与实机验证；使用时须设置 `CATIA_MCP_ALLOW_LEGACY=true` |
| B28 以前 | 最佳努力维护；V5R21 是已验证的旧版本案例 |

安装目录中存在 DLL 或 TypeLib 不等于 GPS/GAS/EST 许可证可用。第一次真实许可证检查是创建并导入 `CATAnalysis` 文档。版本差异与能力模型见
[docs/compatibility-and-architecture.md](docs/compatibility-and-architecture.md)。

## 已知限制

- 不开放任意 Python、CATScript、Shell 或原始 COM 方法执行。
- `catia_capture_view` 只返回 BMP，以保持 CATIA V5 Automation 的稳定兼容。
- 0.3.0 版本只提供恒定半径边倒圆。变半径、面、三切线与曲面倒圆均未开放，也不包含倒角工具。
- `catia_inspect_edges` 返回的是当前拓扑快照。其从 1 开始的索引不是永久拓扑 ID，上游几何变化后必须重新检查。

<details>
<summary>Pocket 方向、BMP 截图与边倒圆细节</summary>

`catia_add_pocket` 默认 `reverse=false`。原点平面草图需要朝草图法向另一侧的材料切除时，设置 `reverse=true`。返回值给出切除方向、以 mm³ 表示的切除前后体积、`removed_volume_mm3` 与 `material_removed`。体积没有下降时返回 `status="no_material_removed"` 并附警告，不会把无效切除表述为已验证成功。

`catia_capture_view` 使用 `catCaptureFormatBMP`（数值 `4`），并在返回 `is_image=true` 前检查文件头是否为 `BM`。CATIA 静默生成非图片内容时会返回错误。

执行倒圆前先调用 `catia_inspect_edges`。工具搜索 `body_name` 的当前三维实体拓扑，排除草图线，也可限定到具名的 `source_feature_name`。`catia_add_edge_fillet` 使用 `radius_mm`、一个或多个 `edge_indices`，以及 `propagation="minimal"` 或 `"tangency"` 创建原生 Part Design `ConstRadEdgeFillet`。多边输入保持调用顺序，因为选择顺序可能影响 CATIA 的原生结果。返回值会回读半径、传播模式、`ObjectsToFillet` 数量与倒圆前后实体体积。

</details>

## 文档

| 文档 | 用途 |
|---|---|
| [docs/safe-export.zh-CN.md](docs/safe-export.zh-CN.md) | 防覆盖、临时文件验证与超时恢复 |
| [docs/advanced-surfaces.zh-CN.md](docs/advanced-surfaces.zh-CN.md) | GSD 工具契约 |
| [docs/compatibility-and-architecture.md](docs/compatibility-and-architecture.md) | 版本处理、能力模型与旧版本说明 |
| [tests/README.md](tests/README.md) | 测试文件说明 |

## 许可与来源

MIT 许可证。本服务基于
[Cai-aa/CAD-Agent-Hub](https://github.com/Cai-aa/CAD-Agent-Hub) 的 CATIA 段修改而来。来源与修改说明见 [NOTICE](../../NOTICE)，许可证全文见 [LICENSE](../../LICENSE)。
