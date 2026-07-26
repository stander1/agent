# Agent 协同低开销通信系统

本仓库用于迭代实现一个面向多 Agent 协作的跨框架运行时工具层，目标是在多 Agent 任务中通过结构化通信、非文本状态传递和共享记忆复用降低协作开销。

当前开发版本：`v5.14h formal regression acceptance`

v5.14h 真实 AutoGen/openEuler 验收步骤：
[experiments/v5.14h-formal-regression-acceptance/README.md](experiments/v5.14h-formal-regression-acceptance/README.md)

最新发行包门禁版本：`v5.13h package release gate`

实验结果记录见：

- [docs/experiments/v0-v1-results.md](docs/experiments/v0-v1-results.md)
- [docs/experiments/v2-llm-travel-results.md](docs/experiments/v2-llm-travel-results.md)
- [docs/experiments/v2-longcontext-travel-results.md](docs/experiments/v2-longcontext-travel-results.md)
- [docs/experiments/v2-longcontext-quality-judge.md](docs/experiments/v2-longcontext-quality-judge.md)
- [docs/experiments/v2-security-b-results.md](docs/experiments/v2-security-b-results.md)
- [docs/experiments/v3-lite-smoke-results.md](docs/experiments/v3-lite-smoke-results.md)
- [docs/experiments/v3-full-ab-results.md](docs/experiments/v3-full-ab-results.md)
- [docs/experiments/v3.1-final-schema-results.md](docs/experiments/v3.1-final-schema-results.md)
- [docs/experiments/v3.2-reliability-guard-results.md](docs/experiments/v3.2-reliability-guard-results.md)
- [docs/experiments/v3.3-memory-admission-results.md](docs/experiments/v3.3-memory-admission-results.md)
- [docs/experiments/v4.0-cost-lifecycle-results.md](docs/experiments/v4.0-cost-lifecycle-results.md)
- [docs/experiments/v4.0-full-ab-results.md](docs/experiments/v4.0-full-ab-results.md)
- [docs/experiments/v5.9-mimo-cross-task-results.md](docs/experiments/v5.9-mimo-cross-task-results.md)
- [docs/experiments/v5.12a-kernel-boundary-results.md](docs/experiments/v5.12a-kernel-boundary-results.md)
- [docs/experiments/v5.12b-launcher-results.md](docs/experiments/v5.12b-launcher-results.md)
- [docs/experiments/v5.12c-autogen-driver-results.md](docs/experiments/v5.12c-autogen-driver-results.md)
- [docs/experiments/v5.12d-autogen-native-results.md](docs/experiments/v5.12d-autogen-native-results.md)
- [docs/experiments/v5.12e-autogen-codec-results.md](docs/experiments/v5.12e-autogen-codec-results.md)
- [docs/experiments/v5.12f-autogen-shp-shadow-results.md](docs/experiments/v5.12f-autogen-shp-shadow-results.md)
- [docs/experiments/v5.12g-autogen-long-shadow-results.md](docs/experiments/v5.12g-autogen-long-shadow-results.md)
- [docs/experiments/v5.12h-autogen-handoff-tool-results.md](docs/experiments/v5.12h-autogen-handoff-tool-results.md)
- [docs/experiments/v5.12i-autogen-broadcast-shadow-results.md](docs/experiments/v5.12i-autogen-broadcast-shadow-results.md)
- [docs/experiments/v5.12j-autogen-broadcast-dry-run-results.md](docs/experiments/v5.12j-autogen-broadcast-dry-run-results.md)
- [docs/experiments/v5.12k-autogen-real-rewrite-results.md](docs/experiments/v5.12k-autogen-real-rewrite-results.md)
- [docs/experiments/v5.12l-autogen-rewrite-echo-results.md](docs/experiments/v5.12l-autogen-rewrite-echo-results.md)
- [docs/experiments/v5.12m-autogen-rewrite-fallback-results.md](docs/experiments/v5.12m-autogen-rewrite-fallback-results.md)
- [docs/experiments/v5.12n-autogen-rewrite-fallback-matrix-results.md](docs/experiments/v5.12n-autogen-rewrite-fallback-matrix-results.md)
- [docs/experiments/v5.12o-autogen-handoff-tool-guard-results.md](docs/experiments/v5.12o-autogen-handoff-tool-guard-results.md)
- [docs/experiments/v5.12p-autogen-handoff-typed-candidate-results.md](docs/experiments/v5.12p-autogen-handoff-typed-candidate-results.md)
- [docs/experiments/v5.12q-autogen-handoff-rewrite-switch-results.md](docs/experiments/v5.12q-autogen-handoff-rewrite-switch-results.md)
- [docs/experiments/v5.12r-autogen-handoff-rewrite-matrix-results.md](docs/experiments/v5.12r-autogen-handoff-rewrite-matrix-results.md)
- [docs/experiments/v5.12s-autogen-tool-summary-candidate-results.md](docs/experiments/v5.12s-autogen-tool-summary-candidate-results.md)
- [docs/experiments/v5.12t-autogen-tool-summary-matrix-results.md](docs/experiments/v5.12t-autogen-tool-summary-matrix-results.md)
- [docs/experiments/v5.12u-autogen-tool-summary-rewrite-switch-results.md](docs/experiments/v5.12u-autogen-tool-summary-rewrite-switch-results.md)
- [docs/experiments/v5.12u-autogen-integrated-rewrite-results.md](docs/experiments/v5.12u-autogen-integrated-rewrite-results.md)
- [docs/experiments/v5.12v-autogen-team-rewrite-results.md](docs/experiments/v5.12v-autogen-team-rewrite-results.md)
- [docs/experiments/v5.12w-autogen-team-rewrite-matrix-results.md](docs/experiments/v5.12w-autogen-team-rewrite-matrix-results.md)
- [docs/experiments/v5.12x-autogen-team-benchmark-results.md](docs/experiments/v5.12x-autogen-team-benchmark-results.md)
- [docs/experiments/v5.12y-release-cli-results.md](docs/experiments/v5.12y-release-cli-results.md)
- [docs/experiments/v5.12z-package-release-gate-results.md](docs/experiments/v5.12z-package-release-gate-results.md)
- [docs/experiments/v5.13f-autogen-core-transport-results.md](docs/experiments/v5.13f-autogen-core-transport-results.md)
- [docs/experiments/v5.13f-autogen-core-content-rewrite-results.md](docs/experiments/v5.13f-autogen-core-content-rewrite-results.md)
- [docs/experiments/v5.13f-autogen-core-message-matrix-results.md](docs/experiments/v5.13f-autogen-core-message-matrix-results.md)
- [docs/experiments/v5.13f-autogen-core-response-rewrite-results.md](docs/experiments/v5.13f-autogen-core-response-rewrite-results.md)
- [docs/experiments/v5.13f-autogen-core-final-output-guard-results.md](docs/experiments/v5.13f-autogen-core-final-output-guard-results.md)
- [docs/experiments/v5.13g-autogen-mixed-team-core-results.md](docs/experiments/v5.13g-autogen-mixed-team-core-results.md)
- [docs/experiments/v5.13h-package-gate-results.md](docs/experiments/v5.13h-package-gate-results.md)
- [docs/experiments/v5.13h-autogen-web-backend-results.md](docs/experiments/v5.13h-autogen-web-backend-results.md)
- [docs/experiments/v5.13i-autogen-shared-memory-results.md](docs/experiments/v5.13i-autogen-shared-memory-results.md)
- [docs/experiments/v5.13j-final-delivery-memory-cost-repair.md](docs/experiments/v5.13j-final-delivery-memory-cost-repair.md)
- [docs/experiments/v5.13k-termination-memory-accounting-root-cause.md](docs/experiments/v5.13k-termination-memory-accounting-root-cause.md)
- [docs/experiments/v5.13m-chronology-grounding-results.md](docs/experiments/v5.13m-chronology-grounding-results.md)
- [docs/experiments/v5.13m-openeuler-acceptance-20260720.md](docs/experiments/v5.13m-openeuler-acceptance-20260720.md)
- [docs/experiments/v5.13n-context-dedup-rewrite-metrics.md](docs/experiments/v5.13n-context-dedup-rewrite-metrics.md)
- [docs/experiments/v5.13o-immutable-experiment-binding.md](docs/experiments/v5.13o-immutable-experiment-binding.md)
- [docs/experiments/v5.13p-autogen-studio-run-binding.md](docs/experiments/v5.13p-autogen-studio-run-binding.md)
- [docs/experiments/v5.13q-continuity-memory-guard.md](docs/experiments/v5.13q-continuity-memory-guard.md)
- [docs/experiments/v5.13r-minimal-sufficient-role-views.md](docs/experiments/v5.13r-minimal-sufficient-role-views.md)
- [docs/experiments/v5.13s-dynamic-capability-profiles.md](docs/experiments/v5.13s-dynamic-capability-profiles.md)
- [docs/experiments/v5.14g-review-conflict-governance.md](docs/experiments/v5.14g-review-conflict-governance.md)
- [docs/experiments/v5.14h-formal-regression-acceptance.md](docs/experiments/v5.14h-formal-regression-acceptance.md)
- [docs/release/v5.12z-final-release-notes.md](docs/release/v5.12z-final-release-notes.md)

