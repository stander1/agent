# 任务组 B：合成安全审计链路追踪与连续证据修订

## 任务组定位

本任务组用于验证多 Agent 协作系统在**多跳线索追踪、异构状态传递、证据链修订、低预算控制与最终审计汇总**场景下的通信开销优化效果。

任务组 A 侧重旅行规划中的偏好、预算和行程连续修订；任务组 B 则侧重防御性安全审计中的状态血缘、证据指针、污染纠偏和审计可追踪性。两组任务互补，适合作为比赛第九题要求的第二个连续压测任务组。

本任务组所有数据均为本地合成审计数据，只用于防御分析、日志归因和证据链整理，不包含真实攻击利用步骤、真实账户、真实 IP、真实恶意代码或可执行入侵流程。

核心实验目的：

```text
baseline_text:
  每轮 Agent 都接收当前请求和前序所有分析文本。
  随着线索跳数增加，原始日志、表格解释、图谱路径和修订说明反复进入 Prompt。
  后续任务容易出现上下文膨胀、证据链断裂、过期线索继续被使用的问题。

runtime_lite:
  Agent 间只传 SHP 控制字段、state_ref、memory_ref 和短摘要。
  原始日志、账本片段、图谱路径、执行产物和审计证据进入 Runtime State Pool。
  下游 Agent 通过 State Access API 读取角色相关 Prompt View。
  高价值审计结论通过 MemoryView 复用，过期线索通过 lifecycle / deprecated 标记避免污染。
```

该任务组更适合展示：

- 多跳线索追踪中，完整历史文本是否导致 prompt 持续膨胀。
- `retrieval_state`、`artifact_state`、`embedding_state`、`evidence_chain_state` 是否能替代长文本传递。
- Cold audit payload 是否能保存完整证据，同时不进入普通 Prompt View。
- MemoryView 是否能复用历史审计规则，又不把过期线索当成当前事实。
- 在控制预算下降时，系统是否能通过状态引用和裁剪视图保持任务继续推进。
- 最终报告是否能追溯每个结论来自哪个 state_ref / memory_ref。

## 是否适合作为第二个连续压测任务

结论：**适合**，但需要按比赛 Demo 目标做边界收敛。

适合的原因：

- 与 A 题的旅行规划不同，B 题天然包含多跳依赖：告警 -> 网络线索 -> 合成载荷备注 -> 合成账本路径 -> 实体画像 -> 最终审计结论。
- 中间状态类型更丰富，能展示非文本/半结构化状态传递，而不仅是短摘要。
- 后半段加入污染纠偏、预算下降和证据合规检查，能压测记忆生命周期和状态一致性。
- 最终 B10 要求审计日志和证据链，适合评价 runtime_lite 是否只是降低 token，还是仍能保持质量与可追踪性。

需要收敛的地方：

- 不做真实网络攻击、真实解密、真实金融追踪，只使用本地合成资料。
- B3 的“解码”应是读取本地已准备好的合成 payload 映射，不让 LLM 生成可执行攻击代码。
- B8 的控制预算下降作为系统运行时压力，不引入真实 DoS 或攻击操作。
- v2/v3 阶段只做 lite 版，读租约、完整 GC、复杂 CSCC 可放到 v4 以后。

## 实验模式要求

### Baseline 模式

Baseline 必须模拟朴素多 Agent 框架协作方式：

- 每个 Agent 的输入包含用户当前请求。
- 每个 Agent 的输入包含之前所有 Agent 输出的完整文本。
- 后续任务可以读取前序结果，但读取方式是完整文本拼接。
- 不允许使用 `state_ref`、`memory_ref`、Prompt View 裁剪或结构化状态读取。
- 原始合成日志、账本片段、路径解释和审计说明会随着轮次不断进入上下文。

Baseline 的上下文规模应随 B1-B10 轮次明显增长。

### Runtime Lite / Optimized 模式

Optimized 模式必须使用低开销运行时机制：

