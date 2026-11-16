# AI for CATIA / HyperMesh / FEKO

仓库按软件分段管理，每个软件一个目录。

| 目录 | 内容 |
|---|---|
| CATIA/ | CATIA V5 建模与 Analysis MCP 服务，基于 CAD-Agent-Hub |
| HyperMesh/ | HyperWorks MCP 服务与进程内扩展，含 CATIA → FEKO 分组网格工作流 |
| FEKO/ | 占位，尚未提供 MCP 服务 |

## 当前状态

第一阶段链路已完成并实跑：CATIA 原生文件导入 HyperMesh，去重、缝合、划分，按闭合壳绕数判定材料对，按面边界条件建立分组，导出 FEKO 可读的 `.fhm` 文件。HyperMesh Python 包版本 0.13.0，进程内扩展 0.28.0。

FEKO 段目前只有说明文件。导出到 `.fhm` 为止，CADFEKO 导入与边界条件设置不在本仓库内。

## 快速开始

1. 阅读 [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md) 的安装与验证步骤。
2. 配置 `HYPERWORKS_HOME` 与工作区，安装 Python 包与进程内扩展。
3. 重启 HyperMesh，用 `probe_live_bridge.py` 确认实时桥接。
4. 按[工作流提示词](HyperMesh/PROMPTS/catia-feko-workflow.md)调用 `run_catia_feko_workflow`。

执行代理先读 [AGENTS.md](AGENTS.md)。

## 许可与来源

CATIA 目录沿用上游 MIT 许可证，来源与修改说明见 [CATIA/NOTICE](CATIA/NOTICE)。HyperMesh 目录的来源与修改说明见 [HyperMesh/NOTICE](HyperMesh/NOTICE)。

各阶段的可交付变更见 [CHANGELOG.md](CHANGELOG.md)。
