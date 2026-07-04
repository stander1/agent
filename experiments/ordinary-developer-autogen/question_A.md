# 任务组 A：长程个性化旅行规划与连续约束修订

## 任务组定位

本任务组用于验证多 Agent 协作系统在**长上下文、连续修订、跨任务记忆复用**场景下的通信开销优化效果。

上一版 A1-A5 只覆盖一次完整旅行规划，历史上下文膨胀不明显，因此真实 LLM 实验中优化幅度不够大。本版将任务扩展为 10 个连续用户轮次：前 5 轮完成基础旅行方案，后 5 轮不断追加新约束、突发变化和细节修订，迫使系统反复复用早期偏好、候选地、行程、预算、排除项和最终选择理由。

核心实验目的：

```text
baseline_text:
  每轮 Agent 都接收完整历史自然语言上下文。
  随着轮次增加，Prompt 和 Agent 间消息持续膨胀。

runtime_lite:
  Agent 间只传 SHP 控制头、state_ref、memory_ref 和短摘要。
  下游 Agent 按角色读取裁剪后的 State Prompt View / Memory View。
  历史正文留在 StatePool，不在每轮消息中重复传播。
```

该任务组更适合展示：

- 结构化协议是否降低 Agent 间直接消息 token。
- 非文本/半结构化状态是否替代长文本中间结果传递。
- 共享记忆是否减少跨轮重复推理。
- 端到端成本是否真的下降，而不是把成本转移到 memory retrieval。
- 延迟是否因状态 IO、记忆检索和协议解析产生新的开销。

## 实验模式要求

### Baseline 模式

Baseline 必须模拟常见多 Agent 框架的朴素协作方式：

- 每个 Agent 的输入包含用户当前请求。
- 每个 Agent 的输入包含之前所有 Agent 输出的完整文本。
- 后续任务可以读取前序任务结果，但读取方式是完整文本拼接。
- 不允许使用 `state_ref`、`memory_ref`、Prompt View 裁剪或结构化状态读取。

Baseline 的上下文规模应随任务轮次明显增长。

### Runtime Lite / Optimized 模式

Optimized 模式必须使用低开销运行时机制：

- Agent 间消息使用 SHP-lite 或 compact SHP。
- 完整中间产物写入 StatePool。
- Agent 间只传 `state_ref`、`memory_ref`、短摘要和必要控制字段。
- 下游 Agent 只能通过 State Access API 读取角色相关 Prompt View。
- 跨任务信息通过 MemoryStore 检索，MemoryView 必须有 token budget。
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
- retrieval_state_count
- artifact_state_count
- embedding_state_count
- memory_query_count
- memory_hit_count
- useful_memory_hit_count
- wrong_memory_hit_count
- memory_supported_output_count

## 静态资料包

为了避免实时网页依赖，比赛 Demo 使用本地静态资料包。

建议准备：

```text
data/travel/cities.md
data/travel/attractions.md
data/travel/transport.csv
data/travel/food_recommendations.md
data/travel/crowd_level.json
data/travel/weather_scenarios.json
data/travel/budget_table.csv
data/travel/accessibility_notes.md
```

示例资料实体：

```text
city_001 山湖镇：
  交通 1.5 小时，自然景观丰富，人流中低，美食以湖鲜和小吃为主。

city_004 古桥湾：
  交通 2 小时，古镇街区和河湾徒步线路，人流中等，住宿价格适中。

city_007 云杉谷：
  交通 2.5 小时，自然风景最佳，轻徒步线路成熟，但周末住宿较贵。

city_009 商业乐园：
  人流高，活动偏商业化，与用户偏好不符。
```

## 连续任务设计

### A1：旅行偏好与硬约束抽取

#### 用户输入

```text
我和朋友打算安排一个 3 天 2 晚的短途旅行，总预算 3000 元以内。
我们更喜欢自然风景、轻徒步、当地美食，不太想去人特别多的商业景点。
每天步行强度不要太高，晚上希望能有比较轻松的活动。
请先帮我整理需求和约束。
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
  "state_type": "preference_state",
  "payload_kind": "structured_constraints",
  "constraints": {
    "duration_days": 3,
    "nights": 2,
    "budget_max": 3000,
    "preferred_activities": ["自然风景", "轻徒步", "当地美食"],
    "avoid": ["人流密集商业景点", "高强度步行"],
    "evening_preference": "轻松活动"
  }
}
```

#### 写入 Memory

```json
{
  "memory_type": "claim_card",
  "subject": "user_travel_preference",
  "slot_id": "slot.user.preference.travel_style",
  "summary": "用户偏好自然风景、轻徒步和当地美食，倾向低强度行程，避免人流密集商业景点。",
  "reuse_scope": "task_group_A"
}
```

