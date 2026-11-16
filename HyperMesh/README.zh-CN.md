# HyperWorks MCP

面向 Altair HyperWorks 的本地 FastMCP 服务，以工作区为安全边界，提供可审计的自动化能力。

服务能够发现本机安装的 HyperMesh、HyperStudy 与求解器启动器，管理相互隔离的项目，通过 HyperMesh Batch 运行经筛查的 Tcl 脚本，启动 HyperMesh 或 HyperView，提交类型化的 OptiStruct、Radioss 作业，并返回真实的任务状态、有界日志、取消结果与产物清单。服务还包含一个带身份校验的进程内扩展，在 HyperWorks 的 Qt 主线程上执行白名单 `hm` 接口，包括有界地创建节点、单元与材料，受控载入 `.hm` 模型以及刷新视图。实时桥接另提供受控的块体与圆柱几何、工作区内的 STEP/IGES/Parasolid/CATIA 导入、曲率与邻近控制的曲面网格、原生 Solid Map、有界的圆柱径向 O-grid、网格质量摘要与检查点。连接方面提供带检查点的刚性连接、RBE3 蜘蛛与节点焊接，另提供类型化的 HyperStudy 内部数学研究生成器，支持变量、响应、DOE 与 GRSM 目标。

服务包含经求解器验证的 OptiStruct SOL 101 纵向链路：类型化的 MAT1/PSOLID/CHEXA 属性、分布力加载、SPC1 约束、可选 PGAP/CGAP 接触、子工况与输出控制、异步求解、产物分类，以及在 HyperView 中读取云图、查询实体并导出 PNG 证据。0.8 版加入真实 Radioss 显式动力学链路：成对的 Starter/Engine 输入文件、LAW2/SOLID/BRICK 属性、初速度、BCS 约束、TYPE7 接触、时序与 H3D 输出控制、实际求解提交、产物分类，以及针对正常终止、能量误差、附加质量、时间步、负体积与穿透的可机读质量门限。HyperView 后处理可选择首帧、末帧或指定结果帧。0.9 版加入带检查点的求解卡实体（Property、Loadcol、Loadstep、Set、Constraint 与接触卡）、类型化的力/力矩/SPC/温度/热流/速度/加速度与压力创建、原生结构四面体网格、带前后对比证据的有界质量修复、带属性的点焊、可复用模板的发现与调用、全帧 HyperGraph 时序、HTML/JSON 作业报告，以及显式给出位置与关联实体的 Spot/Seam/Area/Bolt 连接意图。0.10 版扩展可复用求解模板库，加入经求解器验证的 OptiStruct 模态、线性屈曲、多工况线性静力、CGAP/PGAP 接触静力与均匀热应力模板，以及 Radioss 平板冲击、初速度落锤替代与双实体轴向碰撞夹具。每个模板都标明验证状态；三点弯曲、管压溃、薄壁碰撞盒、整车子系统与求解器联动的 HyperStudy 研究仍明确列为不可运行，直至对应几何或模型耦合夹具完成。

0.11 版加入 `classify_material_pairs`：以每种材料的闭合壳与绕数判定每张带标签 FEKO 曲面两侧的材料，并接受理想导体、阻抗边界等没有体积、无法由体材料判定的面属性。0.12 版加入启动选择窗口的识别：`get_hypermesh_launcher_setting` 报告 `runhwx.exe` 启动前是否会显示选择窗口，`launch_hypermesh` 说明该窗口是否真的出现在屏幕上，`wait_for_live_bridge` 区分“尚无会话”与“桥接故障”，避免把等待操作的窗口误判为启动失败或扩展未加载。0.12.1 版将工作流的回滚重导入改为 `clear_live_reference_mesh`：逐个删除参考组件及其单元，生产段在同一模型内继续，因为回读 `.hm` 检查点会破坏该会话中的 CATIA 读取器。0.12.2 版为工作流加入空模型前置检查，模型内已有曲面或单元时在导入前拒绝启动，避免实体编号整体偏移；`stop_after` 写入不存在的步骤名时同样在启动前报错；同时修正支持的 CAD 格式清单，补入 CATIA。0.12.3 版将有界抽样用于曲面划分与原生 Solid Map 的单元编号回执：`element_count` 保持真实总数，`element_ids` 最多返回 2,000 个并附 `element_ids_truncated`，任意规模的网格都远低于桥接 4 MiB 的响应上限。0.13.0 版允许 `delete_live_entities` 删除空组件。HyperMesh 删除组件时连同其内容一并删除，因此扩展先核对组件内没有单元、曲面、实体、线与点，不符合者在写检查点之前即拒绝；删除后核对单元、曲面、实体与节点的编号集合未减少。