## v5.14h Current Note

`v5.14h` reruns the unchanged v5.14f A1-A10/B1-B10 formal matrix after the
generic v5.14g review-conflict fix. The runtime report now audits dynamic review
authority, targeted memory deprecation, blocker admission, unrelated-memory
protection, and positive-review side effects. Frozen task hashes and all legacy
cost, quality, delivery, memory, fidelity, protocol, and routing thresholds are
checked before the new governance gates are evaluated.

## v5.13s Current Note

`v5.13s` replaces fixed Planner/Writer/Reviewer production views with versioned capability profiles discovered from each real Agent's role description, tools, declared metadata, outputs, and runtime feedback. Tool changes update capability sources automatically; routing uses the final-design RouteScore and deterministic cold-start tie resolver; semantic uncertainty selects a planning-capable Agent without depending on its name. AutoGen remains the scheduling owner while AgentLite records advisory routing and hydrates only the matching receiver's capability/action context view. Legacy fixed-role helpers remain compatibility-only and are not used by the current AutoGen path.

## v5.13r Current Note

`v5.13r` completes the continuity guard with minimal sufficient context injection. It creates generic, rules-first task and memory views for planner, writer, reviewer, or unknown roles; keeps original facts traceable to StatePool and admitted MemoryView objects; hydrates only the matching receiver view before each AutoGen agent call; and escalates to bounded source-state spans only when an explicitly requested semantic field is absent. Reports now expose source-view tokens, role-view tokens, field fetches, receiver hydration, end-to-end collaboration cost, and final-delivery validation. No domain-specific Question A rules or control-LLM calls are used.

