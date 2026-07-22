# v5.13u openEuler 验收

本实验原样复用 v5.13s/v5.13t 的动态 Agent 工作负载、配置和盲评流程，只新增 v5.13u 的证据验收口径，避免改变任务后无法进行前后对比。

新增硬性检查：

1. 无文本 AutoGen 控制消息必须归类为正常透传，不能计入真实错误回退；
2. 真实错误回退次数必须为 0；
3. 实际注入的 MemoryView 必须产生下游采用证据；
4. `useful + wrong + unassessed` 必须与实际注入记忆数完全对账；
5. 采用事件必须携带记忆引用、事实指纹和通用归因算法标识。

## 验收重点

1. AutoGen UUID 运行实例归并到真实逻辑 Agent；
2. 业务 Agent 与 Team、Manager、Runtime 系统实体分层；
3. 能力画像更新事件不再随钩子调用次数膨胀；
4. 实际记忆角色视图 Token 不大于来源 MemoryView；
5. 实际当前任务视图 Token 不大于原任务；
6. 连续记忆、真实消息改写和匿名质量不下降。

## 专用变量

- `AGENTLITE_V513U_EXP_ID`
- `AGENTLITE_V513U_RUN_ROOT`
- `AGENTLITE_V513U_TRACE_ROOT`
- `AGENTLITE_V513U_TEMPERATURE`
- `AGENTLITE_V513U_MAX_TURNS`

脚本拒绝覆盖同名实验目录或压缩包。

## 默认产物

```text
runs/v5.13u-evidence-attribution/<EXP_ID>/
.agentlite-exp/v5.13u-evidence-attribution/<EXP_ID>/
exports/v5.13u-evidence-attribution-<EXP_ID>.tar.gz
exports/v5.13u-evidence-attribution-<EXP_ID>.tar.gz.sha256
```

执行入口：

```bash
bash experiments/v5.13u-evidence-attribution-acceptance/run_openeuler.sh
```
