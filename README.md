# AI for CATIA / HyperMesh / FEKO

在 Windows 上把 CATIA 零件导入 HyperMesh，完成清理、划分与分组，导出 FEKO 可读网格的本地 MCP 工具链。

仓库按软件分段，每个目录可以单独阅读和安装。

| 目录 | 内容 | 状态 |
|---|---|---|
| [CATIA](CATIA/) | CATIA V5 建模与原生 Analysis MCP 服务 | 可用 |
| [HyperMesh](HyperMesh/) | HyperWorks MCP 服务、进程内扩展与 CATIA → FEKO 工作流 | 主要开发段 |
| [FEKO](FEKO/) | FEKO 自动化预留目录 | 尚未提供 MCP 服务 |

## 当前可用

CATIA → HyperMesh → FEKO 的第一阶段链路已实跑：CATIA 原生文件导入 HyperMesh，删除实体与重复面，缝合，划分网格；以每种材料的闭合壳与绕数判定曲面两侧的材料，按有序材料对和面边界条件建立分组；导出 `.fhm` 文件。

HyperMesh Python 包版本 `0.13.0`，进程内扩展版本 `0.28.0`。链路止于导出，CADFEKO 导入与边界条件设置不在本仓库内。

HyperMesh 段另外提供 OptiStruct 与 Radioss 的类型化建模、作业提交、状态读取、结果后处理，以及 HyperStudy 内部数学研究配置。已验证的模板与仍受限制的能力在
[HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md) 中列明。

## 快速开始

1. 按 [HyperMesh/README.zh-CN.md](HyperMesh/README.zh-CN.md) 安装 Python 包与进程内扩展。
2. 设置 `HYPERWORKS_HOME` 与 `HYPERWORKS_MCP_WORKSPACE`，运行 `probe_environment.py`。
3. 重启 HyperMesh，运行 `probe_live_bridge.py`，确认实时桥接可用。
4. 按 [CATIA → FEKO 工作流提示词](HyperMesh/PROMPTS/catia-feko-workflow.md)调用 `run_catia_feko_workflow`。

首次运行使用 `mode="verification"` 判定材料对，同一几何复跑使用 `mode="general"`。两种模式都要求导入前模型为空。

## 给 AI 执行代理

先读 [AGENTS.md](AGENTS.md)。其中列有仓库范围、环境要求、工具边界与工作流规则。完整操作步骤见
[HyperMesh/PROMPTS/catia-feko-workflow.md](HyperMesh/PROMPTS/catia-feko-workflow.md)，测试说明见
[HyperMesh/tests/README.md](HyperMesh/tests/README.md)。

## 文档

| 文档 | 用途 |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | 阶段变更与版本记录 |
| [HyperMesh/ARCHITECTURE.md](HyperMesh/ARCHITECTURE.md) | 服务、扩展、工作区与检查点设计 |
| [HyperMesh/ADVANCED_CAPABILITY_AUDIT.md](HyperMesh/ADVANCED_CAPABILITY_AUDIT.md) | 连接、假人、安全带与气囊的能力边界 |
| [CATIA/MCP/CATIA/README.zh-CN.md](CATIA/MCP/CATIA/README.zh-CN.md) | CATIA 服务的安装、工具与验证方法 |

## 许可与来源

CATIA 目录沿用上游 MIT 许可证，来源与修改说明见 [CATIA/NOTICE](CATIA/NOTICE)。HyperMesh 目录的来源与修改说明见 [HyperMesh/NOTICE](HyperMesh/NOTICE)。许可证全文分别位于各目录的 `LICENSE` 文件。
