# AGENTS

本仓库面向 Windows 上的 Altair HyperWorks 自动化工作流。执行代理应先读本文件，再读 [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md)。

## 仓库范围

| 目录 | 状态 |
|---|---|
| `CATIA/` | CATIA V5 建模与 Analysis MCP 服务，保留上游段 |
| `HyperMesh/` | 主要开发段：HyperWorks MCP 服务与进程内扩展 |
| `FEKO/` | 占位，尚未提供 MCP 服务 |

当前 HyperMesh Python 包版本 0.13.0，进程内扩展 0.28.0。工作链止于导出 `.fhm` 文件；CADFEKO 导入与边界条件设置不在本仓库内，不得宣称已完成。

## 环境与安装

- Windows，Altair HyperWorks 已安装；`HYPERWORKS_HOME` 指向包含 `hwdesktop` 的版本目录。
- Python 3.10 以上。
- 在 `HyperMesh/` 下执行 `uv sync --extra dev`，或使用已有虚拟环境。
- 安装进程内扩展使用 `install_hyperworks_extension.ps1`；安装后需重启 HyperMesh，并以 `probe_live_bridge.py` 验证实时桥接。

## 测试

```powershell
cd HyperMesh
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
```

单元测试不启动求解器、不消耗许可证。`hmbatch_smoke.py`、`optistruct_e2e_smoke.py`、`radioss_e2e_smoke.py` 等脚本会启动真实进程并可能消耗许可证，执行前须取得明确同意。

## 工具边界

- 只调用服务暴露的类型化工具。不执行任意 Python、Tcl、Shell，不绕过参数校验。
- 实时桥接只监听 `127.0.0.1`，每个请求校验令牌。
- 修改模型前建立检查点；失败时读取返回的检查点路径并回滚。
- 不修改用户设置。启动选择窗口等设置须先征得操作者同意。
- 不实现或规避许可证机制。
- 网格质量、求解收敛与作业完成状态以工具回执和日志为准，不得推断。

## 工作流入口

CATIA → HyperMesh → FEKO 分组网格使用 `run_catia_feko_workflow`。首次运行使用 `mode="verification"`，同一几何复跑使用 `mode="general"`；两种模式都要求导入前模型为空。

必须遵守：

- 传入 `face_tags`，否则 PEC、阻抗边界、对称面等面属性会丢失。
- 有序材料对不得合并；负法向侧与正法向侧属于不同的边界组。
- 材料对不得按最近距离或共享边拓扑判定。
- 不得拆分 CAD 曲面来处理未决面；把清单交给操作者。
- 只删除空组件；组件内仍有单元、曲面、实体、线或点时拒绝删除。

完整提示词见 [HyperMesh/PROMPTS/catia-feko-workflow.md](HyperMesh/PROMPTS/catia-feko-workflow.md)。

## 文档

| 文档 | 用途 |
|---|---|
| [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md) | 安装、工具范围与实时流程 |
| [CHANGELOG.md](CHANGELOG.md) | 阶段变更与版本记录 |
