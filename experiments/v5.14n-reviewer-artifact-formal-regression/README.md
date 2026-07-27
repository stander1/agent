# v5.14n Reviewer 成果连续性正式回归

该阶段把 v5.14m 已通过的 Reviewer 成果所有权、隐藏修订清单守卫和
明确决策记忆机制放回真实 MiMo Provider 与完整 A1-A10/B1-B10 连续任务
矩阵。

## 公平条件

- Native、Observed、Managed 三组使用同一份 v5.14m Agent 配置；
- 复用 v5.14l 的正式任务、模型、温度、最大轮次和既有门禁；
- 不限制输出长度，不减少轮次，不增加隐藏 LLM 修复调用；
- Reviewer 只返回修订清单或按引用批准，不复制、摘要或重写业务成果；
- 生产运行时不包含 Question A/B、旅游地点、安全实体或固定题号特判。

## 新增门禁

1. A、B 两个场景都必须真实触发前序成果引用晋升；
2. 接管组不得以 Reviewer 正文替代业务 Agent 成果；
3. 引用晋升后的成果所有者不得错误标成 Reviewer；
4. Reviewer 的审批短文不得成为最终正文；
5. 明确标签结论必须至少有一条进入类型化决策记忆；
6. 原有 Provider Token、端到端协作 Token、质量、交付、记忆和可靠性
   门禁继续生效。

## openEuler 运行

```bash
export AGENTLITE_V514N_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.14n-reviewer-artifact-formal-regression/run_openeuler.sh
```

中断后使用同一实验编号续跑：

```bash
export AGENTLITE_V514N_EXP_ID="原实验编号"
export AGENTLITE_V514N_RESUME=1
bash experiments/v5.14n-reviewer-artifact-formal-regression/run_openeuler.sh
```

若只在匿名技术评分阶段中断：

```bash
export AGENTLITE_V514N_EXP_ID="原实验编号"
bash experiments/v5.14n-reviewer-artifact-formal-regression/resume_technical_audit.sh A
```
