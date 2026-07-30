# AgentLite 开发历程

## 文档目的

本文提供阅读仓库完整 Git 历史的简明路线。详细中间报告保留在对应历史
提交中，当前比赛分支使用中文索引，当前比赛结论汇总于
`RESULTS_SNAPSHOT.md`。

## 开发阶段

| 阶段 | 主要工程成果 |
|---|---|
| v0-v2 | 可复现基线、基于 tokenizer 的核算、初始 StatePool 与 MemoryStore |
| v3-v4 | 交付 Schema、候选准入、输出守卫、重试预算、读租约和生命周期治理 |
| v5.1-v5.11 | 类型化协议桥、持久化、通信治理、访问控制、跨任务评测和时延检测 |
| v5.12 | 覆盖 Team、Agent、消息、Handoff、工具摘要和 Core 边界的原生 AutoGen 接管 |
| v5.13 | 不可变实验绑定、Studio Run 身份、能力级视图、连续性记忆和事实冲突治理 |
| v5.14 | 评审治理、语义保真、类型化可靠性、证据连续性、修订消解和最终交付守卫 |
| v5.15 | 通用语义桥、精确来源跨度、Schema 校验、受控消歧、包加固和最终 openEuler 发行 |

## 证据模型

AgentLite 使用四层证据：

1. 单元测试和机制测试，用于验证确定性契约；
2. 受控 AutoGen 对照实验，用于验证传输与接管行为；
3. 有边界的 Provider 实验，用于观察成本和交付表现；
4. 不可变 openEuler 发行归档，用于验证安装与兼容性。

各层证据回答不同问题。历史提交保留实现和验收边界的演进过程；最终发行
状态只由当前适用的最新门禁确定。

## 最终提交状态

- 版本：`0.5.15`；
- 许可证：Apache-2.0；
- openEuler 最终验收：22 / 22；
- 继承发行验收：15 / 15；
- 完整单元测试：602 / 602；
- wheel 与 sdist：均完成独立检查与隔离安装；
- 公开发布阻塞项：0。

## 追溯入口

- 版本映射：`docs/versioning.md`
- 实现演进：在 Git 历史中查看 `docs/planning/`
- 实验记录：在 Git 历史中查看 `docs/experiments/` 与 `experiments/`
- 发行说明：`docs/release/v0.5.15-final-release-notes.md`
- 交付说明：`docs/competition/v0.5.15-delivery-guide.md`
- 当前结果：`docs/competition/RESULTS_SNAPSHOT.md`
