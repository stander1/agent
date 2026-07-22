# v5.13t openEuler 验收

本实验复用 v5.13s 的动态 Agent 工作负载、配置和盲评流程，只新增 v5.13t 的硬性验收口径，避免改变任务后无法进行前后对比。

## 验收重点

1. AutoGen UUID 运行实例归并到真实逻辑 Agent；
2. 业务 Agent 与 Team、Manager、Runtime 系统实体分层；
3. 能力画像更新事件不再随钩子调用次数膨胀；
4. 实际记忆角色视图 Token 不大于来源 MemoryView；
5. 实际当前任务视图 Token 不大于原任务；
6. 连续记忆、真实消息改写和匿名质量不下降。

## 专用变量

- `AGENTLITE_V513T_EXP_ID`
- `AGENTLITE_V513T_RUN_ROOT`
- `AGENTLITE_V513T_TRACE_ROOT`
- `AGENTLITE_V513T_TEMPERATURE`
- `AGENTLITE_V513T_MAX_TURNS`

脚本拒绝覆盖同名实验目录或压缩包。

## 默认产物

```text
runs/v5.13t-capability-identity/<EXP_ID>/
.agentlite-exp/v5.13t-capability-identity/<EXP_ID>/
exports/v5.13t-capability-identity-<EXP_ID>.tar.gz
exports/v5.13t-capability-identity-<EXP_ID>.tar.gz.sha256
```

执行入口：

```bash
bash experiments/v5.13t-capability-identity-no-expansion/run_openeuler.sh
```
