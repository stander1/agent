# AgentLite 版本、实现与证据映射

## 文档用途

本文将主要版本阶段映射到当前代码边界和可追溯证据。历史版本的完整设计稿、
实验报告和原始实现均保留在 Git 提交历史中；当前比赛结论以最终发行门禁和
比赛结果快照为准。

## 总体演进

| 阶段 | 核心目标 | 当前主要实现 |
|---|---|---|
| v0-v2 | 建立可复现基线、Token 核算和三线最小接口 | `agent_runtime/eval/`、`agent_runtime/protocol/`、`agent_runtime/state/`、`agent_runtime/memory/` |
| v3-v4 | 完成交付契约、候选准入、可靠性守卫和生命周期治理 | `agent_runtime/core/deliverable_schema.py`、`agent_runtime/reliability/`、`agent_runtime/memory/memory_store.py` |
| v5.1-v5.11 | 强化类型化协议、通信治理、访问控制和跨任务评测 | `agent_runtime/core/communication.py`、`agent_runtime/eval/`、`web_monitor/` |
| v5.12 | 建立原生 AutoGen Team、Agent、Core 和工具边界接管 | `agent_runtime/drivers/autogen.py`、`agent_runtime/drivers/autogen_shp.py`、`agent_runtime/adapters/` |
| v5.13 | 增加不可变实验绑定、连续性记忆和事实冲突治理 | `agent_runtime/memory/context_views.py`、`agent_runtime/memory/conflict_resolver.py` |
| v5.14 | 增加评审治理、语义保真和最终交付守卫 | `agent_runtime/reliability/review_conflict_guard.py`、`agent_runtime/reliability/final_delivery_guard.py` |
| v5.15 | 完成通用语义桥、受控消歧、软件包加固和 openEuler 正式发行 | `agent_runtime/bridge/`、`agent_runtime/memory/claim_extractor.py`、`agent_runtime/memory/semantic_disambiguator.py` |

## 三条能力主线

### 结构化通信

- `agent_runtime/protocol/shp.py` 定义结构化传递协议；
- `agent_runtime/core/communication.py` 负责通信与成本治理；
- `agent_runtime/drivers/autogen.py` 和 `autogen_shp.py` 提供 AutoGen 接管；
- 成本保护仅在候选路径降低通信成本且满足契约时应用改写，否则保留原生消息。

### 非文本状态

- `agent_runtime/state/state_pool.py` 管理状态引用和生命周期；
- `agent_runtime/state/non_text.py` 处理非文本状态；
- `agent_runtime/state/structured_output.py` 生成结构化输出；
- `agent_runtime/bridge/state_memory_bridge.py` 连接当前任务状态与受治理记忆。

### 共享记忆

- `agent_runtime/memory/claim_extractor.py` 提取开放式规范化事实候选；
- `schema_registry.py` 执行 Schema 校验；
- `conflict_resolver.py` 处理冲突和修订关系；
- `memory_store.py` 执行持久化、准入与生命周期治理；
- `context_views.py` 为任务和接收方生成受控 MemoryView。

## 可靠性与可观测性

- `agent_runtime/reliability/` 提供 Provider、结构化输出、记忆采用、评审冲突
  和最终交付守卫；
- `agent_runtime/eval/` 统一记录 Token、重试、时延、接管和交付指标；
- `web_monitor/` 提供本地运行状态与跟踪信息查看入口；
- 正式实验通过 SHA256 固化制品、报告和不可变证据归档。

## 最终发行证据

| 验证层 | 当前入口 |
|---|---|
| 完整单元测试 | `python -m unittest discover -s tests -p "test_*.py"` |
| 最终 openEuler 门禁 | `experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh` |
| 比赛结果快照 | `docs/competition/RESULTS_SNAPSHOT.md` |
| 最终交付说明 | `docs/competition/v0.5.15-delivery-guide.md` |
| 最终发行说明 | `docs/release/v0.5.15-final-release-notes.md` |

最终 `0.5.15` 发行要求 wheel 与 sdist 独立安装、导入来源和 CLI 校验、
Apache-2.0 内容一致性、完整测试、必需成员检查、禁止路径检查以及独立
SHA256 校验全部通过。

## 历史追溯

查看某一阶段完整设计和实验演进：

```bash
git log --all -- docs/planning docs/experiments experiments
git log --all --follow -- <path>
```

历史提交用于证明开发全过程；当前工作树只保留中文发布叙述、最终复现入口
和发行契约所需文档。
