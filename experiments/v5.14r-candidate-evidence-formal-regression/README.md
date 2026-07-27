# v5.14r 候选充分证据正式回归

本阶段把 v5.14q 已通过确定性验收的通用修复放回真实 MiMo Provider、
A1-A10/B1-B10 连续任务和 Native、Observed、Managed 三组对照中。

## 公平性

- 完整复用 v5.14p 的冻结任务、Agent 配置、模型、温度、最大轮次和评分器；
- 完整复用 v5.14p 的成本、质量、交付、记忆、状态和协议卫生阈值；
- 只新增候选交付物完整性与任务身份锚点的审计指标；
- 运行时不识别 Question A/B、领域实体、固定数值或固定业务 Agent 名称。

## openEuler 运行

```bash
export AGENTLITE_V514R_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14r-candidate-evidence-formal-regression/run_openeuler.sh
```

中断续跑：

```bash
export AGENTLITE_V514R_EXP_ID="<原实验编号>"
export AGENTLITE_V514R_RESUME=1
bash experiments/v5.14r-candidate-evidence-formal-regression/run_openeuler.sh
```
