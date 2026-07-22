# v5.13v 保守证据与技术质量验收

本阶段沿用 v5.13u 的同一套连续任务、任意角色配置和真实工具调用，只修正实验归因与质量评估口径，不针对某个题目添加业务规则。

## 新增证据

1. **保守记忆归因**：当前任务中已经出现的事实不能再次算作记忆贡献；只有输出新增采用、且证据分高于当前任务基线时才计为有效记忆命中。
2. **当前任务来源追踪**：每次记忆注入记录任务文本来源及指纹，区分团队任务与单次调用 Prompt。
3. **相同逻辑调用归一化**：按 `task_id + agent + 同一任务内调用序号` 对齐三组调用，分别展示固定调用集合和额外轮次的 Token。
4. **双重匿名质量评审**：主盲评检查任务完成和上下文连续性；独立技术盲评检查命令、SQL、参数单位、并发、生命周期和恢复流程。
5. **保守合并结果**：最终质量分取主盲评与技术盲评的较低值；存在高危或致命技术问题时，交付判为不可用。

两次评审均在匿名映射揭示前冻结。评审模型产生的 Token 单独保存在评分文件中，不计入三组运行时协作成本。

## 专用变量

- `AGENTLITE_V513V_EXP_ID`
- `AGENTLITE_V513V_RUN_ROOT`
- `AGENTLITE_V513V_TRACE_ROOT`
- `AGENTLITE_V513V_TEMPERATURE`
- `AGENTLITE_V513V_MAX_TURNS`

脚本拒绝覆盖已有目录或压缩包。

## 默认产物

```text
runs/v5.13v-conservative-evidence-quality/<EXP_ID>/
.agentlite-exp/v5.13v-conservative-evidence-quality/<EXP_ID>/
exports/v5.13v-conservative-evidence-quality-<EXP_ID>.tar.gz
exports/v5.13v-conservative-evidence-quality-<EXP_ID>.tar.gz.sha256
```

执行入口：

```bash
bash experiments/v5.13v-conservative-evidence-quality/run_openeuler.sh
```
