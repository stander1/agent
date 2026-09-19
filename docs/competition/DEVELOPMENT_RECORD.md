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

## 比赛材料校准

最终比赛分支将 A/B 材料分为两个层次：

- `docs/problems/A.md`、`B.md` 面向评委说明问题背景、连续挑战、输入输出、
  评价维度和系统能力映射；
- `experiments/ordinary-developer-autogen/question_A.md`、`question_B.md`
  保留逐轮提示、状态示例和执行要求，用于复现实验；
- `docs/competition/AB_EXPERIMENT_DESIGN.md` 统一说明 Native、Observed、
  Managed 三组对照、控制变量、成本质量指标和盲评方法。

当前工作树归档阶段性调研、内部审计、旧规划和中间实验叙述，只保留当前适用
的中文展示与复现入口。被归档材料及其修改时间线仍完整存在于 Git 提交历史，
没有通过重写历史删除开发过程。

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
- 历史发行归档完整单元测试：602 / 602；
- wheel 与 sdist：均完成独立检查与隔离安装；
- 公开发布阻塞项：0。

本轮质量整改的源码回归结果与当前环境前置条件见
`docs/competition/QUALITY_REPAIR_STATUS.md`；未重新完成 Provider 基准前，不将
本轮工作树写成新的最终发行验收。

## 追溯入口

- 版本映射：`docs/versioning.md`
- 双任务设计：`docs/competition/AB_EXPERIMENT_DESIGN.md`
- 展示版问题：`docs/problems/A.md`、`docs/problems/B.md`
- 实现演进：在 Git 历史中查看 `docs/planning/`
- 实验记录：在 Git 历史中查看 `docs/experiments/` 与 `experiments/`
- 发行说明：`docs/release/v0.5.15-final-release-notes.md`
- 交付说明：`docs/competition/v0.5.15-delivery-guide.md`
- 当前结果：`docs/competition/RESULTS_SNAPSHOT.md`
- 质量整改：`docs/competition/QUALITY_REPAIR_STATUS.md`
