# v5.14l 类型化可靠性正式回归

该阶段把 v5.14k 已通过的通用机制放回真实 MiMo Provider 和完整
A1-A10/B1-B10 连续任务矩阵。

## 冻结条件

- 复用 v5.14j 的正式 A/B 任务、Agent 配置、模型、温度和最大轮次；
- 复用既有成本、质量、交付、记忆和协议阈值；
- 不限制输出长度，不减少协作轮次，不增加隐藏修复调用；
- 不向生产运行时加入 A/B 题号、旅游地点或安全实体特判；
- Native、Observed 和 Managed 使用相同 Provider 参数。

## 新增正式验收

1. A6 不得再把 30 分钟识别为 30 元预算上限；
2. Reviewer 修订清单不得成为成功的最终交付；
3. A6、A7、A10、B8、B9、B10 与原 A2/A4 一起进入盲评焦点；
4. 不确定或推断事实必须保持 `pending_confirmation`；
5. B2/B7 应分别产生真实 `retrieval_state` 和 `embedding_state`；
6. 结构状态必须带真实输出摘要哈希，并能与审计原文重新计算一致；
7. Provider Token 和端到端协作 Token 仍至少降低 15%，总体质量和完整
   交付不得劣于冻结阈值。

## openEuler 运行

```bash
export AGENTLITE_V514L_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14l-typed-reliability-formal-regression/run_openeuler.sh
```

中断后使用相同实验编号续跑：

```bash
export AGENTLITE_V514L_EXP_ID="替换为原实验编号"
export AGENTLITE_V514L_RESUME=1
bash experiments/v5.14l-typed-reliability-formal-regression/run_openeuler.sh
```

若仅匿名技术评分因 evidence 无法绑定当前答案而中断，先定点恢复该场景：

```bash
export AGENTLITE_V514L_EXP_ID="替换为原实验编号"
bash experiments/v5.14l-typed-reliability-formal-regression/resume_technical_audit.sh A
```

恢复脚本会验证并复用已有匿名候选、映射、综合评分和技术评分 checkpoint，
只续跑尚未完成的技术评分任务。完成后再用
`AGENTLITE_V514L_RESUME=1` 运行主脚本，主脚本会跳过已经完整的 A 场景并
继续 B 场景及最终验收。