## v5.13q Current Note

`v5.13q` prevents the token cost gate from silently dropping admitted memory that a follow-up task explicitly depends on. Generic Chinese and English continuation cues such as references to a previous result, named step, existing plan, or requested revision mark the MemoryView as required. When relevant memory was actually retrieved, schema and message-contract guards still apply, but a pure `token_not_reduced` result no longer removes that context. Trace and reports expose required-context decisions, cost overrides, and actual continuity-memory injections; any negative token saving remains visible as the cost of preserving task correctness.

## v5.13p Current Note

`v5.13p` separates a long-running AutoGen Studio backend process from each UI Run. It reads AutoGen Studio 0.4.2.2's native `RunContext` without modifying Studio, propagates one `framework_run_id` through nested Team, Agent, Core, state, memory, rewrite, and model-client trace events, and writes one lifecycle manifest per Run. When Studio is started with a direct `--appdir`, AgentLite reads the existing SQLite database in query-only mode to associate the native Run with its Studio session and Team. The monitor lists these Runs as separate tasks instead of presenting the whole backend process as one task.

Export one Run independently:

```bash
agentlite report autogen-run \
  --data-dir .agentlite-exp/studio-agentlite-A \
  --session-id latest \
  --run-id autogenstudio:22 \
  --format markdown \
  --output exports/studio-run-22.md
```

`--run-id latest` selects the latest Run in the selected AgentLite process session. A Run report never fills missing model usage with a process-level Provider total because that would mix other UI tasks into the selected Run.

The v5.13p release gate passes all 10 steps and the full unit suite passes 189 tests. Long code-side Run identifiers are mapped to bounded manifest directory names so the same gate also succeeds from a deeply nested Windows workspace; the complete identifier remains in the trace and manifest payload.

## v5.13o Current Note

`v5.13o` makes code-side experiment outputs immutable and binds one Provider usage file to one exact AgentLite launch. `agentlite autogen --experiment-dir RUN_DIR -- ... --output-dir RUN_DIR` now creates an exclusive launch binding, rejects directory reuse, verifies `run_id` and `session_id`, copies the exact AgentLite session into the experiment archive, and automatically exports JSON and Markdown session reports. Stateful comparison rejects unbound or mismatched runs by default; `--allow-legacy-unbound` is only for older evidence. The archive smoke uses no external LLM and verifies successful binding, self-contained session evidence, automatic reports, Provider totals, and overwrite rejection. The full suite passes 184 tests and the expanded release gate passes all nine steps.

## v5.13n Current Note

`v5.13n` removes a shared MemoryView from the candidate Prompt View when its factual content is already covered by the current task, user history, or latest upstream artifact. The same memory reference is removed from the SHP envelope, while memories with new facts or changed numeric values remain available. The rule is local and deterministic; it does not call an LLM. Session reports now distinguish all rewrite audit decisions, costed decisions, successful mutations, fallbacks, cost-gate fallbacks, and message-contract fallbacks. Replaying the latest openEuler smoke trace now correctly reports 34 audit decisions, 8 costed decisions, 0 successful rewrites, 8 cost-gate fallbacks, and 26 contract fallbacks. The full suite passes 177 tests and the release gate passes all eight steps.

## v5.13m Current Note

`v5.13m` makes managed Agent input chronology-aware: the current raw user task and latest upstream artifact are preserved, prior raw user requests provide authoritative grounding, and repeated rewritten message clones are collapsed into one Prompt View. The final-delivery guard now accepts an explicitly bounded corrected result while rejecting direct claims of user confirmation that are absent from raw user history. Rejected candidates cannot terminate the Team or enter long-term memory. The full suite passes 174 tests. The final MiMo A1-A3 managed smoke completed all tasks in one Planner/Writer/Reviewer cycle each: 9 calls, 51,572 LLM tokens, no Provider retries, and 11.11% measured transport-token savings. This is a managed smoke result, not a replacement for a same-version three-group quality comparison. The openEuler 24.03 LTS-SP3 acceptance gate also passes; its short A1-A2 rerun correctly fell back to native messages when compression was not cheaper, so that run is compatibility evidence rather than a Token-saving claim.

## v5.13k Current Note

`v5.13k` aligns AutoGen termination, experiment delivery status, and long-term memory admission around the same exact final marker. Team outputs without the marker remain auditable candidates but cannot become long-term memory. The ordinary-developer code and Studio templates now use `ReviewerFinalTextTermination`, stage-aware travel prompts, and the same 9-turn cap across native, observed, and managed groups. Token reports split direct wire, Prompt View, and retrieved memory into mutually exclusive components, and `agentlite report autogen-session --provider-usage ...` can merge usage from custom model clients that bypass AutoGen's standard usage hook.

## v5.13j Current Note