## 找到本机 HyperWorks 安装根目录

不要复制其他电脑上的安装路径。`HYPERWORKS_HOME` 应指向包含 `hwdesktop`、并在安装求解器时包含 `hwsolvers` 的 Altair 版本目录。典型结构如下：

```text
<HYPERWORKS_INSTALL_ROOT>\
  hwdesktop\hwx\bin\win64\runhwx.exe
  hwdesktop\hm\bin\win64\hmbatch.exe
  hwdesktop\hst\bin\win64\hstbatch.exe
  hwsolvers\scripts\optistruct.bat        # 可选
  hwsolvers\scripts\radioss.bat           # 可选
```

先查看 HyperWorks 或 HyperMesh 桌面快捷方式的“目标”。也可以用 PowerShell 搜索常见的 Windows 安装位置：

```powershell
$searchRoots = @(
  "$env:ProgramFiles\Altair"
  "$env:ProgramW6432\Altair"
  "$env:SystemDrive\Altair"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique

Get-ChildItem -Path $searchRoots -Filter runhwx.exe -File -Recurse `
  -ErrorAction SilentlyContinue |
  Where-Object FullName -Match '\\hwdesktop\\hwx\\bin\\win64\\runhwx\.exe$' |
  Select-Object -ExpandProperty FullName
```

若安装在自定义位置，则搜索其上级目录。从结果中去掉末尾的 `\hwdesktop\hwx\bin\win64\runhwx.exe`，即得到 `<HYPERWORKS_INSTALL_ROOT>`。`probe_environment.py` 会报告实际可用的桌面、批量与求解器启动器；缺失的求解器启动器按能力限制报告，不依据本仓库的假设判定为存在。

## 安装与本地验证

```powershell
uv sync --extra dev
$env:HYPERWORKS_HOME = '<HYPERWORKS_INSTALL_ROOT>'
$env:HYPERWORKS_MCP_WORKSPACE = Join-Path `
  ([Environment]::GetFolderPath('MyDocuments')) 'HyperWorksMCP\workspace'
.\.venv\Scripts\python.exe .\probe_environment.py
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\stdio_smoke.py
# 以下命令会短暂启动 hmbatch，可能消耗许可证。
.\.venv\Scripts\python.exe .\hmbatch_smoke.py --run
```

使用 [`examples/codex_config.example.toml`](examples/codex_config.example.toml)，或执行：

```powershell
.\register_codex_mcp.ps1 `
  -PythonExe "$PWD\.venv\Scripts\python.exe" `
  -HyperWorksHome $env:HYPERWORKS_HOME `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

安装进程内扩展：

```powershell
.\install_hyperworks_extension.ps1 `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

安装器还会在当前用户的 Altair 扩展注册表中登记 `HyperWorks MCP Bridge`。随后重启 HyperMesh；若未自动启用，可在 `File > Extension Manager` 中启用，再验证实时会话：

```powershell
.\.venv\Scripts\python.exe .\probe_live_bridge.py
```

实时桥接只绑定 `127.0.0.1`，每个请求都校验随机生成的令牌，只允许固定方法，并通过 Qt 主线程队列执行 HyperWorks 接口。不开放 `eval`、任意 Python、Shell 或 Tcl。