- Agent 间消息使用 SHP-lite 或 compact SHP。
- 完整中间产物写入 Runtime State Pool。
- Agent 间只传 `state_ref`、`memory_ref`、短摘要和必要控制字段。
- 下游 Agent 只能通过 State Access API 读取角色相关 Prompt View。
- 完整 raw evidence、长日志和审计正文只进入 Cold audit payload。
- 跨任务信息通过 MemoryStore / MemoryView 检索，MemoryView 必须有 token budget。
- 端到端成本必须包含：

```text
end_to_end_collaboration_tokens =
  direct_message_tokens
+ prompt_view_tokens
+ retrieved_memory_tokens
+ control_llm_tokens
+ retry_tokens
```

### 对比指标

必须记录：

- message_count
- direct_message_tokens
- prompt_view_tokens
- retrieved_memory_tokens
- llm_prompt_tokens
- llm_completion_tokens
- llm_total_tokens
- end_to_end_collaboration_tokens
- task_latency_ms
- state_refs_count
- state_payload_bytes
- hot_state_count
- warm_state_count
- cold_state_count
- retrieval_state_count
- artifact_state_count
- embedding_state_count
- memory_query_count
- memory_hit_count
- useful_memory_hit_count
- wrong_memory_hit_count
- memory_supported_output_count
- evidence_chain_complete
- deprecated_clue_used_count

## 静态资料包

为了避免实时网络依赖，比赛 Demo 使用本地合成资料包。

建议准备：

```text
data/security/synthetic_alerts.json
data/security/network_observations.csv
data/security/payload_notes.json
data/security/synthetic_ledger.csv
data/security/entity_registry.json
data/security/vector_clusters.json
data/security/compliance_rules.md
data/security/audit_templates.md
```

示例资料实体：

```text
alert_071:
  source_node=Node_71
  timestamp=2026-06-15T02:15:00Z
  event_type=bulk_upload_anomaly
  target_pointer=proxy_token_PX42
  severity=high

network_row_8402:
  proxy_token=proxy_token_PX42
  resolved_device=device_shadow_17
  touched_accounts=[wallet_demo_902, wallet_demo_911]
  confidence=0.86

payload_note_911:
  wallet_id=wallet_demo_911
  decoded_marker=ROUTE_VIA_ENTITY_085
  mapping_source=synthetic_payload_notes

ledger_path_085:
  entity=ENTITY_085
  path=[ENTITY_085, intermediary_demo_302, beneficiary_demo_999]
  amount_bucket=high
  evidence_rows=[ledger_1022, ledger_1067, ledger_1098]
```

## 连续任务设计

### B1：初始告警接入与硬约束抽取

#### 用户输入

```text
审计系统捕获了一条合成高危告警：边缘节点 Node_71 在凌晨 2:15 出现异常大额数据打包上传，目标指针为 proxy_token_PX42。
请先抽取该告警的初始硬约束、核心线索和后续追踪键，作为链式审计的起点。
```

#### Agent 流程

```text
PlannerAgent
  -> AnalyzerAgent
  -> WriterAgent
  -> ReviewerAgent
  -> MemoryManagerAgent
```

#### 产生的 State

```json
{
  "state_type": "alert_state",
  "payload_kind": "structured_alert_profile",
  "alert_metadata": {
    "source_node": "Node_71",
    "timestamp": "2026-06-15T02:15:00Z",
    "target_pointer": "proxy_token_PX42",
    "behavior": "synthetic_bulk_upload_anomaly",
    "risk_level": "high"
  },
  "next_lookup_key": "proxy_token_PX42"
}
```

#### 写入 Memory

```json
{
  "memory_type": "claim_card",
  "subject": "security_audit_start",
  "slot_id": "slot.project.requirement.security_audit_chain",
  "summary": "B 组审计链以 Node_71 的合成异常上传告警为起点，首个追踪键为 proxy_token_PX42。",
  "reuse_scope": "task_group_B"
}
```

### B2：第一层线索解析：网络观测表检索

#### 用户输入

