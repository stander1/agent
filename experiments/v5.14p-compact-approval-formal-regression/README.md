# v5.14p 紧凑审批准入正式回归

本阶段把 v5.14o 已通过确定性验收的通用修复放回真实 MiMo Provider、
A1-A10/B1-B10 连续任务和 Native、Observed、Managed 三组对照中。

## 公平性

- 完整复用 v5.14n 的任务、v5.14m Agent 配置、模型、温度、最大轮次和评分器。
- 完整复用 v5.14n 的成本、质量、交付、记忆、状态和协议卫生阈值。
- 唯一生产机制差异是 v5.14o 对紧凑 Reviewer 审批语义的通用修复。
- 运行时不识别 Question A/B、领域实体、固定数值或固定业务 Agent 名称。

## openEuler 运行

```bash
export AGENTLITE_V514P_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14p-compact-approval-formal-regression/run_openeuler.sh
```

中断续跑：

```bash
export AGENTLITE_V514P_EXP_ID="<原实验编号>"
export AGENTLITE_V514P_RESUME=1
bash experiments/v5.14p-compact-approval-formal-regression/run_openeuler.sh
```

正式证据位于：

```text
runs/v5.14p-compact-approval-formal-regression/<实验编号>/
exports/v5.14p-compact-approval-formal-regression-<实验编号>.tar.gz
exports/v5.14p-compact-approval-formal-regression-<实验编号>.tar.gz.sha256
```
