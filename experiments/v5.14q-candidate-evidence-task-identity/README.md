# v5.14q 当前候选充分证据与任务身份验收

v5.14p 的正式回归表明，低开销视图虽然显著减少了协作 Token，但会把
审查能力真正需要核验的当前候选正文一起裁剪。A10 的 Reviewer 因此在
没有看到完整预算表时产生了错误验算。B9 还出现了把当前任务误称为后续
任务的身份漂移。

v5.14q 按照“语义必要性优先、最小充分上下文注入、端到端成本核算”的
原则补齐两项通用机制：

- 对具有 validation、final_deliverable_review、schema_review 或
  failure_review 能力，或者执行相应审查动作的任意接收者，完整保留最近
  一份当前候选交付物；
- 其余旧历史继续压缩，不恢复全文广播；
- 用当前用户请求原文和协作序号建立任务身份锚点；
- 仅在能够从当前请求可靠推导标签族时，拦截“把当前轮改称为未来轮次”
  的窄范围错误；
- 不依赖固定 Agent 名称、Question A/B 或业务领域词。

## openEuler 运行

```bash
export AGENTLITE_V514Q_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14q-candidate-evidence-task-identity/run_openeuler.sh
```