## 启动选择窗口

`runhwx.exe` 可能在 HyperMesh 启动前先显示启动选择窗口。该窗口等待选择产品并点击 **Create Session**。在点击之前，进程已经存在、主窗口标题正常，但没有会话，扩展未加载，实时桥接也不应答，外观与扩展故障相同，实际并非扩展故障。

是否显示选择窗口由用户设置 `launcher_show_on_startup` 决定，存放于 `~/.altair/<version>/hwx/settings.xml`、`~/.altair/<version>/hwx/profiles/` 下的当前配置，以及 `~/Documents/hwsettings.xml`。任一文件中的值为 `1`，即显示选择窗口。

启动前先查询：

```text
get_hypermesh_launcher_setting
```

返回结果会列出每个文件及其取值，并说明当前屏幕上是否有选择窗口。两种处理方式：

- 保留设置，由操作者在窗口中选择产品并点击 **Create Session**；
- 仅在操作者同意后，调用一次 `set_hypermesh_launcher_setting(show=false, confirm=true)`。每个设置文件在改写前都会备份，除此之外不做其他改动。

`launch_hypermesh` 返回 `launcher` 对象，包含 `will_show`、`window_found`、`window_titles`、`setting_files` 与 `next_step`。应读取该对象判断程序是否真正启动，而不是假定启动成功。若桥接仍不应答，调用 `wait_for_live_bridge` 轮询，并报告是否有选择窗口在等待，从而区分“尚无会话”与“桥接故障”。

## 安全使用流程

先调用 `get_environment`，创建项目，导入明确的文件，编写经筛查的 Tcl 脚本，然后在执行 `run_hmbatch`、`launch_hypermesh`、`submit_solver_job` 或 `cancel_job` 前请求批准。监控真实的任务状态与日志；服务不会猜测进度或收敛情况。

Tcl 中的进程、网络、动态运行时命令、直接文件访问、绝对路径与上级路径、环境变量访问均被拒绝。这是纵深防御，不等同于操作系统沙箱；脚本执行前仍应人工审阅。服务不提供任意 Shell、PowerShell、Python 或原始命令行入口。详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 实时几何与网格

0.9 版的实时流程只使用类型化的白名单操作：

1. 调用 `create_project`，再调用 `import_project_file` 导入 CAD 输入。
2. 调用 `import_live_cad`，使用返回的 `surface_ids` 或 `solid_ids`。
3. 壳网格调用 `automesh_live_surfaces`，显式给出单元尺寸。
4. 可映射实体调用 `solid_map_live_solids`。拓扑是否可映射由 HyperMesh 判定；失败时自动恢复到检查点。
5. 受控的圆柱验证网格调用 `create_live_cylindrical_ogrid`。该工具在 Penta6 核心外生成环形 Hex8 层，不代表 HyperMesh 交互式 O-grid 面板的通用能力。
6. 一般实体调用 `tetra_mesh_live_solids`，内部使用原生结构 `Model.tetmesh` 接口，并拒绝非正体积结果。
7. 调用 `get_live_mesh_quality` 检查质量；选中的网格可通过 `repair_live_mesh_quality` 平滑，返回前后对比证据，并可回滚。
8. 需要 FEKO 边界组时，将每张 CAD 曲面划入独立组件，用 `export_live_feko_mesh` 导出，再以标签到材料的映射调用 `classify_material_pairs`。每种材料视为闭合壳，两侧材料由绕数判定，结果不依赖单元尺寸或缝合质量。

