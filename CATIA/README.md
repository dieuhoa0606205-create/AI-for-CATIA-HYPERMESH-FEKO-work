# CATIA

CATIA V5 modeling and native Analysis MCP server, adapted from CAD-Agent-Hub.

[中文](README.zh-CN.md) · [Install and tools](MCP/CATIA/README.md) · [Changelog](../CHANGELOG.md)

| Path | Purpose |
|---|---|
| [`MCP/CATIA`](MCP/CATIA) | CATIA V5 modeling and native Analysis/ELFINI MCP server |

The server controls CATIA V5 through a local STDIO MCP interface. It covers
part and assembly modeling, native GSD wireframe and surface operations,
materials, export, and CATIA internal Analysis/ELFINI simulation. The tool
surface is fixed at 55 typed tools; arbitrary Python, CATScript, shell, and raw
COM execution are not exposed.

Start with [MCP/CATIA/README.md](MCP/CATIA/README.md) for requirements,
installation, the tool groups, and validation commands. The Chinese guide is
[MCP/CATIA/README.zh-CN.md](MCP/CATIA/README.zh-CN.md).

This directory keeps only the CATIA segment of the upstream project.
Attribution, license, and modification notes are in [NOTICE](NOTICE) and
[LICENSE](LICENSE).
