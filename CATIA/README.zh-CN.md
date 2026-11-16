# CATIA

CATIA V5 建模与原生 Analysis MCP 服务，基于 CAD-Agent-Hub 修改而来。

[English](README.md) · [安装与工具](MCP/CATIA/README.zh-CN.md) · [更新日志](../CHANGELOG.md)

| 路径 | 用途 |
|---|---|
| [`MCP/CATIA`](MCP/CATIA) | CATIA V5 建模与原生 Analysis/ELFINI MCP 服务 |

服务通过本地 STDIO MCP 接口控制 CATIA V5，覆盖零件与装配建模、原生 GSD 线框与曲面操作、材料、导出，以及 CATIA 内部 Analysis/ELFINI 仿真。工具面固定为 55 个类型化工具，不开放任意 Python、CATScript、Shell 或原始 COM 方法执行。

环境要求、安装方法、工具分组与验证命令见
[MCP/CATIA/README.zh-CN.md](MCP/CATIA/README.zh-CN.md)，英文说明见
[MCP/CATIA/README.md](MCP/CATIA/README.md)。

本目录只保留上游项目的 CATIA 段。来源、许可证与修改说明见
[NOTICE](NOTICE) 与 [LICENSE](LICENSE)。
