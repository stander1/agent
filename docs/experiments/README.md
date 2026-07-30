# AgentLite 实验复现索引

当前比赛分支只保留问题设计、可复现实验入口和适用的结果摘要。中间版本的
诊断、修复与回归报告完整保留在 Git 提交历史中，不在当前工作树重复展示。

## 问题设计

- 统一方法：`docs/competition/AB_EXPERIMENT_DESIGN.md`
- 任务组 A 展示版：`docs/problems/A.md`
- 任务组 B 展示版：`docs/problems/B.md`
- A 组实验执行规约：`experiments/ordinary-developer-autogen/question_A.md`
- B 组实验执行规约：`experiments/ordinary-developer-autogen/question_B.md`

## 复现入口

- 普通开发者 AutoGen 实验：`experiments/ordinary-developer-autogen/README.md`
- 成本质量预检：`experiments/v5.14b-fair-cost-quality-preflight/`
- 正式规模运行：`experiments/v5.14f-formal-scale-acceptance/`
- 最终发行验收：`experiments/v5.15y-installed-sdist-delivery-readiness/`

## 当前结果

- 冻结通信对照：`docs/experiments/v5.12x-autogen-team-benchmark-results.md`
- 比赛结果快照：`docs/competition/RESULTS_SNAPSHOT.md`

结果声明以对应实验的输入、模型、阈值和归档边界为准。问题设计和实验计划
不能替代实际结果，通信 Token 也不等同于 Provider 计费 Token。
