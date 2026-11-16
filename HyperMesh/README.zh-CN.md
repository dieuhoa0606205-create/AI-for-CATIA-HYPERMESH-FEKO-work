# HyperWorks MCP

在 Windows 上通过本地 MCP 服务，让 AI 执行代理操作 Altair HyperMesh、HyperView、HyperGraph、HyperStudy、OptiStruct 与 Radioss。

[English](README.md) · [架构说明](ARCHITECTURE.md) · [更新日志](../CHANGELOG.md)

服务在工作区中管理项目，启动 HyperMesh 或批量作业，并通过带身份校验的进程内扩展完成实时几何与网格操作。修改模型的操作均以类型化接口提供，并在执行前写入检查点。服务不开放任意 Python、Tcl 或 Shell 执行。

当前版本：Python 包 `0.13.0`，进程内扩展 `0.28.0`。

## 可以完成的工作

- 将 CATIA、STEP、IGES 与 Parasolid 文件导入正在运行的 HyperMesh 会话。
- 划分曲面与实体网格，读取网格质量，修复选定网格，并在失败时回滚检查点。
- 导出供 FEKO 使用的分组 `.fhm` 网格。材料对由闭合材料壳与绕数判定，不依赖距离阈值。
- 以一次预设调用完成 CATIA → HyperMesh → FEKO 全链。
- 建立类型化的 OptiStruct 与 Radioss 模型，提交作业，并读取真实的任务状态、日志与产物。
- 在 HyperView 与 HyperGraph 中后处理：云图、实体查询、PNG 证据、CSV 时序与 HTML/JSON 报告。
- 生成类型化的 HyperStudy 内部数学研究。

## 环境要求

- Windows 10 或 11。
- 已安装并具备许可证的 Altair HyperWorks，且包含 HyperMesh。`HYPERWORKS_HOME` 应指向包含 `hwdesktop` 的版本目录；安装求解器时还应包含 `hwsolvers`。
- Python 3.10 及以上。
- 支持 STDIO 的 MCP 客户端，例如 Claude Code 或 Codex。

`HYPERWORKS_HOME` 可从 HyperWorks 桌面快捷方式的目标中查看，也可在常见的 Altair 安装目录下搜索 `runhwx.exe`。不要复制其他电脑上的安装路径。`probe_environment.py` 会报告实际可用的桌面、批量与求解器启动器。

## 安装

```powershell
cd HyperMesh
uv sync --extra dev

$env:HYPERWORKS_HOME = '<HYPERWORKS_INSTALL_ROOT>'
$env:HYPERWORKS_MCP_WORKSPACE = Join-Path `
  ([Environment]::GetFolderPath('MyDocuments')) 'HyperWorksMCP\workspace'

.\.venv\Scripts\python.exe .\probe_environment.py
```

若不使用 `uv`，任何 Python 3.10 以上的虚拟环境均可。

将服务注册到 MCP 客户端。Codex 配置示例见
[`examples/codex_config.example.toml`](examples/codex_config.example.toml)，也可执行：

```powershell
.\register_codex_mcp.ps1 `
  -PythonExe "$PWD\.venv\Scripts\python.exe" `
  -HyperWorksHome $env:HYPERWORKS_HOME `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

安装进程内扩展：

```powershell
.\install_hyperworks_extension.ps1 -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

重启 HyperMesh。若扩展未自动启用，在 `File > Extension Manager` 中启用
`HyperWorks MCP Bridge`，然后验证实时会话：

```powershell
.\.venv\Scripts\python.exe .\probe_live_bridge.py
```

实时桥接只监听 `127.0.0.1`，每个请求校验随机生成的令牌，并通过 HyperWorks 的 Qt 主线程执行白名单 `hm` 接口。

## 快速开始：CATIA → HyperMesh → FEKO

