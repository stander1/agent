# 跨框架适配方案：AIOS 启发式透明接管

> 日期：2026-06-24
> 基于：AIOS (COLM 2025) 论文分析 + AutoGen 接入调研 + HQM SDK 对比
> 目标：定义本项目的跨框架适配架构，明确"透明接管"的技术边界

---

## 一、AIOS 的核心思想

### 1.1 AIOS 解决什么问题

AIOS（COLM 2025）观察到当前 Agent 框架的三个关键缺陷：

1. **无序资源访问**：Agent 直接调用 LLM，没有调度，导致多个 Agent 竞争 GPU 资源时反复 OOM 重试
2. **缺乏上下文管理**：Agent 被抢占时无法保存/恢复 LLM 推理状态
3. **框架各自为政**：AutoGen、LangChain、MetaGPT 各自管理 LLM 调用，无法跨框架共享资源

### 1.2 AIOS 的架构

AIOS 将 Agent 系统分为三层：

```
┌──────────────────────────────────────────────┐
│  Application Layer                           │
│  Agent 应用（AutoGen / MetaGPT / ReAct ...）  │
│  通过 AIOS SDK 与内核交互                     │
├──────────────────────────────────────────────┤
│  AIOS Kernel                                 │
│  ┌──────────┬──────────┬──────────┐          │
│  │ Scheduler│ Context  │ Memory   │          │
│  │ (FIFO/RR)│ Manager  │ Manager  │          │
│  ├──────────┼──────────┼──────────┤          │
│  │ Storage  │ Tool     │ Access   │          │
│  │ Manager  │ Manager  │ Manager  │          │
│  ├──────────┴──────────┴──────────┤          │
│  │        LLM Core(s)            │          │
│  └────────────────────────────────┘          │
├──────────────────────────────────────────────┤
│  Hardware Layer (CPU / GPU / Disk / RAM)     │
└──────────────────────────────────────────────┘
```

### 1.3 AIOS 如何实现跨框架透明接入

AIOS 的关键设计是 **框架适配器（Framework Adapter）**：

```python
# AIOS 对 AutoGen 的适配方式：Monkey Patch
@add_framework_adapter("AutoGen 0.2")
def prepare_autogen_0_2():
    # 替换 OpenAIWrapper 的核心方法
    OpenAIWrapper.__init__ = adapter_autogen_client_init
    OpenAIWrapper.create = adapter_client_create
    # 替换 ConversableAgent 的核心方法
    ConversableAgent._generate_oai_reply_from_client = adapter_generate_reply
    ConversableAgent.generate_tool_calls_reply = adapter_tool_calls_reply
    ConversableAgent.execute_function = adapter_execute_function
```

**效果**：AutoGen 代码不需要任何修改，但底层 LLM 调用被重定向到 AIOS 内核。

**AIOS 已验证的框架适配**：
- AutoGen 0.2（monkey patch 核心方法）
- Open-Interpreter（替换 completions 函数）
- MetaGPT
- ReAct / Reflexion

**实验结果**：Agent 性能维持或提升，执行速度最高提升 2.1 倍（250 并发 Agent 场景）。

---

## 二、我们的项目与 AIOS 的关系

### 2.1 同一个思想，不同的层面

| 维度 | AIOS | 本项目 |
|---|---|---|
| **管理什么** | LLM 调用调度、上下文切换、工具执行 | Agent 间通信格式、状态传递、共享记忆 |
| **内核提供什么** | Scheduler + Context Manager + Memory Manager | CMJCC + SHP-State + TLC-Memory |
| **透明接入方式** | Monkey patch 框架核心方法 | 待定（本文档定义） |
| **类比操作系统** | 进程调度器 + 虚拟内存 + 文件系统 | IPC 机制 + 共享内存 + 进程间消息队列 |

**核心洞察**：

```
AIOS 管的是：Agent 怎么调 LLM（CPU 调度层面）
我们管的是：Agent 怎么互相通信（IPC 层面）

AIOS：多个 Agent 竞争一个 LLM → 需要调度器
我们：多个 Agent 传递大量冗余文本 → 需要压缩 + 状态化 + 记忆复用
```

两者是互补关系，不是竞争关系。

### 2.2 AIOS 的局限（我们解决的）

AIOS 论文明确指出其管理的是 **LLM 相关的系统调用**：

```
AIOS System Call 分类：
- LLM Syscall：调用 LLM 生成
- Memory Syscall：读写 Agent 记忆
- Storage Syscall：文件操作
- Tool Syscall：工具调用
```