第 1 至 8 步均可单独调用。若团队需要把整条链固定为一个可重复的预设，`run_catia_feko_workflow` 按以下顺序一次执行：导入、删除实体、去除重复面、删除排除面、缝合、划分、判定材料对、每个边界组建立组件、搬运单元、导出。每一步都写检查点，并提供 `stop_after` 以便中途检查。`mode="verification"` 计算材料对：先对全部带材料曲面划分参考网格（包含重复面与后续要排除的面，因为材料必须构成闭合壳），导出参考网格并判定，写出配对表，然后删除参考组件及其单元，在同一模型中继续生产段。每次运行只导入一次 CAD 文件。`mode="general"` 跳过参考段，直接读取配对表，同一几何的重复运行较快。工作流要求导入前模型为空；两侧为同一介质的面列入 `same_medium`，不进入分组。

每次几何导入或创建、每次自动划分前，桥接都会在 `<HYPERWORKS_MCP_WORKSPACE>\.hyperworks_mcp\checkpoints` 下写入由桥接管理的 `.hm` 检查点。若 HyperMesh 接口报错或未产生新实体，桥接自动载回该检查点。手动回滚使用 `rollback_live_checkpoint`，需要 `confirm=true`。

CAD 路径必须位于 MCP 工作区内。实时导入支持的格式为 STEP、IGES、Parasolid 与 CATIA（CATPart、CATProduct）。导入选项使用有界的 `name=value` 字符串；任意命令、任意 Python/Tcl 仍不可用。

## 连接与 HyperStudy

创建连接前先调用 `get_live_connection_capabilities`。已验证的实时操作包括 `create_live_rigid_link`、`create_live_rbe3`、`create_live_weld` 与带属性的 `create_live_spot_weld`。`create_live_connector` 依据节点或线位置、组件或曲面关联，创建 Spot、Seam、Area 或 Bolt 连接意图；在提供求解器专用的有限元控制参数之前，该工具明确返回 `realized=false`。每次修改型连接调用都会写检查点；若 HyperMesh 未暴露出预期的连接或单元实体，则自动回滚。通用连接实体化与紧固件仍受配置限制。HyperMesh 原生紧固件创建接口目前仅对 Abaqus 配置提供文档，桥接不将其宣传为求解器中立的能力。

0.9.1 版在导出 HyperGraph 图像后恢复 HyperView 结果页。HyperGraph 页面保留在会话中，扩展仍由兼容的客户端承载，后续 MCP 调用不会丢失本机桥接。

安全的 HyperStudy 配置流程为：调用 `create_project`，再调用 `prepare_hyperstudy_math_study`，提供类型化的连续变量、表达式响应与可选的 DOE/GRSM 定义。`generate_hyperstudy_study` 仅通过 Altair `hstpy.bat` 执行 MCP 生成的脚本，并在项目内创建 `.hstudy`。该工具不会自动评估 DOE 或优化；任意 Python 被拒绝。

## 通用求解卡、载荷、时序与报告

`create_live_solver_card_entity` 可创建有界的 Property、Loadcol、Loadstep、Set、Constraint、Contactbehavior、Contactgroup、Contactsurf 或 Group 卡。卡片映像与求解器数据名仍取决于当前求解器配置；引用一律使用显式的类型化 `entity_type`/`entity_id` 对象，而非未校验的整数。常见载荷链使用 `create_live_nodal_load`、`create_live_pressure_load` 与 `create_live_loadstep`。所有修改模型的调用都会先写检查点。

用 `list_analysis_templates`、`get_analysis_template` 与 `prepare_analysis_template` 发现并调用已验证的模板，无需在客户端写死工具名。作业完成后，`extract_solver_time_history_in_hypergraph` 遍历全部可用结果帧，写出 CSV，生成 HyperGraph XY 曲线并截取 PNG 证据。`generate_solver_job_report` 将真实任务状态、分类后的产物、可用的 Radioss 质量审计数据与选定项目截图汇总为 HTML 与 JSON。

0.10.0 版经真实求解验证的模板集为：

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

其中两个带 `*_surrogate` 的条目是受控的实体、初速度替代夹具，不代表重力驱动的落锤运动，也不代表薄壁壳碰撞盒的保真度。`radioss.three_point_bending`、`radioss.tube_crush`、`radioss.thin_wall_axial_collision`、`radioss.vehicle_crash_subsystem` 与 `hyperstudy.template_doe_optimization` 可被发现，但会明确返回不可运行。这样可以按完整路线规划，而不会用两块实体的基准算例冒充另一种物理试验。

