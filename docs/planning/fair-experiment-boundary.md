# AgentLite 公平实验与领域边界

日期：2026-07-18

## 核心原则

Question A/B 只是实验任务。AgentLite 运行时不得预先知道任务编号、旅游目的地、预算字段、安全审计实体或标准答案结构。

正式对比的三组必须满足：

```text
相同 AutoGen 代码
相同 Agent 角色与提示词
相同模型和采样参数
相同终止条件
相同 max_turns
相同问题顺序
```

唯一变量是：

```text
native: 不启动 AgentLite
observed: AgentLite 只观察，不改写
managed: AgentLite 接管通用消息、状态和记忆传递
```

## 允许保留的领域内容

Question A 可以使用旅游专家 Planner、Writer、Reviewer，因为真实开发者本来就会配置领域 Agent。三组必须使用完全相同的配置。

问题文本、专家提示词和离线盲评评分表可以包含旅游内容，但不得把旅游规则注入 AgentLite Adapter、状态池、记忆池、交付守卫或额外重试提示。

## 已删除的定制能力

正式运行路径不再包含：

- `AGENTLITE_DELIVERY_POLICY`；
- Question A 三候选、十点后出发、目的地连续性等硬编码；
- Question A/B 专用记忆槽位 Profile；
- A10/B10 专用运行时交付 Schema；
- 因答案质量不合格而追加的 Writer/Reviewer LLM 调用。

AgentLite 共享记忆只能依赖通用候选准入、来源、任务作用域、版本、摘要和语义检索。旅游事实是否被正确保留，由真实 Agent 在收到的上下文中判断。

## 失败与重试口径

网络失败、Provider 空响应和协议格式损坏仍可按既定鲁棒性方案重试，并完整计入 Token、延迟和重试次数。

答案遗漏、事实错误、Reviewer 仍要求修改属于任务质量失败，不是通信协议失败。达到统一轮次上限后直接保存原始输出并记录：

```text
delivery_valid=false
delivery_status=task_failed
semantic_retry_count=0
```

如果原生组成功而接管组在相同轮次失败，应判定为 AgentLite 质量回归并修复上下文改写或记忆注入，不得通过增加轮次掩盖。

## 当前实验配置

- Team：旅游专家 Planner、Writer、Reviewer；
- 调度：`RoundRobinGroupChat`；
- 终止：三组统一使用 `ReviewerFinalTextTermination`，只接受 Reviewer 最后一行的精确完成标记；
- 最大轮次：`9`，即三个完整三 Agent 周期；
- 质量：任务结束后生成匿名候选，由统一盲评评分，不影响运行过程。