### A2：候选目的地与景点检索

#### 用户输入

```text
基于刚才的旅行偏好，请从本地资料库中筛选 3 个适合的短途目的地，并说明各自适合或不适合的原因。
```

#### 验证重点

Retriever 不应把全部资料原文传给 Writer。Optimized 模式应写入 `retrieval_state`：

```json
{
  "state_type": "retrieval_state",
  "payload_kind": "structured_non_text",
  "candidate_ids": ["city_001", "city_004", "city_007"],
  "score_map": {
    "city_001": 0.89,
    "city_004": 0.82,
    "city_007": 0.78
  },
  "evidence_refs": ["travel_chunk_012", "travel_chunk_018", "travel_chunk_031"],
  "usage_hint": "destination_selection"
}
```

### A3：生成初版 3 天 2 晚行程

#### 用户输入

```text
请基于 A2 的候选目的地，选择最合适的一个，并生成 3 天 2 晚的初版行程。
```

#### 验证重点

Writer 需要读取：

- A1 preference_state
- A2 retrieval_state
- 目的地候选排序结果
- 用户对人流、步行强度和晚间活动的偏好

Optimized 模式生成 `plan_state`：

```json
{
  "state_type": "plan_state",
  "payload_kind": "structured_plan",
  "selected_destination": "city_001",
  "days": [
    {"day": 1, "theme": "抵达 + 城市轻体验", "activity_ids": ["food_003", "walk_002"]},
    {"day": 2, "theme": "自然风景 + 轻徒步", "activity_ids": ["nature_004", "trail_001"]},
    {"day": 3, "theme": "本地市场 + 返程", "activity_ids": ["market_002"]}
  ]
}
```

### A4：预算与时间约束优化

#### 用户输入

```text
请检查 A3 的行程是否超过 3000 元预算，并优化交通、住宿和活动安排，使总成本控制在预算内。
```

#### 验证重点

Calculator/Optimizer 读取结构化预算表，而不是完整旅行文本。

```json
{
  "state_type": "budget_state",
  "payload_kind": "structured_table",
  "before_total": 3260,
  "after_total": 2860,
  "changed_items": [
    {"item": "住宿", "before": 1200, "after": 900},
    {"item": "交通", "before": 760, "after": 620}
  ],
  "constraint_satisfied": true
}
```

### A5：生成第一版最终旅行手册

#### 用户输入

```text
请整合前面的偏好、目的地选择、行程和预算优化结果，生成一份最终旅行手册，包括每日安排、预算表、注意事项和备选方案。
```

#### 验证重点

A5 是第一次收束任务。Baseline 需要携带 A1-A4 大量文本；Optimized 模式只读取：

```json
{
  "state_refs": [
    "state_A1_travel_preferences",
    "state_A2_destination_candidates",
    "state_A3_initial_itinerary",
    "state_A4_budget_optimization"
  ],
  "memory_refs": [
    "mem_A1_travel_preference",
    "mem_A2_destination_selection",
    "mem_A3_itinerary_pattern",
    "mem_A4_budget_strategy"
  ]
}
```

### A6：新增同行人晕车与出发时间约束

#### 用户输入

```text
我刚确认了一下，同行的朋友容易晕车，所以不希望坐太久盘山路。
另外我们第一天上午 10 点以后才能出发。
请在不推翻前面整体方案的前提下，调整交通和第一天安排。
```

#### 新增 State

```json
{
  "state_type": "constraint_delta_state",
  "payload_kind": "structured_constraints_delta",
  "new_constraints": {
    "motion_sickness": true,
    "avoid_transport": ["长时间盘山路", "连续换乘"],
    "day1_departure_after": "10:00"
  },
  "affected_states": ["plan_state", "budget_state", "transport_state"]
}
```

#### 验证价值

这是第一个修订任务。Baseline 会重新携带 A1-A5 的完整方案；Optimized 只需读取偏好、交通、预算和第一天行程相关视图。

### A7：天气变化导致第二天户外活动受限

#### 用户输入

```text
天气预报显示第二天下午可能下雨。请保留自然风景和轻徒步体验，但给第二天下午增加一个室内或低风险备选方案。
预算仍然不要超过 3000 元。
```

#### 新增 State

```json
{
  "state_type": "weather_state",
  "payload_kind": "structured_event",
  "event": "day2_afternoon_rain",
  "risk_level": "medium",
  "affected_activity_ids": ["nature_004", "trail_001"],
  "fallback_activity_ids": ["museum_002", "tea_house_004", "covered_market_001"]
}
```