AIOS **没有**：
- Agent 间消息的结构化压缩（SHP）
- 非文本状态传递（StateFrame）
- 跨任务记忆生命周期管理（TLC-Memory）
- 输出契约校验（Contract Guard）

这些正是我们的三套创新机制。

### 2.3 理想的完整架构

如果将 AIOS 和我们的系统结合：

```
Agent Application（AutoGen / LangGraph / CrewAI）
    ↓ AIOS SDK（管理 LLM 调用）
    ↓ AgentLite SDK（管理通信、状态、记忆）
┌─────────────────────────────────────────────┐
│  AIOS Kernel          AgentLite Kernel      │
│  ┌─────────────┐      ┌──────────────────┐ │
│  │ Scheduler   │      │ CMJCC            │ │
│  │ Context Mgr │      │ SHP-State        │ │
│  │ Memory Mgr  │      │ TLC-Memory       │ │
│  │ Tool Mgr    │      │ Contract Guard   │ │
│  │ Access Mgr  │      │ State GC         │ │
│  │ LLM Core(s) │      │ Metrics & Trace  │ │
│  └─────────────┘      └──────────────────┘ │
└─────────────────────────────────────────────┘
```

---

## 三、透明接入方案

### 3.1 用户的原始想法

> 我打开我们的软件，然后我再正常使用 AutoGen 框架，在网页端中，自己定义 Agent 角色、组等。然后底层协作时，如何传递信息，以及状态池和候选池由我们的软件接管。

### 3.2 技术可行性评估

| 接管内容 | 透明程度 | 实现方式 | 技术难度 |
|---|---|---|---|
| 状态池（StatePool） | ✅ 完全透明 | Agent 输出后自动提取写入 | 低 |
| 候选池（Memory） | ✅ 完全透明 | 后台异步处理，用户无感 | 低 |
| 记忆检索注入 | ✅ 完全透明 | 在 Agent 调用 LLM 前注入 memory context | 低 |
| 契约守卫 | ✅ 完全透明 | 后台校验，失败时触发重试 | 低 |
| Token 计量 | ✅ 完全透明 | 后台指标采集 | 低 |
| Read Lease / GC | ✅ 完全透明 | 后台管理 | 低 |
| 信息传递压缩（SHP） | ⚠️ 部分透明 | 需要知道"当前是哪个 Agent"才能渲染 PromptView | 中 |
| 角色化 PromptView | ❌ 不能完全透明 | 必须有 Agent Proxy 或 monkey patch | 高 |

**结论**：

- **状态池、候选池、记忆、计量**：可以 100% 透明，后台静默运行
- **信息传递压缩**：需要一个入口点让系统介入 Agent 消息收发
- **角色化 PromptView**：必须知道接收 Agent 的角色，无法做到完全无感

### 3.3 三种接入方案对比

#### 方案 A：纯 Monkey Patch（类 AIOS 方式）

```python
# 启动时自动 patch
agentlite start --framework autogen -- python app.py

# 底层自动执行：
# 1. 替换 AssistantAgent.on_messages()
# 2. 替换 GroupChat 的消息广播
# 3. 注入 Runtime 钩子
```

**用户改动**：零
**技术风险**：高（依赖 AutoGen 内部实现，版本升级可能失效）
**适用场景**：快速验证，比赛 demo

#### 方案 B：Agent Proxy（显式包装）

```python
bridge = AgentLiteAutoGenBridge.connect_from_env()

# 用户需要做的：每个 Agent 包装一下
planner = bridge.wrap_agent(AssistantAgent("planner", ...))
writer = bridge.wrap_agent(AssistantAgent("writer", ...))

# 以下完全不变
team = RoundRobinGroupChat([planner, writer])
await team.run(task="...")
```

**用户改动**：3 行代码
**技术风险**：低（不依赖框架内部实现）
**适用场景**：生产级集成

#### 方案 C：SDK 嵌入（类 HQM 方式）

```python
from agentlite.sdk import AgentLiteClient

client = AgentLiteClient.connect()

def planner_node(state):
    # Agent 逻辑中嵌入 SDK 调用
    memories = client.retrieve(task, tags=["travel"])
    result = call_llm(prompt, context=memories)
    client.remember(task, result, validation=True)
    return result
```

**用户改动**：每个 Agent 函数内加 2-3 行
**技术风险**：最低（零框架依赖）
**适用场景**：LangGraph / CrewAI / 自研框架