`v5.13j` adds a rules-first semantic delivery guard after the exact Reviewer marker, one context-pruned Reviewer repair for experiment failures, validated-FinalArtifact-only memory admission, finer travel memory slots, and a strict cost gate that no longer bypasses `token_not_reduced` when memory references exist. Session reports now separate actual rewrite cost, shadow compression potential, Provider usage, unique memory retrieval, and receiver fanout. A memory hit is no longer counted as useful without downstream evidence.

## v5.13i Current Note

`v5.13i` now connects AutoGen takeover to the canonical TLC-Memory path. In `real-rewrite` mode, Agent outputs are first written to StatePool. Intermediate Agent outputs become `MemoryCandidate` records with `pending` status, while a completed Team result can pass rules-first admission and become a persistent `MemoryView`. A later managed process with the same data directory, workspace scope, and Team participant signature retrieves that MemoryView and injects it into the Team task without requiring the user's AutoGen code or Studio configuration to import AgentLite.

Shared memory is enabled by the normal `agentlite autogen -- ...` command. It is disabled in `--rewrite off` observation mode, so native/observed experiments remain uncontaminated. Different Team participant signatures use strict retrieval scopes and cannot retrieve one another's memory. The optional `AGENTLITE_MEMORY_SCOPE` environment variable can provide an explicit experiment or project namespace; otherwise AgentLite derives a stable scope from the target working directory.

Real AutoGen 0.7.5 validation used two independent managed Python processes. The first process admitted one Team final result; the second process loaded the persistent snapshot and recorded one memory hit with a `127`-token MemoryView. That historical run labeled the hit as useful; v5.13j now classifies it as unassessed until downstream evidence exists. The view entered three receiver prompts, producing `381` fanout retrieved-memory tokens. The actual Team input contained both the shared-memory marker and the first process's confirmed fact. The ordinary AutoGen script did not import AgentLite.

This bridge provides cross-task context continuity, but it does not by itself fix weak Agent prompts, an unsuitable Team selection policy, or a termination condition that stops before a complete answer. Those remain part of the Studio Team configuration and quality experiment.

## v5.13h Historical Note

`v5.13h` builds on the v5.13g mixed-link takeover, which extends AutoGen coverage from AgentChat / Team entry points down to `autogen_core.SingleThreadedAgentRuntime.send_message()` and `publish_message()`. Core runtime direct and publish messages are now written to StatePool, converted into compact SHP shadow wire packets, rendered as receiver Prompt Views, and measured in trace.

With `AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE=1`, v5.13g also supports type-preserving real rewrite for Core messages that expose a string `content`, `body`, or `text` field. The receiver still gets the original Python message type, while the long text field is replaced by an AgentLite SHP state-ref packet plus Prompt View.

With `AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE=prompt-view`, the receiving `BaseAgent.on_message()` path now converts AgentLite wire content into a Prompt View before the user's Core handler runs.

Latest smoke evidence: the new mixed Team/Core smoke passed through `RoundRobinGroupChat.run_stream(task=...) -> BaseChatAgent.on_messages() -> SingleThreadedAgentRuntime.send_message(sender=...) -> Core worker -> Team final output`. Team rewrite applied with broadcast token delta `1409`; the Bridge Agent saw the Team packet directly without a second AgentChat rewrite; Core request rewrite/hydration was `1/1` with token delta `818`; Core response rewrite/hydration was `1/1` with token delta `966`; final Team output contained `DONE_MIXED` and did not leak AgentLite wire.

v5.13h adds the package gate for this mixed takeover path. The wheel `multi_agent_collaboration_runtime-0.5.13.dev0-py3-none-any.whl` was built, installed into an isolated target site, and verified from outside the source tree. Installed `agentlite autogen -- python app.py` passed the mixed Team/Core rewrite check: Team packets carried state refs and Prompt Views, Core request/response arrived as Prompt Views, native markers did not leak, and final Team output did not expose AgentLite wire. The generic `agentlite run --framework autogen --rewrite all -- python app.py` path remains supported and passed the Team rewrite package check.

The web backend smoke also passed: a stdlib HTTP backend started by `agentlite autogen -- python web_backend.py` handled a `/run` request, created AutoGen Team objects inside the request handler, and still had the Team task rewritten into StatePool refs and Prompt Views. This proves process-lifetime injection for a web-style backend started under AgentLite.

This is not yet universal replacement for arbitrary AutoGen Core objects, distributed/remote runtime transport, automatic AutoGen Studio process injection, or attachment to an already-running backend process.

## v5.12z Historical Release Note

`v5.12z` hardens the package release path around the validated v5.12y CLI. The wheel can be built, installed into an isolated target site, and used from outside the source tree to run `agentlite run --framework autogen --rewrite all -- python app.py`.

v5.12z package gate evidence: installed package version `0.5.12.post3`, installed import path under target site, AutoGen doctor passed, first Team input was replaced with an AgentLite packet, `native_marker_count=0`, `rewrite_applied=true`, `fallback_reasons=[]`, native full broadcast cost `6213` tokens, wire plus Prompt View cost `882` tokens, and quality score `12/12`.

Final local release artifacts are generated under `dist/`:

```text
multi_agent_collaboration_runtime-0.5.12.post3-py3-none-any.whl
multi_agent_collaboration_runtime-0.5.12.post3.tar.gz
```

## AgentLite 托管启动

安装仓库后可以使用 AutoGen 专用入口，默认启用当前支持的全部 AutoGen 改写门控：

```powershell
agentlite autogen -- python app.py
```

该命令默认使用持久化共享记忆。只要后续任务使用同一个 `--data-dir`、同一工作目录和同一组 Team 成员，已通过准入的 Team 最终结论就可以跨进程复用：

```powershell
agentlite autogen --data-dir "$HOME/.agentlite" -- python app.py
```

原生观察组使用 `--rewrite off`，不会读取或写入共享记忆：

```powershell
agentlite autogen --rewrite off -- python app.py
```

也可以使用统一命令入口：

```powershell
agentlite run --framework autogen -- python app.py
```

也支持等价别名：

```powershell
agentlite start --framework autogen -- python app.py
```

开启真实改写推荐使用 CLI 预设：

```powershell
agentlite run --framework autogen --rewrite all -- python app.py
```

`--rewrite` 可选值：

```text
off       仅观察与影子审计，不真实改写
agent     改写简单 agent 文本输入
team      改写 AutoGen Team 入口长 task
non-text  改写安全的 Handoff / ToolSummary content
all       启用当前 v5.12 支持的全部真实改写门控
```

环境自检：

```powershell
agentlite doctor --framework autogen
agentlite version
```

发布门禁：

```powershell
python .\examples\run_release_gate.py
```

`v5.12c` 已实现 AutoGen Driver 的托管导入钩子。用户脚本不需要显式导入 AgentLite；在 `agentlite run --framework autogen -- python app.py` 下，Driver 会在用户脚本导入 AutoGen 前安装受控 patch，并把 AgentChat / Runtime 调用事件桥接到 `CollaborationKernel`、trace 和 session-local StatePool。

```text
Bootstrap status: active
Driver hooks: active
```

`v5.12d` 已在独立 `agentlite-autogen` conda 环境中验证真实 AutoGen `0.7.5` Python 代码端最小样例。原生样例只导入 AutoGen，不导入 AgentLite；托管启动后能够产生 AgentLite trace 和 session-local StatePool `artifact_state`。

`v5.12e` 已补充 AutoGen Message Codec，覆盖 `TextMessage`、`HandoffMessage`、`ToolCallRequestEvent`、`ToolCallExecutionEvent` 和 `ToolCallSummaryMessage` 的稳定映射，并将解码结果写入 trace。

`v5.12f` 已在不改变 AutoGen 原生消息和返回值的前提下，将 codec 输出、StatePool `state_ref` 与 `CollaborationKernel.build_handoff` 接通，生成 SHP handoff 影子包；trace 同时记录完整审计包和紧凑 wire 包，通信 token 口径使用紧凑 wire 包。

`v5.12g` 新增长内容 AutoGen 原生任务，在影子模式下对比原生长文本、紧凑 SHP wire 包、以及 `state_ref + Prompt View` 的端到端 token 成本。最近一次长内容 smoke 中，原生输出文本为 `38227` tokens，`SHP wire + Prompt View` 为 `1885` tokens，摘要读取口径下降约 `95.1%`。

`v5.12h` 增加真实 AutoGen `HandoffMessage`、`ToolCallRequestEvent`、`ToolCallExecutionEvent` 和 `ToolCallSummaryMessage` 覆盖。最近一次 handoff/tool-call smoke 中，`decoded_message_kinds` 覆盖 `handoff`、`tool_call`、`tool_result`、`tool_summary`，并记录 `autogen_transport_input_state: 2`，原生输出文本为 `36948` tokens，`SHP wire + Prompt View` 为 `2764` tokens，下降约 `92.5%`。

`v5.12i` 增加 AutoGen Team 广播替换前的影子计划。Driver 会从真实 `RoundRobinGroupChat` 中提取参与者列表，为每个接收方生成 per-receiver SHP wire 包并统计 Prompt View 成本。最近一次三 Agent smoke 中，原生全文广播口径为 `42858` tokens，per-receiver `SHP wire + Prompt View` 为 `2368` tokens，下降约 `94.5%`。

`v5.12j` 增加广播替换安全开关和 dry-run 改写审计。`AGENTLITE_AUTOGEN_BROADCAST_MODE` 支持 `shadow-only`、`dry-run-rewrite` 和 `real-rewrite`；当前真实改写仍会回退。最近一次 dry-run smoke 中，候选改写 `rewrite_safe_count=2`、`fallback_required_count=0`、`real_message_mutation_count=0`，原生全文广播口径为 `42858` tokens，dry-run `SHP wire + Prompt View` 为 `2350` tokens，下降约 `94.5%`。

`v5.12k` 在 `real-rewrite` 模式下增加简单 `TextMessage` 的 agent 输入层真实改写。最近一次真实 AutoGen smoke 中，Team 层广播策略仍安全回退，但 `planner`、`writer`、`reviewer` 三个 agent 的输入均发生真实替换，`real_message_mutation_count=3`，输入 token 从 `17124` 降到 `1073`。