#### 验证价值

需要复用：

- A1 偏好
- A3 行程
- A4 预算
- A6 交通约束

Optimized 模式应只读取与第二天下午相关的裁剪视图。

### A8：预算下调到 2600 元

#### 用户输入

```text
我们想再节省一点，总预算最好控制在 2600 元以内。
请不要删除核心自然体验，也不要把住宿降到明显影响休息的程度。
请重新优化预算。
```

#### 新增 State

```json
{
  "state_type": "budget_delta_state",
  "payload_kind": "structured_table",
  "old_budget_max": 3000,
  "new_budget_max": 2600,
  "protected_items": ["核心自然体验", "基础住宿质量"],
  "optimization_priority": ["交通", "餐饮组合", "非核心活动", "住宿微调"]
}
```

#### 验证价值

A8 会显著放大历史依赖。预算优化必须知道：

- 原预算结构
- 用户核心体验不可删
- A6 交通约束
- A7 雨天备选

Baseline 很容易把所有历史全文塞进 prompt；Optimized 应通过 `budget_state` 和 `constraint_delta_state` 组合读取。

### A9：新增饮食偏好和伴手礼需求

#### 用户输入

```text
我们希望每天至少安排一顿当地特色餐，但其中一个朋友不吃辣。
另外想预留 200 元买伴手礼。
请调整餐饮安排和预算表，不要影响已经确定的核心行程。
```

#### 新增 State

```json
{
  "state_type": "food_preference_state",
  "payload_kind": "structured_constraints",
  "food_constraints": {
    "local_meal_per_day": true,
    "avoid_spicy_for_one_member": true,
    "souvenir_budget": 200
  },
  "affected_states": ["budget_delta_state", "plan_state"]
}
```

#### 验证价值

这个任务会测试 Memory 是否会污染或遗漏早期约束：系统不能为了 2600 元预算删掉核心体验，也不能忽略不吃辣和伴手礼预算。

### A10：生成最终修订版旅行手册与决策日志

#### 用户输入

```text
请基于所有已经确认和修订过的内容，生成最终旅行手册。
除了每日安排和预算表，还要附上一份“决策日志”，说明每次修改为什么没有破坏最初的旅行偏好。
```

#### 必须复用的内容

```json
{
  "required_state_types": [
    "preference_state",
    "retrieval_state",
    "plan_state",
    "budget_state",
    "constraint_delta_state",
    "weather_state",
    "budget_delta_state",
    "food_preference_state"
  ],
  "required_memory_subjects": [
    "user_travel_preference",
    "travel_destination_selection",
    "travel_itinerary",
    "travel_budget_optimization",
    "travel_revision_policy"
  ]
}
```

#### 验证价值

A10 是成本放大任务。Baseline 必须携带 A1-A9 的完整历史文本；Optimized 应只读取最终汇总所需的 StateView 和 MemoryView。

目标是让实验更清楚地观察到：

```text
baseline_text:
  prompt size grows with accumulated full history

runtime_lite:
  prompt size stays bounded by role-specific views and top-k memories
```

## 预期实验现象

相比旧版 A1-A5，本版 A1-A10 应更容易拉开差距：

- baseline 的 direct_message_tokens 和 prompt_tokens 随轮次增长。
- runtime_lite 的 direct_message_tokens 主要受 SHP 控制头和短摘要影响。
- runtime_lite 的 prompt_view_tokens 受 StateView/MemoryView budget 控制。
- retrieved_memory_tokens 会增加，但应低于 baseline 反复携带完整历史的增量。
- 如果 runtime_lite 延迟仍然上升，需要继续优化 StateView 缓存、Memory 检索和状态写入策略。

## 结果判定标准

当前 v2 单轮 A1-A5 的 token 降幅仍不够明显。因此本任务组的目标不是证明单次短任务收益，而是验证长程协作中的成本曲线。

建议判定标准：

```text
最低目标：
  end_to_end_collaboration_tokens 降低 >= 30%
  llm_prompt_tokens 降低 >= 20%
  success_rate 不低于 baseline

理想目标：
  end_to_end_collaboration_tokens 降低 >= 45%
  llm_prompt_tokens 降低 >= 35%
  memory_supported_output_count 随轮次增加
  latency_ms 不高于 baseline 的 1.15 倍

风险信号：
  retrieved_memory_tokens 占 runtime_lite 总成本 > 25%
  wrong_memory_hit_count 上升
  runtime_lite latency 明显高于 baseline
  A10 最终手册遗漏 A6-A9 新增约束
```