预设 `run_catia_feko_workflow` 依次完成导入、删除实体、去除重复面、删除排除面、缝合、划分、判定材料对、按边界组建组、搬运单元与导出。

必需输入：

| 输入 | 含义 |
|---|---|
| `project_id` | 已放入 CAD 文件的项目 |
| `input_name` | 项目内的文件名 |
| `surface_materials` | 曲面标签到材料名的完整映射，须覆盖全部带材料曲面 |
| `drop_surfaces` | 去重后要删除的曲面标签 |
| `face_tags` | 贴在面上、没有体积的边界条件，例如 PEC 或阻抗面 |
| `element_size` | 目标单元尺寸 |
| `export_name` | 以 `.fhm` 结尾的输出文件名 |
| `mode` | 首次运行用 `verification`，同一几何复跑用 `general` |

材料对尚未确定时使用 `mode="verification"`。该模式对全部带材料曲面划分参考网格，包括重复面与后续要删除的面，因为每种材料必须构成闭合壳。写出配对表后，删除参考网格并在同一模型中继续生产段。同一几何再次运行时使用 `mode="general"`，直接复用已有的配对表。

以下规则不可省略：

- 导入前模型必须为空，否则实体编号会整体偏移。
- `surface_materials` 必须覆盖全部带材料曲面，包括重复面与后续要删除的面。
- 必须传入 `face_tags`。省略后，PEC、阻抗边界与对称面等属性会从组名中静默丢失。
- 有序材料对不得合并。负法向侧与正法向侧属于不同的 FEKO 边界组。
- 不得按最近距离或共享边拓扑判定材料对。
- 不得为处理 `undecided` 标签而拆分 CAD 曲面，应把清单交给操作者。
- 只允许删除空组件。组件内仍有单元、曲面、实体、线或点时，服务会在写检查点之前拒绝删除。

完整操作提示词见
[`PROMPTS/catia-feko-workflow.md`](PROMPTS/catia-feko-workflow.md)。

## 工具分组

| 范围 | 代表工具 |
|---|---|
| 环境与项目 | `get_environment`、`create_project`、`import_project_file` |
| 实时会话 | `get_live_bridge_status`、`get_live_session_info`、`get_live_model_summary` |
| 几何与网格 | `import_live_cad`、`automesh_live_surfaces`、`solid_map_live_solids`、`tetra_mesh_live_solids`、`get_live_mesh_quality`、`repair_live_mesh_quality` |
| FEKO | `export_live_feko_mesh`、`classify_material_pairs`、`run_catia_feko_workflow` |
| 连接 | `create_live_rigid_link`、`create_live_rbe3`、`create_live_weld`、`create_live_spot_weld` |
| 求解作业 | `prepare_analysis_template`、`submit_solver_job`、`get_job_status`、`tail_job_log`、`get_solver_result_artifacts` |
| 后处理 | `postprocess_solver_result_in_hyperview`、`extract_solver_time_history_in_hypergraph`、`generate_solver_job_report` |
| HyperStudy | `prepare_hyperstudy_math_study`、`generate_hyperstudy_study` |

每次几何导入或创建、自动划分、连接创建、求解卡创建与模型载入，都会在
`<HYPERWORKS_MCP_WORKSPACE>\.hyperworks_mcp\checkpoints` 下写入检查点。若 HyperMesh 接口报错或未产生新实体，桥接会自动恢复该检查点。手动回滚使用 `rollback_live_checkpoint`，并要求 `confirm=true`。

## 求解流程

OptiStruct 与 Radioss 作业通过类型化模板建立，不直接编写原始求解卡。典型的 OptiStruct 流程为：

1. `create_project`
2. `prepare_optistruct_cantilever_analysis`
3. 取得明确同意后调用 `submit_solver_job`
4. `get_job_status`、`tail_job_log`、`get_solver_result_artifacts`
5. `postprocess_solver_result_in_hyperview`