### 3.4 推荐方案：分层渐进

```
Phase 1（比赛验证）：
  采用方案 C（SDK 嵌入）
  先在 LangGraph 上验证，零框架依赖
  证明"可嵌入主流框架"

Phase 2（深度集成）：
  采用方案 B（Agent Proxy）
  实现 AutoGen Adapter
  证明"可接管数据面"

Phase 3（透明体验）：
  采用方案 A（Monkey Patch）
  agentlite start 自动注入
  用户零改动
```

---

## 四、AutoGen 透明接管的详细设计

### 4.1 AIOS 的做法（参考）

AIOS 对 AutoGen 0.2 的适配：

```python
# 替换 LLM 调用
OpenAIWrapper.create = adapter_client_create

# 替换 Agent 消息处理
ConversableAgent._generate_oai_reply_from_client = adapter_generate_reply
ConversableAgent.generate_tool_calls_reply = adapter_tool_calls_reply
ConversableAgent.execute_function = adapter_execute_function
ConversableAgent._print_received_message = adapter_print_received
```

### 4.2 我们的做法

我们不接管 LLM 调用（那是 AIOS 做的事），我们接管的是 Agent 间的消息传递：

```python
# 概念设计（非最终代码）

# 1. 替换消息发送（输出后处理）
original_send = ConversableAgent.send_message

def patched_send(self, message, recipient, **kwargs):
    # Agent 输出后：契约校验 → 状态提取 → StatePool → SHP
    processed = kernel.after_agent_output(
        agent=self.name,
        raw_output=message,
    )
    # 用压缩的 SHP 替换原始消息
    return original_send(self, processed.handoff_message, recipient, **kwargs)

ConversableAgent.send_message = patched_send


# 2. 替换消息接收（输入前处理）
original_receive = ConversableAgent.receive_message

def patched_receive(self, message, sender, **kwargs):
    # Agent 输入前：解析 SHP → 渲染 PromptView
    prepared = kernel.before_agent_receive(
        agent=self.name,
        incoming_messages=[message],
    )
    return original_receive(self, prepared.prompt_view, sender, **kwargs)

ConversableAgent.receive_message = patched_receive


# 3. 替换 GroupChat 消息广播
original_broadcast = RoundRobinGroupChat.publish_message

def patched_broadcast(self, message, **kwargs):
    # 记录指标 + 状态池写入
    kernel.record_broadcast(message)
    return original_broadcast(self, message, **kwargs)

RoundRobinGroupChat.publish_message = patched_broadcast
```

### 4.3 需要注意的问题

1. **AutoGen 0.2 vs 0.7.5 API 差异巨大**：AIOS 适配的是 0.2，我们目标是 0.7.5
2. **GroupChat 广播 vs 点对点**：GroupChat 默认广播所有消息给所有参与者，我们的 SHP 需要 per-agent 渲染
3. **Studio 创建的 Agent**：Studio 通过 Web UI 创建 Agent，代码中没有 `AssistantAgent(...)` 调用
4. **异步 vs 同步**：AutoGen 0.7.5 是 async-first，monkey patch 需要处理 async/await

---

## 五、与 HQM SDK 方式的对比

| 维度 | HQM SDK（4 API） | 我们的 Adapter（透明接管） |
|---|---|---|
| **接入成本** | 极低（4 个函数调用） | 中等（需要 monkey patch 或 Proxy） |
| **控制粒度** | 粗（只有 retrieve/record/vote/remember） | 细（每个 Agent 输入输出都经过内核） |
| **角色化 PromptView** | ❌ 不能（SDK 不知道 Agent 角色） | ✅ 能（Proxy 知道每个 Agent 的角色） |
| **SHP 信封** | ❌ 不能（SDK 不介入消息传递） | ✅ 能（拦截消息生成 SHP） |
| **框架依赖** | 零（纯 Python 函数调用） | 需要框架适配代码 |
| **稳定性** | 极高（不依赖框架内部） | 中等（依赖框架 API 稳定性） |

**两者不是非此即彼，而是互补**：

- SDK 方式适合快速验证、零侵入场景
- Adapter 方式适合深度集成、完整功能场景
- 可以同时提供两种接入方式

---

## 六、实施路线图

### Phase 1：CollaborationKernel 提取（1 周）

从 V0Runtime 中拆出框架无关内核：

