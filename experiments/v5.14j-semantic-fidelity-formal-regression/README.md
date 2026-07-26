# v5.14j 语义保真正式回归

本阶段把 v5.14i 已通过的通用机制修复放回真实 Provider、A1-A10/B1-B10
完整连续任务矩阵中验收。

## 公平性约束

- 完全复用 v5.14h 使用的两份 v5.14f 冻结任务；
- 完全复用 A/B 两组 Agent 配置；
- 模型仍为 `mimo-v2.5`，`temperature=0`，`max_turns=9`；
- Provider Token、端到端协作 Token、总体质量、交付完整度、终局质量、
  记忆有效性和审查治理阈值不变；
- 不缩短输出，不减少轮次，不向生产运行时加入 A/B 题目或领域实体。

## 新增验收

1. 分别统计模型输入、Agent 输出和团队最终输出中的协议标记；
2. A、B 场景分别要求错误记忆命中为 0；
3. 记忆守卫不得破坏框架结果类型；
4. 对 v5.14h 暴露根因的 A2、A4、A10、B10 逐项检查：
   - 运行时交付有效；
   - 匿名盲评认定交付完整；
   - 质量分不低于 7，且不比 Native 低超过 1 分；
   - 无 critical/high 技术发现；
   - 无当前任务保真失败或内部控制标记泄漏。

这些任务编号只存在于实验预注册和验收器中。`agent_runtime` 仍只实现通用
语义事实身份、冲突治理、当前任务保真和协议隔离机制。

## openEuler 运行

```bash
export AGENTLITE_V514J_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14j-semantic-fidelity-formal-regression/run_openeuler.sh
```

中断后使用相同实验编号续跑：

```bash
export AGENTLITE_V514J_EXP_ID="替换为原实验编号"
export AGENTLITE_V514J_RESUME=1
bash experiments/v5.14j-semantic-fidelity-formal-regression/run_openeuler.sh
```

## 评分 JSON 中断恢复

匿名综合评分和技术评分都使用逐任务检查点。评分响应依次经过：

1. JSON 块提取与确定性规则修复；
2. 只携带坏 JSON、校验错误、Schema 和匿名候选 ID 的短上下文格式修复；
3. 仍失败时，使用相同证据和评分标准执行一次限制说明长度的完整重审。

所有失败和成功尝试的评分 Token、延迟、恢复模式、响应长度与 SHA-256
都会进入 `judge_attempts`。这些评分调用仍与运行时协作成本隔离。