```text
基于 B1 锁定的 proxy_token_PX42，请从本地 network_observations.csv 中检索对应的合成设备标识、关联账户和证据行。
请说明哪些字段可以作为下一跳审计依据。
```

#### 验证重点

Retriever 不应把完整 CSV 原文传给 Writer。Optimized 模式应写入 `retrieval_state`：

```json
{
  "state_type": "retrieval_state",
  "payload_kind": "network_observation_lookup",
  "key_resolved": "proxy_token_PX42",
  "resolved_device": "device_shadow_17",
  "derived_clues": ["wallet_demo_902", "wallet_demo_911"],
  "score_map": {
    "network_row_8402": 0.86,
    "network_row_11904": 0.79
  },
  "evidence_refs": ["network_row_8402", "network_row_11904"],
  "usage_hint": "wallet_flow_analysis"
}
```

### B3：第二层线索解析：合成载荷备注映射

#### 用户输入

```text
网络观测显示 wallet_demo_911 带有一条合成载荷备注。请读取本地 payload_notes.json 的已知映射，解析该备注指向的下一跳实体标记。
注意：这里只做本地合成资料映射，不生成真实解密代码。
```

#### 验证重点

B3 测试工具产物和 Cold audit payload。系统可以把完整映射记录和工具输出保存在 Cold Tier，普通 Prompt View 只展示摘要和 hash。

```json
{
  "state_type": "artifact_state",
  "payload_kind": "synthetic_payload_mapping",
  "artifact_id": "artifact_B3_payload_mapping",
  "stdout_ref": "cold://security/payload_mapping_B3.json",
  "status": "success",
  "artifact_digest": {
    "detected_keys": ["wallet_id", "decoded_marker", "mapping_source"],
    "summary": "wallet_demo_911 的合成备注映射到 ROUTE_VIA_ENTITY_085。"
  },
  "next_lookup_key": "ENTITY_085"
}
```

### B4：第三层线索解析：合成账本路径审计

#### 用户输入

```text
B3 得到的实体标记是 ENTITY_085。请在 synthetic_ledger.csv 中进行多跳路径审计，找出该实体在合成账本中的最终受益账户，并记录路径证据。
```

#### 验证重点

这是 Nested Lookup 的第三层。Baseline 会反复携带 B1-B3 的文本和账本解释；Optimized 应读取 B3 的 `artifact_state` Prompt View，再写入路径状态：

```json
{
  "state_type": "evidence_chain_state",
  "payload_kind": "ledger_path",
  "hop_path": ["ENTITY_085", "intermediary_demo_302", "beneficiary_demo_999"],
  "resolution_status": "resolved",
  "active_value": {
    "ultimate_target": "beneficiary_demo_999",
    "amount_bucket": "high"
  },
  "evidence_refs": ["ledger_1022", "ledger_1067", "ledger_1098"]
}
```

### B5：生成第一版审计链路报告

#### 用户输入

```text
请把 B1-B4 已获得的初始告警、网络观测、载荷映射和账本路径，融合成一份第一版合成安全审计链路报告。
报告需要列出每一跳的依赖指针、证据引用和当前不确定性。
```

#### 验证重点

B5 是第一次收束任务。Baseline 需要携带 B1-B4 的完整文本；Optimized 只读取：

```json
{
  "state_refs": [
    "state_B1_alert_profile",
    "state_B2_network_lookup",
    "state_B3_payload_mapping",
    "state_B4_ledger_path"
  ],
  "memory_refs": [
    "mem_B1_audit_start",
    "mem_B2_network_lookup_rule",
    "mem_B3_payload_mapping_rule",
    "mem_B4_ledger_path"
  ]
}
```

### B6：新增反混淆修订：设备标识可能被污染

#### 用户输入

```text
接到新的合成情报：早期 device_shadow_17 可能受到标识混淆影响，不应再作为强证据。
请在不推翻 B1-B5 整体审计骨架的前提下，重新标记 B2 的设备线索，并说明后续应改用哪些证据作为主依据。
```

