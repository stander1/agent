# v5.13w 版本冲突感知记忆验收

本阶段解决框架原生历史仍保留旧值、而 AgentLite MemoryView 已切换到新值时的跨层污染问题。实现和验收均不读取 Question A/D，也不依赖 Planner、Writer、Reviewer 等固定角色名。

## 核心门禁

1. 被替换 Claim 不再进入当前 Prompt View；
2. 当前 MemoryView 自动生成 `revision_guard`，明确当前有效 Claim 优先；
3. 修订守卫在任意动态能力角色的最小上下文视图中强制保留；
4. 记忆采用被互斥分类为 `useful`、`wrong`、`mixed`、`unassessed`；
5. 明确否定旧值时不得误判为污染；
6. 四类采用数之和必须等于实际记忆注入数；
7. 有效、错误和混合计数写回 MemoryStore，并进入追踪、报告和监控口径。

## 本地确定性验收

```bash
python experiments/v5.13w-conflict-aware-memory/verify_conflict_guard.py \
  --output-dir runs/v5.13w-conflict-aware-memory/local
```

该命令不调用 LLM，用于先验证通用机制。

## openEuler 完整验收

```bash
bash experiments/v5.13w-conflict-aware-memory/run_openeuler.sh
```

完整验收会运行 native、observed、managed 三组真实 LLM 连续任务、双重匿名质量评审、v5.13w 追踪门禁和不可变证据打包。

默认产物：

```text
runs/v5.13w-conflict-aware-memory/<EXP_ID>/
.agentlite-exp/v5.13w-conflict-aware-memory/<EXP_ID>/
exports/v5.13w-conflict-aware-memory-<EXP_ID>.tar.gz
exports/v5.13w-conflict-aware-memory-<EXP_ID>.tar.gz.sha256
```