## OptiStruct 与 HyperView 端到端流程

0.9 版保留完整的类型化线性静力验证配置，不开放原始卡片或命令执行通道：

1. `create_project`
2. `prepare_optistruct_cantilever_analysis`：生成材料、属性、网格、载荷、约束、可选接触与 SOL 101 控制链
3. 明确批准后调用 `submit_solver_job`
4. `get_job_status`、`tail_job_log`、`get_solver_result_artifacts`
5. `postprocess_solver_result_in_hyperview`：在新页面载入生成的 H3D，绘制指定的标量结果，返回图例极值与实体查询行，并保存项目内的 PNG 截图

`optistruct_e2e_smoke.py` 仅在可能消耗真实 OptiStruct 许可证时运行。该验证夹具包含一组 PGAP/CGAP 接触，并要求实际产生 H3D/OP2 结果才算通过。HyperView 只接受工作区内项目或作业的文件，桥接仍不开放任意 HWC、Tcl 或 Python 执行。

## Radioss 显式冲击端到端流程

0.8 版的配置是以 kg-mm-ms 为单位的受控、经求解器验证的块体冲击基准：

1. `create_project`
2. `prepare_radioss_block_impact_analysis`：生成 `*_0000.rad` 与 `*_0001.rad`
3. 检查返回的 LAW2、属性、载荷、约束、接触与控制链
4. 批准后以 `solver="radioss"` 调用 `submit_solver_job`
5. 监控 `get_job_status` 与 `tail_job_log`
6. 调用 `get_solver_result_artifacts` 检查 H3D、`A###`、`T##`、OUT 与重启文件
7. 调用 `audit_radioss_explicit_job` 评估实际的 Engine 输出
8. 0.9 扩展加载后，以 `simulation="first"`、`"last"` 或可用的整数帧号调用 `postprocess_solver_result_in_hyperview`

`radioss_e2e_smoke.py` 会消耗真实 Radioss 许可证。2026 年验证的回归算例包含 45 个节点与 12 个 HEXA8 实体，Starter 以零错误零警告完成，Engine 正常结束，生成 11 帧动画以及 H3D 与时序文件，并以最大绝对能量误差 11.4%、附加质量误差 0%、最小时间步 2.853e-4 ms 的正值通过默认门限。以上数字只描述随仓库提供的回归夹具；MCP 对每个新作业都重新解析，不沿用这些数值。

连接、Design Explorer、假人、安全带与气囊的能力边界见 [ADVANCED_CAPABILITY_AUDIT.md](ADVANCED_CAPABILITY_AUDIT.md)。

## 当前边界

0.10 版为六类 OptiStruct 实体分析与四类 Radioss 实体冲击夹具提供经过验证的受控链路，同时提供受控连接与类型化的 HyperStudy 内部数学配置。这不代表在没有专用几何与耦合配置的情况下，可以合成任意碰撞、乘员、气囊、材料、失效或求解器联动优化模型。受控的实时几何与网格能力保持可用。单次调用最多创建 5,000 个节点或单元；单元必须引用已存在的正节点编号。实时载入模型只接受已放入 MCP 项目的 `.hm` 文件，并要求显式 `replace_current=true`。视图刷新只执行固定的 `hm_viewfit` 与 `hm_redraw` 命令，任意 Python 与 Tcl 仍不可用。原生 Solid Map 只适用于 HyperMesh 能映射的几何；圆柱 O-grid 工具是有意限定的径向拓扑，不是通用交互式 O-grid 的替代。HyperView 的云图、结果查询与截图可用于工作区内的结果文件；通用 CAD 清理与修改模型的安全/气囊操作仍需等待求解器专用夹具，属于后续工作。
