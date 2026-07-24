# v5.13x 事实级 Claim 与 MemoryView 验收

该目录验证 TLC-Memory 的事实级主线，不读取 Question A/D，也不依赖固定 Agent 角色名。

## 验收内容

1. 同语义键的新值形成 `active_value`，旧值进入 `historical_values`；
2. 同值重复只合并证据，不产生新的历史冲突；
3. `subject` 或 `scope` 不同的事实不会误冲突；
4. 空作用域和无法裁决的冲突不能进入业务 Prompt；
5. Prompt View 只提供当前值，Audit View 和 Evidence Expansion 按需展开；
6. `useful`、`wrong`、`mixed` 和“明确否定旧值”按结构化值与极性判定；
7. 系统配置与普通规划两个领域共用同一运行时实现；
8. Agent 名称可以任意设置，运行时不识别 Planner/Writer/Reviewer。

## 本地确定性验收

```bash
python experiments/v5.13x-fact-level-memory/verify_fact_memory.py \
  --output-dir runs/v5.13x-fact-level-memory/local
```

## openEuler 工程验收

```bash
bash experiments/v5.13x-fact-level-memory/run_openeuler.sh
```

该脚本不调用 LLM，负责验证机制、全量单元测试、编译检查和证据打包。真实 AutoGen 的 Native/Observed/Managed、Provider Token、端到端成本和匿名质量评审应作为下一阶段实验单独运行，不能用本门禁结果替代。