#### 新增 State

```json
{
  "state_type": "constraint_delta_state",
  "payload_kind": "anti_obfuscation_rule",
  "new_constraints": {
    "deprecated_clues": ["device_shadow_17"],
    "primary_key_switch_to": ["wallet_demo_911", "ledger_path", "entity_registry_match"],
    "do_not_use_as_strong_evidence": ["device_shadow_17"]
  },
  "affected_states": ["state_B2_network_lookup", "state_B4_ledger_path"]
}
```

#### 写入 Memory

```json
{
  "memory_type": "claim_card",
  "subject": "deprecated_audit_clue",
  "slot_id": "slot.system.failure_pattern",
  "summary": "device_shadow_17 已被标记为可能污染的弱线索，后续普通 Prompt View 不应把它作为强证据。",
  "status": "deprecated_source_guard"
}
```

#### 验证价值

这是第一个修订任务。系统必须保留审计骨架，同时阻止过期线索污染后续结论。Optimized 模式应通过生命周期状态或 Prompt View 过滤避免继续使用 deprecated clue。

### B7：第四层线索解析：相似实体聚类与去重

#### 用户输入

```text
由于 B6 标记了设备线索污染，现在 entity_registry.json 中出现 5 个相似的候选实体。
请利用本地 vector_clusters.json 的合成向量聚类结果，对这些候选实体做去重，找出最可能对应 beneficiary_demo_999 的实体画像。
```

#### 新增 State

```json
{
  "state_type": "embedding_state",
  "payload_kind": "entity_cluster_refs",
  "query_embedding_id": "emb_query_beneficiary_demo_999",
  "chunk_embedding_ids": ["emb_entity_085", "emb_entity_086", "emb_entity_091"],
  "vector_dim": 384,
  "similarity_scores": {
    "entity_profile_085": 0.91,
    "entity_profile_086": 0.74,
    "entity_profile_091": 0.69
  },
  "selected_entity_profile": "entity_profile_085"
}
```

#### 验证重点

Agent 不应读取完整向量矩阵。Prompt View Renderer 只渲染 top-k 候选、分数、冲突点和选择理由。

### B8：控制预算下降下的历史风险核验

#### 用户输入

```text
现在运行时控制预算下降：本轮控制模型调用应尽量限制在 1 次以内，重试输入 token 预算压缩到 500。
请在这个限制下，继续核验 beneficiary_demo_999 的历史关联风险，并说明哪些证据足够、哪些证据需要暂缓展开。
```

#### 新增 State

```json
{
  "state_type": "budget_control_state",
  "payload_kind": "runtime_budget_constraint",
  "control_llm_call_limit": 1,
  "retry_token_budget": 500,
  "allowed_views": ["summary", "top_k_evidence", "audit_digest"],
  "blocked_views": ["full_raw_log", "full_vector_matrix", "full_ledger_dump"]
}
```

#### 验证价值

B8 不是测试真实攻击，而是测试运行时在低预算下是否仍能通过已有状态和 MemoryView 推进。系统应优先读取摘要和 top-k 证据，不应为了核验历史风险展开全量 raw content。

### B9：第五层线索解析：合规证据链匹配

#### 用户输入

```text
请结合 B1 初始告警、B6 反混淆修订、B7 实体画像和 compliance_rules.md，对 beneficiary_demo_999 的最终风险类型进行合规证据链匹配。
如果证据不足，请明确输出 blocked 或 needs_more_evidence，不要强行编造结论。
```

#### 新增 State

```json
{
  "state_type": "compliance_state",
  "payload_kind": "risk_classification_evidence",
  "candidate_risk_types": ["synthetic_fraud_ring", "synthetic_internal_collusion", "insufficient_evidence"],
  "selected_risk_type": "synthetic_fraud_ring",
  "supporting_state_refs": [
    "state_B1_alert_profile",
    "state_B4_ledger_path",
    "state_B6_anti_obfuscation",
    "state_B7_entity_cluster"
  ],
  "evidence_sufficiency": "medium",
  "blocked_items": ["full_raw_ledger_not_expanded_due_to_budget"]
}
```

