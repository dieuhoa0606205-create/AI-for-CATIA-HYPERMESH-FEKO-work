# CATIA

CATIA V5 建模与原生 Analysis MCP Server，基于 CAD-Agent-Hub 修改而来。

| 路径 | 用途 |
| --- | --- |
| [`MCP/CATIA`](MCP/CATIA) | CATIA V5 建模与原生 Analysis MCP Server |

该服务通过 Windows 本地 STDIO 接口控制 CATIA V5：零件与装配建模、原生 GSD
线框与曲面操作、材料、导出，以及 CATIA 内部 Analysis/ELFINI 仿真。共 55 个
类型化工具，不开放任意 Python、CATScript、Shell 或 COM 方法执行。

安装与验证方法、工具范围与多版本策略见
[`MCP/CATIA/README.zh-CN.md`](MCP/CATIA/README.zh-CN.md)，英文说明见
[`MCP/CATIA/README.md`](MCP/CATIA/README.md)。

本目录只保留上游项目的 CATIA 段。来源、许可证与修改说明见
[NOTICE](NOTICE) 与 [LICENSE](LICENSE)。
