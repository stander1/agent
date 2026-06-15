# v1：三线 Lite 最小完整闭环

## 1. 版本定位

v1 是第一版真正的完整系统。

它不追求每个模块都强，但必须同时具备：

```text
CMJCC-lite:
  Agent 间不再只传长文本，而是传 SHP-lite。

SHP-State-lite:
  中间结果可以写入 Runtime State Pool，并通过 state_refs 被下游读取。

TLC-Memory-lite:
  高价值阶段结论可以写入 Memory Store，并在后续任务中被检索。
```

这版的核心价值是系统闭环完整。

## 2. 总体架构

```text
Agent / Agent Framework
  ↓
AgentAdapter
  ↓
AgentRuntime
  ├── CMJCC-lite Protocol
  ├── StatePool-lite
  ├── MemoryStore-lite
  └── MetricsCollector
```

v1 同时引入 Transport 抽象，但默认实现仍然是 in-process：

```text
Transport:
  in_process:
    默认实现，用于稳定跑通闭环。

  ipc_socket:
    预留接口，用于后续接 AgentWorker、AutoGenAdapter 或 Socket Gateway。
```

v1 不要求真正多进程 IPC，但接口命名和数据结构必须避免写死本地函数调用。

## 3. CMJCC-lite

最小 SHP：

```json
{
  "shp_id": "shp_001",
  "from": "retriever",
  "to": "writer",
  "task_id": "task_001",
  "action": "write_summary",
  "summary": "已完成检索，找到 6 条相关证据。",
  "parameters": {},
  "state_refs": ["state_001"],
  "memory_refs": []
}
```

能力路由先用简单表：

```text
plan_task -> PlannerAgent
retrieve_evidence -> RetrieverAgent
write_summary -> WriterAgent
review_result -> ReviewerAgent
```

## 4. SHP-State-lite

StatePool 最小支持三类状态：

```text
retrieval_state:
  top-k chunk、score、source_id。

artifact_state:
  工具执行输出、文件路径、日志路径。

failure_state:
  错误类型、stderr、失败步骤。
```

最小 API：

```python
write_state(payload, state_type, task_id, source_agent) -> StateRef
read_prompt_view(state_ref, agent_role, budget_tokens) -> PromptView
```

v1 可以不做 read lease 和复杂 GC。

状态 payload 存储策略：

```text
小 metadata:
  SQLite。

大 payload / artifact:
  文件系统保存，StatePool 只保存 payload_ref。

后续 backend:
  mmap / shared memory。
```

## 5. TLC-Memory-lite

MemoryStore 最小支持：

```text
write_memory(summary, tags, source_agent, task_topic)
search_memory(query, tags=None, top_k=3)
render_memory_prompt_view(memory_id)
```

最小记忆对象：

```json
{
  "memory_id": "mem_001",
  "task_id": "task_001",
  "source_agent": "writer",
  "task_topic": "low_cost_agent_communication",
  "summary": "结构化 SHP 可以减少 Agent 间直接传递的长文本。",
  "tags": ["communication", "token_saving"],
  "created_at": "2026-06-15T10:00:00",
  "status": "active"
}
```

## 6. 指标

```text
direct_text_chars
handoff_packet_chars
state_refs_count
state_payload_bytes
state_access_count
memory_write_count
memory_hit_count
memory_prompt_view_chars
task_latency_ms
```

Token 统计策略：

```text
沿用 v0 的 TokenCounter；
优先使用目标模型真实 tokenizer；
若真实 tokenizer 不可用，使用兼容 tokenizer；
若 tokenizer 全部不可用，才使用 v0 的中英混合估算公式；
每条指标记录 token_count_method，区分 actual / compatible / estimated。
```

## 7. 存储底座约束

v1 开始引入 SQLite / JSONL 作为运行时底座时，必须提前处理后续并发问题。

SQLite 初始化建议：

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
```

Python 连接建议：

```text
sqlite3.connect(db_path, timeout=30.0)
每个线程/任务使用独立连接；
事务保持短小；
大 payload 写入文件系统，SQLite 只保存 metadata 和 ref；
高频运行时计数优先放在内存 RuntimeRegistry 中，异步或阶段性写入 trace。
```

这样 v4 引入 Read Lease-lite 时，不会因为每次 read_state 都写 SQLite 而触发 `database is locked`。

## 8. 验收标准

```text
1. 至少 3 个 Agent 协同运行；
2. Agent 间主交接消息是 SHP-lite；
3. Retriever 产生 retrieval_state，Writer 通过 state_ref 读取 Prompt View；
4. 第一轮任务写入记忆，第二轮相关任务能检索记忆；
5. baseline_text_mode 和 runtime_lite_mode 可对比；
6. 10 轮连续任务可稳定跑完；
7. SQLite 初始化启用 WAL 和 busy_timeout；
8. 指标记录 token_count_method；
9. Transport 抽象存在 in_process 默认实现，并预留 ipc_socket。
```

## 9. 本版本不做什么

```text
不做复杂 JSON 契约修复；
不做完整 MemoryView；
不做状态租约；
不做冷数据 I/O 调度；
不接 AutoGen。
```
