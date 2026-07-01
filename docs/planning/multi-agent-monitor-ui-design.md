# 多 Agent 运行观测平台 UI 设计说明

## 1. 产品定位

该界面面向比赛答辩、系统研发与实验排障三个场景，用于回答四类问题：

1. 当前有多少任务正在运行，整体健康度如何。
2. 某个任务由哪些 Agent 执行，任务如何被拆分、传递与收敛。
3. Agent 实际接收和输出了什么，通信、状态与记忆产生了多少成本。
4. 状态池与记忆池当前保存了什么，哪些对象被引用、晋升、淘汰或拒绝。

平台不是通用聊天界面，也不是营销型仪表盘。它应呈现为高密度、低装饰、可持续操作的工程运维工作台。

## 2. 信息架构

全局导航固定在左侧，包含以下一级视图：

| 视图 | 主要职责 |
| --- | --- |
| 驾驶舱 | 汇总运行规模、稳定性、成本和资源池健康度 |
| 任务实例 | 检索、筛选和进入单个实验任务 |
| 工作流观测 | 展示单任务 DAG、Agent、传递载荷和执行细节 |
| Agent 注册表 | 查看角色、调用量、延迟、错误率和资源消耗 |
| 状态池 | 查看 Hot/Warm/Cold/Tombstone 分层与生命周期 |
| 记忆池 | 查看记忆对象、声明、视图、晋升链路与准入状态 |
| 告警中心 | 汇总重试、契约异常、状态过期与记忆准入问题 |

顶部工具栏提供环境、时间范围、全局搜索、健康状态和新建实验入口。各视图共享相同导航与工具栏，避免页面跳转造成上下文丢失。

## 3. 核心页面

### 3.1 驾驶舱

首屏展示六项核心指标：任务总数、成功率、Token 节省、平均延迟、记忆命中率、契约通过率。指标下方依次提供：

- 七日任务趋势与成功率。
- 任务状态分布。
- Agent 调用量和平均延迟排名。
- 状态池分层占用。
- 最近告警与最近任务。

指标必须能够回到具体任务或对象，不作为孤立数字存在。

### 3.2 任务实例

任务列表以起始问题作为最重要的信息列，同时展示任务 ID、模式、状态、进度、Agent 数量、耗时、Token、记忆命中和开始时间。支持：

- 关键词搜索。
- 状态、模式筛选。
- 表头排序。
- 点击整行进入工作流详情。

### 3.3 工作流观测

工作流详情由任务摘要区、模式切换、画布、画布工具和右侧详情抽屉组成。

- 顶部显示原始问题、任务 ID、运行状态、耗时、Token、Agent 数和记忆命中。
- `runtime_lite` 与 `baseline_text` 使用分段控件切换，一次只展示一张 DAG。
- Agent 使用大矩形节点；传递载荷使用小型切角矩形；状态与记忆对象使用不同颜色和轮廓。
- 连线使用带箭头的正交路径。运行中的边使用低频虚线流动，不制造视觉噪声。
- 画布支持缩放、重置、适配视图、鼠标拖动和迷你地图。

### 3.4 状态池

状态池按 Hot、Warm、Cold、Tombstone 展示数量和容量，并提供对象级列表。每个状态对象至少显示：

- state_id、state_type、tier、lifecycle。
- source_agent、task_id、大小、创建时间。
- access_policy、active_readers、lineage_protected。
- summary、payload_ref、audit_payload_ref。

### 3.5 记忆池

记忆池默认使用知识图谱，支持切换节点类型和状态筛选。节点类型包括：

- MemoryView
- ClaimCard
- PromotionView
- MemoryObject
- MemoryCandidate
- StateObject

边语义包括 active-claim、memory-view、claim、promotion、source、evidence。点击节点后在右侧抽屉显示摘要、状态、来源、置信度、槽位和引用链。

## 4. 工作流视觉语义

| 对象 | 形态 | 主色 | 说明 |
| --- | --- | --- | --- |
| User Question / Start | 横向起始条 | 绿色 | 任务输入与启动点 |
| Agent | 大矩形 | 青色 | 执行单元，显示角色和运行指标 |
| Handoff Message | 切角小矩形 | 蓝色 | Agent 间结构化交接 |
| StateRef | 小矩形 | 橙色 | 状态池引用 |
| MemoryRef / ClaimCard | 小矩形 | 紫色 | 记忆与声明引用 |
| Final Deliverable | 横向结果条 | 深绿色 | 最终交付物 |
| Failed / Blocked | 红色描边 | 红色 | 异常、阻断或降级状态 |

Agent 节点展示：角色、状态、耗时、Token、输入/输出摘要。载荷节点展示：类型、来源、目标、引用数量和字节数。

## 5. 右侧详情抽屉

抽屉宽度为 520px，桌面端从右侧覆盖画布，移动端占满屏幕。不同对象共享抽屉框架：

- Agent：概览、接收内容、输出内容、契约与重试、日志。
- Message：摘要、完整 Payload、Refs、成本。
- State：概览、Payload、生命周期、引用。
- Memory：概览、声明、证据、图谱关系。
- Alert：概览、影响范围、处理建议、原始事件。

长文本使用等宽字体和可滚动区域，JSON 保留层级与换行。

## 6. 视觉系统

- 左侧导航宽 232px，背景使用深中性色而不是大面积蓝色。
- 顶栏高 56px，内容区使用浅灰背景和白色工作面。
- 主色为青色；成功为绿色；运行/警告为琥珀色；失败为红色；记忆对象为紫色。
- 圆角不超过 8px；不使用装饰性渐变、光球、插画或大面积营销式标题。
- 中文正文使用系统无衬线字体；ID、日志、代码和 JSON 使用等宽字体。
- 表格表头固定，行高保持 44-48px，优先支持扫描和比较。

## 7. Demo 数据模型

静态 Demo 以内嵌 JavaScript 对象提供数据：

```text
dashboardMetrics
tasks[]
agents[]
workflowData.runtime_lite { nodes[], edges[] }
workflowData.baseline_text { nodes[], edges[] }
states[]
memoryNodes[] / memoryEdges[]
alerts[]
```

工作流节点统一包含 `id`、`type`、`label`、`status`、`x`、`y`、`detail`；边包含 `source`、`target`、`status`。这一结构可以直接映射到后端 snapshot。

## 8. 接入现有 API

后续生产化时保持 UI 组件不变，仅替换数据适配层：

| UI 数据 | 现有来源 |
| --- | --- |
| 任务列表 | `GET /api/runs` 与 run/task 元数据 |
| 工作流节点和消息 | `GET /api/runs/{run_id}/snapshot` 的 agents、messages、timeline |
| 状态池 | snapshot 的 `state_pool` |
| 记忆图谱 | snapshot 的 `memory_graph.nodes/edges` |
| 驾驶舱指标 | `summary.json` 的 `by_mode` 聚合 |
| 实时运行 | `GET /api/experiments/{id}/snapshot` 轮询 |

需要在适配层补充 task 维度聚合、时间序列聚合和统一的对象详情结构，不应让视图直接拼接原始 trace。

## 9. 编码与交付约束

- 文档和 HTML 均使用 UTF-8。
- Demo 的 CSS、JavaScript 和数据全部内嵌，无 CDN、字体或图片依赖。
- Demo 必须可通过 `file://` 直接打开。
- 生成后使用脚本检查 HTML 标签、JavaScript 语法和 UTF-8 解码。
- 使用浏览器分别检查 1920×1080、1440×900 和 390×844，确认无重叠、截断或空白画布。