```python
class CollaborationKernel:
    def before_agent_receive(self, session, agent, messages) -> PreparedInput
    def after_agent_output(self, session, agent, raw_output) -> ProcessedOutput
    def before_handoff(self, session, sender, receiver, output) -> HandoffEnvelope
    def open_session(self, framework, metadata) -> Session
    def close_session(self, session_id) -> None
```

验收标准：
- 内核不 import 任何 AutoGen / LangGraph 代码
- 现有测试全部通过
- V0Runtime 退化为 CustomAgentAdapter

### Phase 2：LangGraph SDK Demo（3 天）

用 SDK 方式在 LangGraph 上验证：

```python
client = AgentLiteClient.connect()

def planner_node(state):
    result = client.run_agent("planner", state["task"], llm_call=call_llm)
    return {"plan": result.output, "memory_hits": result.memory_hits}
```

验收标准：
- 3 个 node 的 LangGraph 可运行
- A/B 对比数据：token 削减率
- pytest 验证

### Phase 3：AutoGen Agent Proxy（1 周）

实现 AutoGen Adapter 的 Agent Proxy：

```python
class RuntimeChatAgentProxy(BaseChatAgent):
    def __init__(self, inner_agent, kernel, role):
        self.inner = inner_agent
        self.kernel = kernel
        self.role = role

    async def on_messages(self, messages, cancellation_token):
        # 输入前处理
        prepared = self.kernel.before_agent_receive(...)
        # 调用原始 Agent
        response = await self.inner.on_messages(prepared.messages, cancellation_token)
        # 输出后处理
        processed = self.kernel.after_agent_output(...)
        return processed.handoff_message
```

验收标准：
- RoundRobinGroupChat + 3 个 AssistantAgent
- SHP 替换原始消息
- 角色化 PromptView 渲染

### Phase 4：AutoGen Monkey Patch（可选，1 周）

如果需要"零改动"体验：

```python
@add_framework_adapter("AutoGen 0.7.5")
def prepare_autogen_0_7_5():
    AssistantAgent.on_messages = patched_on_messages
    RoundRobinGroupChat.run_stream = patched_run_stream
```

验收标准：
- 用户代码零改动
- Studio 创建的 Agent 也能被接管
- 版本兼容性检测

---

## 七、答辩叙事建议

### 7.1 一句话定位

> 我们的系统是 Agent 间通信的"操作系统"——就像 AIOS 管理 Agent 怎么调 LLM，我们管理 Agent 怎么互相说话。

### 7.2 与 AIOS 的关系说明

> AIOS（COLM 2025）证明了将 Agent 管理提升到系统层的价值：2.1 倍吞吐提升。AIOS 管的是"Agent 和 LLM 之间"（控制面），我们管的是"Agent 和 Agent 之间"（数据面）。两者互补，共同构成完整的 Agent 运行时基础设施。

### 7.3 跨框架能力说明

> 像 AIOS 通过 Framework Adapter 适配 AutoGen、MetaGPT、Open-Interpreter 一样，我们通过 CollaborationKernel + Adapter 模式适配 AutoGen、LangGraph 等框架。Agent 代码不需要理解 SHP、StatePool、MemoryView，就像 AutoGen 代码不需要理解 AIOS 的 Scheduler 一样。

---

## 八、风险与边界

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| AutoGen 内部 API 变化 | Monkey patch 失效 | 优先用 Agent Proxy；monkey patch 作为可选增强 |
| GroupChat 广播无法 per-agent 渲染 | 角色化 PromptView 无法实现 | 必须用 Agent Proxy 或替换消息接收 |
| Studio 创建的 Agent 无法包装 | Studio 场景不透明 | 自定义 Team 组件 |
| 框架版本碎片化 | 每个版本需要独立适配 | 版本检测 + 兼容矩阵 + CI |
| "零改动"承诺无法兑现 | 用户预期落差 | 明确区分"SDK 嵌入"和"透明接管"两种模式 |

---

## 九、总结

| 问题 | 答案 |
|---|---|
| 受 AIOS 启发的想法是否可行？ | ✅ 可行，AIOS 已经验证了 Framework Adapter 模式 |
| 能否做到完全透明接管？ | ⚠️ 状态池/候选池/计量可以透明；消息压缩需要最小改动 |
| 最佳接入方式是什么？ | 分层渐进：SDK → Agent Proxy → Monkey Patch |
| 与 AIOS 的关系？ | 互补：AIOS 管 LLM 调用（控制面），我们管通信效率（数据面） |
| 最大的技术风险？ | AutoGen 版本碎片化和 GroupChat 广播机制 |