`v5.12l` 增加 EchoAgent 三模式对照，直接验证 agent 实际读到的内容。最近一次 smoke 中，`shadow-only` 和 `dry-run-rewrite` 下 agent 仍看到 `8372` 字符原生长文本；`real-rewrite` 下 agent 看到包含 `AGENTLITE_REAL_REWRITE v1`、`state_refs` 和 `prompt_view` 的紧凑内容，字符数降到 `1124`，token 从 `4160` 降到 `338`，`rewrite_attempt_count=1`、`rewrite_applied_count=1`、`rewrite_fallback_count=0`。

`v5.12m` 增加真实改写失败分桶和失败样例 smoke。当前不支持真实改写的 `HandoffMessage` 会安全回退，agent 仍收到原生消息；trace 记录 `fallback_reasons=['non_text_message_present']`、`fallback_buckets=['unsupported_message_type']`、`fallback_count=1`、`real_message_mutation_count=0`。同时，简单 `TextMessage` 成功改写回归仍通过，token 从 `4160` 降到 `336`。

`v5.12n` 将失败样例扩展为回退矩阵，覆盖空消息、空文本、短文本成本门控失败、非 TextMessage 四类场景。最近一次矩阵 smoke 中，`fallback_count=4`、`applied_count=0`、`real_message_mutation_count=0`，分桶覆盖 `input_contract_empty`、`empty_payload`、`cost_gate_failed`、`unsupported_message_type`。简单 `TextMessage` 成功改写回归仍通过，token 从 `4160` 降到 `341`。

`v5.12o` 在不扩大真实改写范围的前提下，为 `HandoffMessage` 和 `ToolCallSummaryMessage` 增加最小安全守卫审计。最近一次 guard smoke 中，`fallback_count=2`、`real_message_mutation_count=0`，安全审计记录 `handoff_rewrite_requires_target_preservation`、`tool_rewrite_requires_call_lineage`、`tool_rewrite_requires_result_lineage`，并保留 `handoff_targets=['guard']`、`tool_call_ids=['call_guard_1']`、`tool_result_call_ids=['call_guard_1']`。简单 `TextMessage` 成功改写回归仍通过，token 从 `2147` 降到 `299`。

`v5.12p` 增加 `HandoffMessage` typed rewrite candidate。最近一次 candidate smoke 中，候选合约为 `autogen_handoff_typed_rewrite_candidate.v1`，`candidate_safe_count=1`、`mutation_applied_count=0`，并验证 `message_type/source/target/id/metadata/context/content` 均保持正确；Handoff candidate token 从 `618` 降到 `304`。简单 `TextMessage` 成功改写回归仍通过，token 从 `2147` 降到 `300`。

`v5.12q` 增加受控 HandoffMessage 真实改写开关 `AGENTLITE_AUTOGEN_HANDOFF_REWRITE`。默认关闭时只生成 typed candidate，`mutation_applied_count=0`；开关开启时，在 candidate 语义安全、SHP schema 有效、Prompt View 可用、token 确实降低的条件下，只替换 `HandoffMessage.content`，并保留 `source/target/id/metadata/context`。最近一次开启 smoke 中，`mutation_applied_count=1`、`applied_count=1`、`fallback_count=1`，Handoff candidate token 从 `618` 降到 `300`。

`v5.12r` 增加 Handoff rewrite 压测矩阵。矩阵覆盖开关关闭、单条长 Handoff、短内容成本门、多条 Handoff、带 `context` 的 Handoff 五类边界；最近一次矩阵 smoke 中，关闭模式 `applied_count=0`，开启模式 4 个 Handoff 调用里只改写 2 个安全候选，短内容因 `token_reduced=false` 回退，多 Handoff 保持原生消息，带 `context` 的 Handoff 在 `context_count=2` 时仍能保留控制字段并完成改写。

`v5.12s` 增加 `ToolCallSummaryMessage` typed rewrite candidate。该版本仍不真实改写 ToolCallSummary；agent 实际收到的仍是 AutoGen 原生消息，但 trace 中会记录 `autogen_tool_summary_typed_rewrite_candidate.v1` 候选，验证 `tool_calls.id/name`、`results.call_id/name/is_error` 等工具调用链路字段完整保留。最近一次 smoke 中，`candidate_safe_count=1`、`mutation_applied_count=0`，候选 token 从 `1082` 降到 `333`。

`v5.12t` 增加 `ToolCallSummaryMessage` rewrite matrix。矩阵覆盖正常 `call_id` 对齐、`call_id` 不匹配、多工具调用、多工具结果、短内容不降成本、`is_error=true` 错误结果保留等边界；最近一次 matrix 中，5 个候选全部记录，`candidate_safe_count=4`、`mutation_applied_count=0`，不匹配链路触发 `tool_result_lineage_complete=false`，短内容触发 `token_reduced=false`，所有 agent 仍收到原生 ToolCallSummary。

