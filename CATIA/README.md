# CATIA

CATIA V5 modeling and native Analysis MCP server, adapted from CAD-Agent-Hub.

| Path | Purpose |
| --- | --- |
| [`MCP/CATIA`](MCP/CATIA) | CATIA V5 modeling and native Analysis MCP server |

The server controls CATIA V5 through a Windows local STDIO interface: part and
assembly modeling, native GSD wireframe and surface operations, materials, export,
and CATIA internal Analysis/ELFINI simulation. It exposes 55 typed tools and no
arbitrary Python, CATScript, shell, or COM execution.

Install and validation instructions, the supported tool surface, and the
multi-release policy are in [`MCP/CATIA/README.md`](MCP/CATIA/README.md).
Chinese documentation: [`MCP/CATIA/README.zh-CN.md`](MCP/CATIA/README.zh-CN.md).

This folder retains only the CATIA segment of the upstream project. Attribution,
license, and modification notes are in [NOTICE](NOTICE) and [LICENSE](LICENSE).
