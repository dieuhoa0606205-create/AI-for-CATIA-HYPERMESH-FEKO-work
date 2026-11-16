# AGENTS

本仓库面向 Windows 上的 Altair HyperWorks 自动化工作流。执行代理应先读本文件，再读 [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md)。

## 仓库范围

| 目录 | 状态 |
|---|---|
| `CATIA/` | CATIA V5 建模与 Analysis MCP 服务，保留上游段 |
| `HyperMesh/` | 主要开发段：HyperWorks MCP 服务与进程内扩展 |
| `FEKO/` | 占位，尚未提供 MCP 服务 |

当前 HyperMesh Python 包版本 0.13.0，进程内扩展 0.29.0。工作链止于导出 `.fhm` 文件；CADFEKO 导入与边界条件设置不在本仓库内，不得宣称已完成。

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

## 操作者决策

以下事项会改变模型或其物理含义，工具不能代为选择。缺少、含糊或与回执矛盾时，必须先停下来询问操作者，得到答复前不得调用修改类工具：

- 曲面到材料的映射、需排除的曲面、仅用于闭合壳参考的曲面。
- 面属性（理想导体、阻抗边界、对称面、涂层等没有体积的赋值）。
- 单元尺寸、单元类型与划分方式。
- 要修改的 CATIA 参数及其新值。
- 未决标签、一致率低于 1.0、同介质面、自由边、重复面与重叠面的处置。
- 删除、回滚、覆盖或另存的选择。

不得从几何、上一次运行或默认值推断这些内容。提问须写明：要决定什么、可选项、每个选项的后果。结果中出现未决项、`operator_report_required` 或需要人工判断的清单时，视为停止点，不得绕过。

## 工作流入口

CATIA → HyperMesh → FEKO 分组网格使用分阶段的 `run_catia_feko_workflow`。`action="start"` 只返回材料对问题、理由与令牌，不导入、不改动模型；把问题交给操作者。`action="classify"` 导入 CAD、建立封闭参考壳、写配对表并停在确认点；`action="produce"` 仅在操作者以 `confirm_pairs=true` 确认配对表后继续；`action="skip"` 记录材料对未判定并停止，不预设后续生产链。参考网格可用粗尺寸 `reference_element_size`，不会进入最终网格。

材料对判定完成前存在拦截门。`remove_live_duplicate_surfaces`、`delete_live_entities`（`surface`、`solid`）、`stitch_live_free_edges` 与 `trim_live_free_edges` 返回 `decision_required` 而不执行。这是停止点：把问题原样交给操作者，用 `resolve_material_pair_decision` 回答，或在 `classify_material_pairs` 成功后自动解除；不得换用其他工具或拆分曲面绕过。经 `import_live_cad` 导入 CATIA 文件同样会打开该门，返回中带 `material_pair_gate`。

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
| [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md) | 安装、工具范围、工作流、测试与限制 |
| [HyperMesh/tests/README.md](HyperMesh/tests/README.md) | 测试文件分工与免许可证测试命令 |
| [CATIA/MCP/CATIA/tests/README.md](CATIA/MCP/CATIA/tests/README.md) | CATIA 测试文件分工 |
| [CHANGELOG.md](CHANGELOG.md) | 阶段变更与版本记录 |

README 只保留入口与操作要点。版本历史、已完成的逐项修改与旧版本细节写入 CHANGELOG，避免在 README 中重复。