`v5.12u` 增加 `ToolCallSummaryMessage` 真实改写开关 `AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE`。关闭时只记录 typed candidate，不改 AutoGen 原生消息；开启且 `tool_calls/results` 链路、schema、Prompt View、`token_reduced` 全部通过时，只替换 `content` 字段。最近一次 switch smoke 中，开启模式 `applied_count=1`、`fallback_count=0`、`mutation_applied_count=1`，输入 token 从 `1371` 降到 `327`，并保留 `tool_call_ids/tool_result_call_ids=['call_tool_switch_1']`。
`v5.12u` 同时增加综合 AutoGen-only 用户脚本验证：脚本只导入 AutoGen，不导入 AgentLite；托管启动后，`TextMessage`、`HandoffMessage`、`ToolCallSummaryMessage` 三类消息在 agent 输入层均完成真实替换，`applied_count=3`、`fallback_count=0`，输入 token 从 `3930` 降到 `942`，并保留 ToolCallSummary 的工具调用链路。
`v5.12v` 增加 AutoGen Team 入口层真实改写开关 `AGENTLITE_AUTOGEN_TEAM_REWRITE`。开启后，`RoundRobinGroupChat.run_stream(task=长文本)` 会在 AutoGen 原生分发前，把长任务写入 StatePool，并将传入 Team 的 task 替换成 `AGENTLITE_TEAM_REAL_REWRITE v1` 广播清单和接收方 Prompt Views。最近一次 Team rewrite smoke 中，`applied_count=1`、`fallback_count=0`，原始 task token 从 `1596` 降到 `1044`，按三接收方广播口径从 `4788` 降到 `899`。
`v5.12w` 增加 Team rewrite 边界矩阵。矩阵验证：开关关闭时保留原生任务；长 `task: str` 可真实替换；短 task 因成本门控安全回退；`TextMessage task` 和 `list task` 因类型暂不支持安全回退；`task=None` 因缺少任务参数安全回退；`RoundRobinGroupChat.run(...)` 间接路径仍会触发 `run_stream` 改写。最近一次 matrix 中，off 组 `applied_count=0`、`fallback_count=1`，on 组 `applied_count=2`、`fallback_count=4`，task token 总下降 `1519`。

当前仍未接入 AutoGen Studio 网页端，也未重写 AutoGen 所有 Team 内部私有消息队列；Team 层真实替换目前只覆盖 `task: str` 的入口场景。

核心安装仅需要 `tiktoken`：

```powershell
python -m pip install -e .
```

只有在需要 Hugging Face tokenizer 时才安装可选依赖：

```powershell
python -m pip install -e ".[transformers]"
```

只有在验证真实 AutoGen 接入时才安装 AutoGen 可选依赖：

```powershell
python -m pip install -e ".[autogen]"
```

安装后可运行原生 AutoGen smoke：

```powershell
python .\examples\run_autogen_native_smoke.py
```

版本切换与 GitHub 浏览方式见：[docs/versioning.md](docs/versioning.md)

版本路线与代码实现对照见：[docs/planning/version-implementation-mapping.md](docs/planning/version-implementation-mapping.md)。后续小版本必须先在该文档中校准归属主版本和对应创新方案模块。

## v0/v1 目标

v0 是评测地基，先建立稳定、可复现的纯文本基线和指标系统。
v1 在此基础上加入三线 Lite 闭环：

- 运行确定性的多 Agent baseline 任务。
- 统计文本通信、prompt、延迟和预留的 state/memory 字段。
- 使用 tokenizer-backed `TokenCounter` 记录 token 与 tokenizer 元数据。
- 导出 `trace.jsonl`、`metrics.csv`、`metrics.json`、`summary.json`。
- 在 `runtime_lite` 模式下使用 SHP-lite、StatePool-lite、MemoryStore-lite。
- 将上游输出写入 file-backed StatePool，并通过 state_refs 渲染 Prompt View。
- 将 Writer/Reviewer 的阶段结论写入 MemoryStore，并在后续连续任务中检索复用。

## v0 快速启动

```powershell
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v1_runtime_lite.py --rounds 10 --mode both
```

`--mode both` 会运行：

```text
baseline_text
runtime_lite
```

输出目录默认位于 `runs/`，其中 `state_payloads/` 保存 v1 的结构化状态 payload。

## v1 Smoke Result

最近一次 10 轮双模式 benchmark：

```text
baseline_text:
  direct_text_tokens: 159140
  prompt_view_tokens: 131260
  end_to_end_collaboration_tokens: 290400

runtime_lite:
  direct_text_tokens: 52939
  prompt_view_tokens: 104773
  retrieved_memory_tokens: 15644
  end_to_end_collaboration_tokens: 173356
```

相对 baseline：

```text
direct_text_tokens 降低约 66.7%
end_to_end_collaboration_tokens 降低约 40.3%
```

## Token 统计

v0 要求使用 `tiktoken` 或 `transformers` 进行 token 统计，不会静默使用字符估算。

只有显式传入以下参数时才允许估算：

```powershell
--allow-estimated-tokens
```

估算结果会在指标中标记：

```text
token_count_method=estimated
```

正常情况下应看到类似：

```text
token_count_method=compatible
tokenizer_name=tiktoken:cl100k_base
```

## v2 LLM 实验入口

v2 开始提供可选真实 LLM evaluation harness。API key 不写入仓库，建议通过本地环境变量提供：

```powershell
$env:MIMO_API_KEY='你的本地 key'
F:\software\anaconda\envs\multi-agent-demo\python.exe .\examples\run_v2_llm_eval.py --rounds 1 --mode both --config .\configs\llm.mimo.example.json
```