#### 验证重点

本轮测试质量守卫。若 Prompt View 中没有足够证据，Agent 应输出 `needs_more_evidence`，不能因为追求成功率而生成无证据结论。

### B10：最终审计手册与系统决策日志

#### 用户输入

```text
请基于 B1-B9 所有已经确认和修订过的内容，生成最终合成安全审计手册。
除了链路图谱、风险类型和证据表，还要附上一份“系统决策日志”，说明 B6 线索污染和 B8 控制预算下降时，系统如何避免脏读、过期线索和 raw content 过度展开。
```

#### 必须复用的内容

```json
{
  "required_state_types": [
    "alert_state",
    "retrieval_state",
    "artifact_state",
    "evidence_chain_state",
    "constraint_delta_state",
    "embedding_state",
    "budget_control_state",
    "compliance_state"
  ],
  "required_memory_subjects": [
    "security_audit_start",
    "network_lookup_rule",
    "payload_mapping_rule",
    "ledger_path_evidence",
    "deprecated_audit_clue",
    "runtime_budget_policy"
  ]
}
```

#### 验证价值

B10 是成本和质量共同放大任务。Baseline 必须携带 B1-B9 的完整历史文本；Optimized 应读取最终汇总所需的 StateView、MemoryView 和 Cold audit digest。

目标是观察：

```text
baseline_text:
  prompt size grows with accumulated full history
  old clue may continue to appear in final report

runtime_lite:
  prompt size stays bounded by role-specific views and top-k memories
  deprecated clue is visible only as warning, not as strong evidence
  cold audit payload remains available for reviewer but not default-expanded
```

## 预期实验现象

相比 A 题，B 题更适合拉开以下差异：

- baseline 的 prompt_tokens 随多跳线索和审计报告积累快速增长。
- runtime_lite 的 direct_message_tokens 主要受 SHP 控制头和短摘要影响。
- runtime_lite 的 prompt_view_tokens 应被 StateView/MemoryView budget 控制。
- `cold_state_count` 应明显增加，因为完整审计正文、工具产物和长证据应进入 Cold Tier。
- `deprecated_clue_used_count` 应低于 baseline，最好为 0。
- 如果 runtime_lite 质量下降，往往说明 Prompt View 过短，B10 最终证据表和决策日志不完整。

## 结果判定标准

建议判定标准：

```text
最低目标：
  end_to_end_collaboration_tokens 降低 >= 30%
  llm_prompt_tokens 降低 >= 20%
  success_rate 不低于 baseline
  B10 最终报告包含完整 B1-B9 证据链

理想目标：
  end_to_end_collaboration_tokens 降低 >= 45%
  llm_prompt_tokens 降低 >= 35%
  cold_state_count 明确高于 0
  evidence_chain_complete = true
  deprecated_clue_used_count = 0
  runtime_lite latency 不高于 baseline 的 1.15 倍

风险信号：
  runtime_lite 为了省 token 遗漏 B6 deprecated clue
  B10 最终报告仍把 device_shadow_17 当成强证据
  retrieved_memory_tokens 占 runtime_lite 总成本 > 25%
  wrong_memory_hit_count 上升
  runtime_lite latency 明显高于 baseline
  质量裁判判定最终审计手册缺少证据表或系统决策日志
```

## 与 A 题的互补关系

```text
A 题:
  面向用户偏好、预算、行程、连续修订。
  更适合展示日常多 Agent 工作流中的长期上下文膨胀。

B 题:
  面向合成安全审计、多跳状态、证据链、污染纠偏。
  更适合展示 Runtime State Pool、Cold audit payload、Memory lifecycle 和 Prompt/Audit View 分离。
```

因此，B 题适合作为第二组连续压测任务，但比赛演示时应明确其是**本地合成、防御审计、不可执行攻击**的数据集。
