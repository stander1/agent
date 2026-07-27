# v5.14o 紧凑 Reviewer 审批语义验收

v5.14n 的真实 MiMo 输出证明 Reviewer 经常只返回“验收通过”或“批准
上一份成果”，而不是旧规则覆盖的“前序/上一版成果”。v5.14o 将这些
紧凑表达识别为对最近一份有效业务成果的引用审批。

## 可靠性边界

- 只有明确的短审批表达能够触发引用晋升；
- 必须存在最近一份通过当前任务守卫的业务成果；
- 没有候选成果时不能凭空生成最终交付；
- 修订要求和验收不通过不能触发晋升；
- 包含完整业务正文的成果不会被误判成审批短文；
- 机制不依赖 Question A/B、业务实体或成果 Agent 名称。

## openEuler 运行

```bash
export AGENTLITE_V514O_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14o-compact-review-approval/run_openeuler.sh
```