默认任务集为 `benchmarks/travel_task_group_a.json`，来自 `question/A.md` 的个性化旅行规划任务组。

当前 MiMo 示例配置使用开发者计划 Token Plan 的 OpenAI-compatible 地址：

```text
https://token-plan-cn.xiaomimimo.com/v1
```

`https://token-plan-cn.xiaomimimo.com/anthropic` 属于 Anthropic-compatible 接口，当前 v2 runner 暂未启用。

v2.3 将 Runtime State Pool 升级为 Hot / Warm / Cold 三层。完整 artifact content 只进入 Cold audit payload，普通 Agent 仍只读取 Prompt View。

最新 A/B 两组连续任务实验显示：

```text
A1-A10 旅行规划:
  end_to_end_collaboration_tokens 降低 91.7%
  llm_total_tokens 降低 87.2%

B1-B10 合成安全审计:
  end_to_end_collaboration_tokens 降低 90.9%
  llm_total_tokens 降低 86.7%
```

但两组 v2.3 的 MiMo 严格质量裁判都判定 `baseline_text` 的最终收束结果更完整，说明 v3 需要重点补 ClaimCard / MemoryView / Deliverable View / Reviewer 修复闭环，避免低开销压缩牺牲最终交付质量。

v3-lite 已开始补齐这条质量链路：当前实现了 Promotion View、ClaimCard、MemoryView、Alias Mapping 和 Deliverable View。完整 A/B 重跑显示：A 组旅行规划仍由 `baseline_text` 质量胜出，说明通用 Deliverable View 还不足以生成具体行程手册；B 组合成安全审计中 `runtime_lite` 质量反超 baseline，说明 Claim/Evidence/Decision Log 类任务已经能从 v3 记忆视图中受益。

v3.1 在 v3 基础上加入 Final Deliverable Schema：A10/B10 最终收束任务会注入领域化 schema，Reviewer 可触发一次短上下文修复，并记录 `deliverable_schema_complete` / `final_quality_retry_count`。正式 A/B 实验显示：

```text
A1-A10 旅行规划:
  end_to_end_collaboration_tokens 降低 88.8%
  llm_total_tokens 降低 85.1%
  schema 覆盖 16/16
  质量裁判 baseline_text 胜出 26 vs 22

B1-B10 合成安全审计:
  end_to_end_collaboration_tokens 降低 89.7%
  llm_total_tokens 降低 86.3%
  schema 覆盖 12/12
  质量裁判 runtime_lite 胜出 35 vs 22
```

结论：v3.1 已证明低开销通信和最终交付 schema 可以兼容；但 A 组仍需要在后续版本增强领域事实保真和细节充分性，而 B 组这类证据链任务已经比较适合当前 Runtime Lite 路线。

v3.2 按照创新方案中的 Output Contract Guard 路线补齐输出可靠性地基：

```text
Provider Response Guard:
  先规则恢复 provider response envelope，恢复不了才 retry。

Agent Output Contract Guard:
  提取 <CMJCC_CONTROL>，规则修复 JSON，schema 校验，默认值补齐，
  失败时短上下文 Same-Agent Format Retry，仍失败才 degraded_fallback。
```

v3.2 smoke 结果：

```text
A1 runtime_lite:
  contract_guard_checked_count: 5
  contract_schema_valid_count: 5
  schema_valid_rate: 1.0
  contract_retry_count: 0
  fallback_count: 0
```

这一步不是追求更高质量分，而是保证后续 v4 的 Retry Budget、Read Lease、GC 等机制建立在稳定输出契约之上。

v3.3 按照 TLC-Memory 创新方案补齐 Memory Candidate Admission Lite：

```text
Contract Guard control.memory_card / claim_cards
  -> MemoryCandidate / ClaimCandidate
  -> Admission Lite
  -> admitted candidates only
  -> MemoryStore / ClaimCard / MemoryView
```

A1 runtime_lite smoke 显示：

```text
memory_candidate_count: 3
memory_admitted_count: 3
memory_rejected_count: 0
memory_pending_count: 0
memory_audit_only_count: 0
admission_unresolved_slot_count: 0
claim_to_memoryview_count: 3
memory_admission_rate: 1.0
schema_valid_rate: 1.0
```

这一步确认 `memory_card` 不再被视为最终长期记忆，而是先进入候选池和准入门控；只有 admitted candidate 才更新 MemoryView。

v4.0 进入联合成本治理与生命周期 lite：

```text
CMJCC: Retry Budget
SHP-State: Read Lease-lite + GC-lite + tombstone
TLC-Memory: lifecycle-aware retrieval + Preflight Validation-lite
```

A1-A2 runtime_lite smoke 显示：

```text
task_runs: 2
success_rate: 1.0
memory_hit_rate: 1.0
preflight_validation_count: 2
read_lease_acquire_count: 20
summary_access_count: 14
evidence_snippet_access_count: 6
raw_access_count: 0
context_pruned_retry_count: 3
retry_input_tokens: 1393
retry_budget_exhausted_count: 0
```

这一步重点不是提高最终质量分，而是防止成本转移和运行时竞态：默认读取 Prompt View，不读 full raw；Writer 生成前做 read-set preflight；GC 不删除正在读取或已晋升为记忆 lineage 的状态。
