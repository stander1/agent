# v5.14k 类型化可靠性机制验收

该阶段针对 v5.14j 正式回归暴露出的通用问题做确定性验收，不调用
LLM Provider，也不识别 Question A/B 或固定 Agent 角色。

验收内容：

1. 金额、时间、计数和比例按量纲分离，时间值不能成为预算上限；
2. Reviewer 的阻断意见或修订清单不能伪装成最终交付物；
3. 已经整合修正的完整成果仍可正常交付；
4. `expected`、`assumed` 等不确定结论只进入记忆候选池；
5. `observed`、`confirmed` 等有证据事实才可进入正式共享记忆；
6. 只有框架输出真实携带 ID、维度和分数时，才生成
   `embedding_state` 或 `retrieval_state`；
7. 盲评扣分证据必须能在当前答案中定位，不能来自历史答案；
8. 生产运行时不得包含实验任务、地点或题号特判。

Reviewer 闭环沿用正式 AutoGen 团队已注册的轮次：阻断意见不能触发终止，
团队在原定最大轮次内继续修订；轮次耗尽仍不合格时明确失败，不增加隐藏
LLM 调用。

openEuler 运行：

```bash
export AGENTLITE_V514K_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14k-typed-reliability-acceptance/run_openeuler.sh
```
