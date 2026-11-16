# FEKO

本目录预留用于 FEKO 自动化工具、配置与文档。目前尚未提供 MCP 服务，也没有可运行的代码。

## 当前状态

HyperMesh 段可以把分组网格导出为 `.fhm` 文件。CADFEKO 导入、边界条件设置、求解提交与结果读取均不在本仓库内。导出成功只表示网格文件已经生成，不代表 FEKO 模型已经建立或仿真已经完成。

## 计划范围

后续工作可能包括：

- 在 CADFEKO 中导入 `.fhm` 网格。
- 按 HyperMesh 生成的组名设置 PEC、阻抗边界与对称面。
- 提交求解作业并读取结果。

以上内容尚未实现。在提供类型化工具和实机验证之前，本目录不会把这些能力列为可用。

## 相关文档

- [HyperMesh 段说明](../HyperMesh/README.zh-CN.md)
- [CATIA → HyperMesh → FEKO 工作流提示词](../HyperMesh/PROMPTS/catia-feko-workflow.md)
- [仓库更新日志](../CHANGELOG.md)