Radioss 显式冲击流程使用 `prepare_radioss_block_impact_analysis` 生成成对的 Starter/Engine 输入文件，提交后可调用 `audit_radioss_explicit_job` 检查终止状态、能量误差、附加质量、时间步、负体积与穿透。

<details>
<summary>已验证的求解模板</summary>

- `optistruct.linear_static_solid`
- `optistruct.normal_modes_solid`
- `optistruct.linear_buckling_solid`
- `optistruct.multi_case_static_solid`
- `optistruct.gap_contact_static_solid`
- `optistruct.uniform_thermal_stress_solid`
- `radioss.explicit_block_impact`
- `radioss.plate_impact_solid`
- `radioss.drop_weight_solid_surrogate`
- `radioss.solid_axial_collision`

其中两个 `*_surrogate` 条目是实体、初速度替代夹具，不代表重力驱动的落锤运动，也不代表薄壁壳碰撞盒的保真度。以下模板可被发现，但会明确返回不可运行：
`radioss.three_point_bending`、`radioss.tube_crush`、
`radioss.thin_wall_axial_collision`、`radioss.vehicle_crash_subsystem` 与
`hyperstudy.template_doe_optimization`。

</details>

`*_e2e_smoke.py` 脚本会提交真实求解作业，可能消耗许可证，仅在确有需要时运行。

## 启动选择窗口

`runhwx.exe` 可能在 HyperMesh 启动前显示产品选择窗口。在操作者选择产品并点击 **Create Session** 之前，进程已经存在，外观正常，但扩展尚未加载，实时桥接也不会应答。

启动前可用 `get_hypermesh_launcher_setting` 查询设置。`launch_hypermesh` 返回的 `launcher` 对象会说明是否找到了选择窗口。若桥接仍不应答，`wait_for_live_bridge` 可区分“尚无会话”与“桥接故障”。经操作者同意后，可调用 `set_hypermesh_launcher_setting(show=false, confirm=true)` 关闭选择窗口；每个设置文件在改写前都会备份。

## 测试

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
```

测试共 106 项，耗时约 3 秒，不启动 HyperWorks、不提交求解作业，也不消耗许可证。各测试文件覆盖的范围见
[`tests/README.md`](tests/README.md)。

## 已知限制

- 链路止于 `.fhm` 导出。CADFEKO 导入与边界条件设置不在本仓库范围内。
- 单次创建调用最多创建 5,000 个节点或单元。
- 桥接响应上限为 4 MiB。网格回执返回真实的 `element_count`，并附最多 2,000 个单元编号的抽样。
- 通用连接实体化与紧固件仍受配置限制。
- 在已有网格的模型上以 `keep_existing_mesh=false` 重划时，单元编号可能被复用并触发回滚。应先退回无网格检查点再划分。
- 圆柱 O-grid 工具是有意限定的径向拓扑，不是交互式 HyperMesh O-grid 面板的替代。

## 文档

| 文档 | 用途 |
|---|---|
| [AGENTS.md](../AGENTS.md) | AI 执行代理入口 |
| [PROMPTS/catia-feko-workflow.md](PROMPTS/catia-feko-workflow.md) | CATIA → FEKO 完整操作提示词 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 服务、扩展、工作区与检查点设计 |
| [ADVANCED_CAPABILITY_AUDIT.md](ADVANCED_CAPABILITY_AUDIT.md) | 连接、假人、安全带与气囊的能力边界 |
| [tests/README.md](tests/README.md) | 测试文件说明与免许可证测试命令 |
| [CHANGELOG.md](../CHANGELOG.md) | 阶段变更与版本记录 |

## 许可与来源

MIT 许可证。本目录基于
[Cai-aa/CAE-Agent-Hub](https://github.com/Cai-aa/CAE-Agent-Hub) 的 HyperWorks 模块修改而来。来源与修改说明见 [NOTICE](NOTICE)，许可证全文见 [LICENSE](LICENSE)。
