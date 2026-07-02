(function () {
  const POLL_MS = 2000;

  const emptyTask = () => ({
    id: "NO-DATA",
    question: "尚未发现运行数据。启动 agentlite autogen 或运行一次实验后，这里会自动刷新。",
    group: "monitor",
    mode: "runtime_lite",
    status: "waiting",
    progress: 0,
    agents: 0,
    duration: 0,
    tokens: 0,
    memoryHits: 0,
    startedAt: "--",
    alerts: 0,
    sourceKind: "none",
    sourceId: ""
  });

  const emptyMode = () => ({
    task: {},
    timeline: [],
    agents: [],
    messages: [],
    state_pool: [],
    memory_graph: { nodes: [], edges: [], fallback: false }
  });

  selectedTask = function () {
    return tasks.find((task) => task.id === appState.selectedTask) || tasks[0] || emptyTask();
  };

  trendChart = function () {
    const sample = tasks.slice(0, 7).reverse();
    if (!sample.length) {
      return `<svg class="trend-svg" viewBox="0 0 720 240" preserveAspectRatio="none"><text class="axis-label" x="360" y="128" text-anchor="middle">等待真实运行数据</text></svg>`;
    }
    const values = sample.map((task) => Math.max(1, Number(task.duration || task.agents || task.tokens || 1)));
    const max = Math.max(...values, 1);
    const points = values
      .map((value, index) => `${35 + index * 110},${205 - (value / max) * 150}`)
      .join(" ");
    return `<svg class="trend-svg" viewBox="0 0 720 240" preserveAspectRatio="none"><line class="grid-line" x1="35" y1="55" x2="695" y2="55"/><line class="grid-line" x1="35" y1="125" x2="695" y2="125"/><line class="grid-line" x1="35" y1="195" x2="695" y2="195"/><path class="trend-area" d="M ${points.replaceAll(" "," L ")} L695 215 L35 215 Z"/><polyline class="trend-line" points="${points}"/>${sample.map((task, index) => `<circle class="trend-point" cx="${35 + index * 110}" cy="${205 - (values[index] / max) * 150}" r="4"/><text class="axis-label" x="${35 + index * 110}" y="232" text-anchor="middle">${esc(shortLabel(task.startedAt))}</text>`).join("")}<text class="axis-label" x="8" y="59">${Math.round(max)}</text><text class="axis-label" x="8" y="129">${Math.round(max / 2)}</text><text class="axis-label" x="14" y="199">0</text></svg>`;
  };

  renderDashboard = function () {
    const total = tasks.length;
    const success = tasks.filter((task) => task.status === "success").length;
    const running = tasks.filter((task) => task.status === "running").length;
    const failed = tasks.filter((task) => task.status === "failed").length;
    const other = Math.max(0, total - success - running - failed);
    const durations = tasks.map((task) => Number(task.duration || 0)).filter(Boolean);
    const avg = durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : 0;
    const successRate = total ? `${(success / total * 100).toFixed(1)}%` : "--";
    const runtimeTokens = sumTokens("runtime_lite");
    const baselineTokens = sumTokens("baseline_text");
    const tokenFoot = baselineTokens && runtimeTokens
      ? `runtime ${fmtTokens(runtimeTokens)} / baseline ${fmtTokens(baselineTokens)}`
      : "等待对照数据";
    const tokenSaving = baselineTokens && runtimeTokens
      ? `${Math.max(0, (1 - runtimeTokens / baselineTokens) * 100).toFixed(1)}%`
      : "--";
    const memoryHits = tasks.reduce((sum, task) => sum + Number(task.memoryHits || 0), 0);
    const contractRate = contractPassRate();

    return `<section class="view"><div class="page-head"><div><h1 class="page-title">运行驾驶舱</h1><div class="page-desc">多 Agent 协作效率、可靠性与资源池健康度总览</div></div><div class="page-actions"><button class="secondary-btn" data-nav="tasks">查看全部任务</button><button class="primary-btn" data-action="new-experiment">＋ 新建实验</button></div></div>
      <div class="metrics-strip">
        ${metric("任务实例", total, appState.loading ? "正在刷新" : dataSourceFoot())}${metric("成功率", successRate, `${success} 成功 / ${total} 总计`, successRate === "--" ? "" : "metric-up")}${metric("Token 节省", tokenSaving, tokenFoot, tokenSaving === "--" ? "" : "metric-up")}${metric("平均延迟", avg ? `${avg.toFixed(1)}s` : "--", "按已完成任务计算")}${metric("记忆命中", memoryHits, `${memoryNodes.length} 个图谱节点`, memoryHits ? "metric-up" : "")}${metric("契约通过率", contractRate, "来自 contract_guard 事件")}
      </div>
      <div class="dashboard-grid">
        <div class="panel span-2"><div class="panel-head"><span class="panel-title">任务运行趋势</span><span class="panel-meta">最近 ${Math.min(total, 7)} 条真实记录</span></div><div class="panel-body">${trendChart()}</div></div>
        <div class="panel"><div class="panel-head"><span class="panel-title">任务状态</span><span class="panel-meta">共 ${total} 个</span></div><div class="panel-body"><div class="donut-row"><div class="donut"><div class="donut-center">${success}<small>成功</small></div></div><div class="legend"><div class="legend-item"><span class="legend-dot" style="background:var(--green)"></span>成功<strong>${success}</strong></div><div class="legend-item"><span class="legend-dot" style="background:var(--amber)"></span>运行中<strong>${running}</strong></div><div class="legend-item"><span class="legend-dot" style="background:var(--red)"></span>失败<strong>${failed}</strong></div><div class="legend-item"><span class="legend-dot" style="background:#9aa7b0"></span>其他<strong>${other}</strong></div></div></div></div></div>
        <div class="panel"><div class="panel-head"><span class="panel-title">Agent 调用排行</span><span class="panel-meta">调用量 / 状态</span></div><div class="panel-body"><div class="bar-list">${agents.length ? agents.map((agent) => `<div class="bar-row"><span>${esc(String(agent.role || agent.id).replace("Agent",""))}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, Number(agent.calls || 0) * 20)}%"></div></div><span>${agent.calls || 0}</span></div>`).join("") : `<div class="empty-hint">等待 Agent 轨迹</div>`}</div></div></div>
        <div class="panel"><div class="panel-head"><span class="panel-title">状态池占用</span><span class="panel-meta">${states.length} objects · ${formatBytes(totalStateBytes())}</span></div><div class="panel-body">${tierTrack()}<div class="legend">${stateTierItems()}</div></div></div>
        <div class="panel"><div class="panel-head"><span class="panel-title">最近告警</span><button class="ghost-btn" data-nav="alerts">全部告警</button></div><div class="dense-list">${alerts.length ? alerts.slice(0,4).map((alert) => denseAlert(alert)).join("") : `<div class="empty-hint">暂无异常事件</div>`}</div></div>
        <div class="panel span-2"><div class="panel-head"><span class="panel-title">最近任务</span><button class="ghost-btn" data-nav="tasks">任务列表</button></div><div class="table-wrap" style="border:0;border-radius:0"><table style="min-width:760px"><thead><tr><th>任务</th><th>模式</th><th>状态</th><th>耗时</th><th>Token</th><th>开始时间</th></tr></thead><tbody>${tasks.length ? tasks.slice(0,5).map((task) => `<tr data-task-id="${task.id}"><td><div class="question-cell truncate">${esc(task.question)}</div><div class="dense-sub mono">${task.id}</div></td><td><span class="mode-tag ${task.mode.startsWith("baseline") ? "baseline" : ""}">${task.mode}</span></td><td>${statusPill(task.status)}</td><td>${task.duration ? task.duration + "s" : "--"}</td><td>${fmtTokens(task.tokens)}</td><td>${task.startedAt}</td></tr>`).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--muted);height:100px">等待真实运行数据</td></tr>`}</tbody></table></div></div>
      </div></section>`;
  };

  renderWorkflow = function () {
    const task = selectedTask();
    const runtime = appState.workflowMode === "runtime_lite";
    const nodes = runtime ? runtimeNodes : baselineNodes;
    return `<section class="workflow-view"><div class="workflow-header"><div class="breadcrumb"><button data-nav="tasks">任务实例</button> / <span class="mono">${task.id}</span></div><div class="workflow-title-row"><div><div class="workflow-question">${esc(task.question)}</div><div class="workflow-meta"><span>状态 ${statusPill(task.status)}</span><span>耗时<strong>${task.duration || "--"}s</strong></span><span>Token<strong>${fmtTokens(task.tokens)}</strong></span><span>Agent<strong>${task.agents}</strong></span><span>记忆命中<strong>${task.memoryHits}</strong></span></div></div><div class="segment"><button data-mode="runtime_lite" class="${runtime ? "active" : ""}">runtime_lite</button><button data-mode="baseline_text" class="${!runtime ? "active" : ""}">baseline_text</button></div></div></div>
      <div class="workflow-shell"><aside class="workflow-aside"><div class="aside-title">Agent 执行链</div>${nodes.filter((node) => node.type === "agent").map((node) => `<button class="aside-agent" data-node-id="${node.id}"><span class="mini-status"></span><span><strong>${esc(node.label)}</strong><br><span class="dense-sub">${esc(node.metrics || "等待执行")}</span></span></button>`).join("") || `<div class="empty-hint">等待 Agent 轨迹</div>`}<div class="aside-title" style="margin-top:14px">资源池快照</div>${asideStateStats()}</aside>
      <div class="canvas-wrap"><div class="workflow-viewport" id="workflowViewport"><div class="workflow-stage" id="workflowStage"><svg class="edge-layer" id="wfEdges"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#6c98ab"/></marker></defs></svg>${nodes.map(renderWorkflowNode).join("")}</div></div><div class="canvas-controls"><button data-canvas="zoom-out" title="缩小">−</button><button data-canvas="zoom-in" title="放大">＋</button><button data-canvas="fit" title="适配画布">□</button><button data-canvas="reset" title="重置">↺</button></div><div class="canvas-legend"><span><i class="legend-shape" style="border-color:var(--teal);background:var(--teal-soft)"></i>Agent</span><span><i class="legend-shape" style="border-color:var(--blue);background:var(--blue-soft)"></i>Message</span><span><i class="legend-shape" style="border-color:var(--amber);background:var(--amber-soft)"></i>State</span><span><i class="legend-shape" style="border-color:var(--purple);background:var(--purple-soft)"></i>Memory</span></div><div class="minimap">${nodes.map((node) => `<i class="minimap-node ${node.type}" style="left:${node.x * .115}px;top:${node.y * .115}px;width:${node.type === "agent" ? 25 : 16}px;height:${node.type === "agent" ? 10 : 7}px"></i>`).join("")}</div></div></div></section>`;
  };

  renderStates = function () {
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">状态池</h1><div class="page-desc">非文本状态分层、访问策略与生命周期</div></div><div class="page-actions"><select class="filter-select" id="stateTier"><option value="all">全部层级</option><option>hot</option><option>warm</option><option>cold</option><option>tombstone</option></select><button class="secondary-btn">运行 GC 预检</button></div></div><div class="pool-summary">${poolSummary()}</div><div class="table-wrap" style="border-top:1px solid var(--line);border-radius:8px"><table><thead><tr><th>State ID</th><th>类型</th><th>层级</th><th>生命周期</th><th>来源 Agent</th><th>任务</th><th>大小</th><th>访问策略</th><th>读租约</th><th>Lineage</th></tr></thead><tbody id="stateRows">${states.map(stateRow).join("") || `<tr><td colspan="10" style="text-align:center;color:var(--muted);height:100px">暂无状态对象</td></tr>`}</tbody></table></div></section>`;
  };

  renderMemory = function () {
    const typeCounts = memoryTypeCounts();
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">记忆池</h1><div class="page-desc">声明、记忆视图与证据晋升关系</div></div><div class="page-actions"><select class="filter-select"><option>全部节点类型</option><option>MemoryView</option><option>ClaimCard</option><option>PromotionView</option></select><button class="secondary-btn" data-memory-fit>适配图谱</button></div></div><div class="memory-layout"><div class="panel memory-canvas" id="memoryCanvas"><div class="memory-stage" id="memoryStage"><svg class="memory-edge-layer" id="memoryEdges"></svg>${memoryNodes.length ? memoryNodes.map((node) => `<button class="memory-node ${node.type}" data-memory-id="${node.id}" style="left:${node.x}px;top:${node.y}px"><strong>${esc(node.label)}</strong><br><span class="dense-sub">${esc(node.summary)}</span></button>`).join("") : `<div class="empty-hint" style="padding:80px;text-align:center">暂无记忆图谱节点</div>`}</div></div><aside class="memory-side"><div class="panel"><div class="panel-head"><span class="panel-title">节点类型</span><span class="panel-meta">${memoryNodes.length} nodes</span></div><div class="type-list">${[["MemoryView","view"],["ClaimCard","claim"],["PromotionView","promotion"],["MemoryObject","object"],["Candidate","candidate"],["StateObject","stateobj"]].map(([label,type]) => `<div class="type-item"><i class="legend-shape memory-node ${type}" style="position:static;width:10px;min-height:10px;padding:0"></i>${label}<strong>${typeCounts[type] || 0}</strong></div>`).join("")}</div></div><div class="panel"><div class="panel-head"><span class="panel-title">记忆健康度</span></div><div class="panel-body"><div class="legend"><div class="legend-item">Active views<strong>${typeCounts.view || 0}</strong></div><div class="legend-item">Active claims<strong>${typeCounts.claim || 0}</strong></div><div class="legend-item">Pending candidates<strong>${typeCounts.candidate || 0}</strong></div><div class="legend-item">Graph edges<strong>${memoryEdges.length}</strong></div></div></div></div></aside></div></section>`;
  };

  renderAlerts = function () {
    const open = alerts.filter((alert) => alert.status !== "已关闭").length;
    const watching = alerts.filter((alert) => alert.status === "观察中").length;
    const closed = alerts.filter((alert) => alert.status === "已关闭").length;
    const high = alerts.filter((alert) => alert.severity === "error").length;
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">告警中心</h1><div class="page-desc">运行异常、契约治理与资源生命周期事件</div></div><button class="secondary-btn">告警规则</button></div><div class="metrics-strip" style="grid-template-columns:repeat(4,minmax(0,1fr))">${metric("未处理", open, "需要关注", open ? "metric-down" : "")}${metric("观察中", watching, "自动恢复")}${metric("今日已关闭", closed, "来自当前数据")}${metric("高优先级", high, "error 级别", high ? "metric-down" : "")}</div><div class="table-wrap" style="border-top:1px solid var(--line);border-radius:8px"><table><thead><tr><th>严重级别</th><th>告警</th><th>任务</th><th>来源</th><th>时间</th><th>状态</th></tr></thead><tbody>${alerts.length ? alerts.map((alert) => `<tr data-alert-id="${alert.id}"><td><span class="status-pill ${alert.severity === "error" ? "status-failed" : alert.severity === "warn" ? "status-running" : "status-waiting"}"><span class="status-dot"></span>${alert.severity.toUpperCase()}</span></td><td><strong>${esc(alert.title)}</strong><div class="dense-sub mono">${alert.id}</div></td><td class="mono">${alert.task}</td><td>${esc(alert.source)}</td><td>${esc(alert.time)}</td><td>${esc(alert.status)}</td></tr>`).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--muted);height:100px">暂无告警</td></tr>`}</tbody></table></div></section>`;
  };

  async function bootLiveData() {
    appState.loading = true;
    render();
    await refreshLiveData({ keepSelection: false });
    appState.loading = false;
    render();
    clearInterval(appState.pollTimer);
    appState.pollTimer = setInterval(() => {
      refreshLiveData({ keepSelection: true }).catch((error) => {
        appState.error = String(error.message || error);
        renderLiveError(error);
      });
    }, POLL_MS);
  }

  async function refreshLiveData({ keepSelection }) {
    const [sessionsResult, runsResult] = await Promise.allSettled([
      apiJson("/api/sessions"),
      apiJson("/api/runs")
    ]);
    const sessions = sessionsResult.status === "fulfilled" ? sessionsResult.value.sessions || [] : [];
    const runs = runsResult.status === "fulfilled" ? runsResult.value.runs || [] : [];
    tasks = [
      ...sessions.map(taskFromSession),
      ...runs.map(taskFromRun)
    ].sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0));

    const previousId = appState.selectedTask;
    const hasPrevious = keepSelection && tasks.some((task) => task.id === previousId);
    appState.selectedTask = hasPrevious ? previousId : (tasks[0]?.id || "");

    if (tasks.length) {
      const snapshot = await snapshotForTask(selectedTask());
      appState.snapshot = snapshot;
      applySnapshot(snapshot, selectedTask());
    } else {
      clearLiveData();
    }
    updateBadges();
    if (!appState.loading) {
      render();
    }
  }

  async function apiJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${url} ${response.status}`);
    }
    return response.json();
  }

  async function snapshotForTask(task) {
    if (!task || task.sourceKind === "none") {
      return null;
    }
    const prefix = task.sourceKind === "session" ? "sessions" : "runs";
    return apiJson(`/api/${prefix}/${encodeURIComponent(task.sourceId)}/snapshot`);
  }

  function applySnapshot(snapshot, task) {
    if (!snapshot) {
      clearLiveData();
      return;
    }
    const runtime = snapshot.modes?.runtime_lite || emptyMode();
    const baseline = snapshot.modes?.baseline_text || emptyMode();
    const runtimeSummary = modeSummary(snapshot, "runtime_lite");
    const baselineSummary = modeSummary(snapshot, "baseline_text");
    task.status = normalizeStatus(snapshot.status);
    task.agents = runtime.agents.length || baseline.agents.length || task.agents;
    task.duration = seconds(runtime.finished?.latency_ms || runtimeSummary.latency_ms || baseline.finished?.latency_ms || baselineSummary.latency_ms);
    task.tokens = runtimeSummary.end_to_end_collaboration_tokens || runtimeSummary.llm_total_tokens || runtimeSummary.prompt_tokens || task.tokens || 0;
    task.baselineTokens = baselineSummary.end_to_end_collaboration_tokens || baselineSummary.llm_total_tokens || baselineSummary.prompt_tokens || 0;
    task.memoryHits = runtimeSummary.useful_memory_hit_count || runtimeSummary.memory_hit_count || countRefs(runtime.messages, "memory_refs");
    task.progress = task.status === "success" ? 100 : task.status === "failed" ? Math.max(10, task.progress || 10) : Math.min(95, 20 + runtime.timeline.length * 4);
    task.alerts = (snapshot.errors || []).length;

    agents = mapAgents(runtime.agents.length ? runtime.agents : baseline.agents);
    states = mapStates(runtime.state_pool || [], task);
    const graph = runtime.memory_graph || { nodes: [], edges: [] };
    memoryNodes = layoutMemoryNodes(graph.nodes || []);
    memoryEdges = (graph.edges || []).map((edge) => [edge.source, edge.target, edge.label || "related"]);
    const runtimeFlow = buildWorkflow(runtime, task, "runtime_lite");
    const baselineFlow = buildWorkflow(baseline, task, "baseline_text");
    runtimeNodes = runtimeFlow.nodes;
    runtimeEdges = runtimeFlow.edges;
    baselineNodes = baselineFlow.nodes;
    baselineEdges = baselineFlow.edges;
    alerts = mapAlerts(snapshot, task);
  }

  function clearLiveData() {
    agents = [];
    states = [];
    alerts = [];
    memoryNodes = [];
    memoryEdges = [];
    const emptyFlow = buildWorkflow(emptyMode(), emptyTask(), "runtime_lite");
    runtimeNodes = emptyFlow.nodes;
    runtimeEdges = emptyFlow.edges;
    baselineNodes = emptyFlow.nodes;
    baselineEdges = emptyFlow.edges;
  }

  function taskFromSession(session) {
    const updatedAt = Number(session.updated_at || 0);
    const status = session.driver_status || (session.hooks_active ? "active" : "unknown");
    return {
      id: `session:${session.session_id}`,
      question: `AutoGen 接管会话：${session.framework || "unknown"} / ${session.driver || "driver"}`,
      group: "agentlite autogen",
      mode: "runtime_lite",
      status: normalizeStatus(status),
      progress: session.has_trace ? 80 : 20,
      agents: 0,
      duration: 0,
      tokens: 0,
      memoryHits: 0,
      startedAt: formatTimestamp(updatedAt),
      alerts: session.hooks_active ? 0 : 1,
      sourceKind: "session",
      sourceId: session.session_id,
      updatedAt
    };
  }

  function taskFromRun(run) {
    const updatedAt = Number(run.updated_at || 0);
    return {
      id: `run:${run.run_id}`,
      question: `实验输出：${run.run_id}`,
      group: "benchmark run",
      mode: "runtime_lite",
      status: normalizeStatus(run.status),
      progress: normalizeStatus(run.status) === "success" ? 100 : 60,
      agents: 0,
      duration: 0,
      tokens: 0,
      memoryHits: 0,
      startedAt: formatTimestamp(updatedAt),
      alerts: 0,
      sourceKind: "run",
      sourceId: run.run_id,
      updatedAt
    };
  }

  function mapAgents(items) {
    return (items || []).map((agent) => ({
      id: agent.agent_id || agent.id || "unknown",
      role: roleName(agent.agent_id || agent.id || "unknown"),
      status: agent.status === "running" ? "busy" : "online",
      calls: 1 + (agent.retries || []).length,
      p95: seconds(agent.latency_ms || 0),
      error: agent.contract_guard?.schema_valid === false ? 1 : 0,
      tokens: Number(agent.llm_total_tokens || agent.token_count || 0),
      memoryWrites: (agent.memory_refs || []).length,
      description: agent.output_summary || agent.received_summary || "来自真实运行轨迹",
      raw: agent
    }));
  }

  function mapStates(items, task) {
    return (items || []).map((state) => ({
      id: state.state_id || state.id || "state_unknown",
      type: state.state_type || state.type || "state",
      tier: state.tier || "hot",
      lifecycle: state.lifecycle || "active",
      agent: state.source_agent || state.agent || "--",
      task: state.task_id || task.sourceId || task.id,
      size: Number(state.size_bytes || state.size || 0),
      policy: state.access_policy || state.policy || "--",
      readers: Number(state.active_readers || state.access_count || 0),
      protected: Boolean(state.lineage_protected || state.protected || state.lifecycle === "active"),
      created: state.created_at || "--",
      summary: state.summary || state.fallback_summary || "",
      raw: state
    }));
  }

  function layoutMemoryNodes(items) {
    return (items || []).map((node, index) => ({
      id: node.id || node.label || `mem_${index}`,
      type: memoryType(node.type),
      label: node.label || node.id || `Memory ${index + 1}`,
      x: 60 + (index % 4) * 225,
      y: 70 + Math.floor(index / 4) * 125,
      status: node.status || "active",
      summary: node.summary || "",
      slot: node.slot || node.type || "",
      confidence: node.confidence ?? "",
      raw: node
    }));
  }

  function buildWorkflow(mode, task, modeName) {
    const agentsInMode = mode.agents || [];
    const messages = mode.messages || [];
    const nodes = [{
      id: `${modeName}_start`,
      type: "start",
      label: "User Question",
      summary: task.question,
      x: 555,
      y: 25,
      detail: { question: task.question, task_id: task.id, mode: modeName, source: task.sourceKind }
    }];
    const edges = [];
    let previous = nodes[0].id;
    const positions = [
      [590, 130], [315, 330], [590, 500], [900, 610], [260, 755],
      [650, 755], [1030, 755], [120, 610]
    ];

    if (!agentsInMode.length) {
      nodes.push({
        id: `${modeName}_waiting`,
        type: "final",
        label: "Waiting For Trace",
        summary: "尚未捕获该模式的 Agent 执行链",
        x: 590,
        y: 300,
        detail: { mode: modeName, task_id: task.id }
      });
      edges.push([previous, `${modeName}_waiting`]);
      return { nodes, edges };
    }

    agentsInMode.slice(0, 8).forEach((agent, index) => {
      const [x, y] = positions[index] || [590, 130 + index * 120];
      const agentId = `${modeName}_${agent.agent_id || index}`;
      const message = messages[index] || {};
      if (index > 0 || messages.length) {
        const payloadId = `${modeName}_payload_${index}`;
        nodes.push({
          id: payloadId,
          type: "payload",
          label: "Handoff Message",
          summary: message.summary || message.content || `${messages[index - 1]?.sender || "agent"} -> ${agent.agent_id || "agent"}`,
          x: Math.min(1000, x + 35),
          y: Math.max(235, y - 105),
          detail: payloadDetailFrom(message, modeName)
        });
        edges.push([previous, payloadId]);
        previous = payloadId;
      }
      nodes.push({
        id: agentId,
        type: "agent",
        label: roleName(agent.agent_id || "agent"),
        summary: agent.output_summary || agent.received_summary || "真实运行节点",
        x,
        y,
        status: agent.status === "running" ? "running" : "success",
        metrics: agentMetrics(agent),
        detail: agentDetailFrom(agent, mode, modeName)
      });
      edges.push([previous, agentId]);
      previous = agentId;
    });

    (mode.state_pool || []).slice(0, 2).forEach((state, index) => {
      const stateId = `${modeName}_state_${index}`;
      nodes.push({
        id: stateId,
        type: "state",
        label: "StateRef",
        summary: `${state.state_type || "state"} · ${state.tier || "hot"}`,
        x: 900 - index * 585,
        y: 375 + index * 250,
        detail: state
      });
      edges.push([state.source_agent ? `${modeName}_${state.source_agent}` : previous, stateId]);
    });

    nodes.push({
      id: `${modeName}_final`,
      type: "final",
      label: "Final Deliverable",
      summary: mode.finished?.success === false ? "运行未成功完成" : "已根据真实轨迹生成最终节点",
      x: 650,
      y: 820,
      detail: { status: mode.finished?.success === false ? "failed" : "delivered", mode: modeName, task_id: task.id, finished: mode.finished || {} }
    });
    edges.push([previous, `${modeName}_final`]);
    return { nodes, edges };
  }

  function mapAlerts(snapshot, task) {
    const errors = (snapshot.errors || []).filter(Boolean).map((error, index) => ({
      id: `ERR-${index + 1}-${task.sourceId}`,
      severity: "error",
      title: "运行错误",
      task: task.id,
      source: "monitor_api",
      time: "刚刚",
      status: "未处理",
      detail: error
    }));
    const runtime = snapshot.modes?.runtime_lite || emptyMode();
    const contractWarnings = (runtime.agents || [])
      .filter((agent) => agent.contract_guard?.schema_valid === false)
      .map((agent, index) => ({
        id: `CG-${index + 1}-${task.sourceId}`,
        severity: "warn",
        title: `${roleName(agent.agent_id)} 契约校验未通过`,
        task: task.id,
        source: "contract_guard",
        time: "当前快照",
        status: "观察中",
        detail: JSON.stringify(agent.contract_guard || {}, null, 2)
      }));
    return [...errors, ...contractWarnings];
  }

  function agentDetailFrom(agent, mode, modeName) {
    const relevantLogs = (mode.timeline || [])
      .filter((event) => !event.agent_id || event.agent_id === agent.agent_id)
      .slice(-8)
      .map((event) => ({ time: formatIsoTime(event.ts), level: "INFO", text: `${event.event_type}: ${event.summary || ""}` }));
    return {
      role: roleName(agent.agent_id || "agent"),
      input: agent.received_summary || "",
      output: agent.output_summary || agent.output_content || "",
      contract: agent.contract_guard?.contract_status || "not_checked",
      status: agent.status || "done",
      mode: modeName,
      state_refs: (agent.state_refs || []).length,
      memory_refs: (agent.memory_refs || []).length,
      logs: relevantLogs
    };
  }

  function payloadDetailFrom(message, modeName) {
    return {
      from: message.sender || "--",
      to: message.receiver || message.handoff_to || "--",
      summary: message.summary || message.content || "",
      refs: [...(message.state_refs || []), ...(message.memory_refs || [])],
      cost: message.cost_report || {},
      payload: { mode: modeName, event_type: message.event_type || "message_sent", ...message }
    };
  }

  function modeSummary(snapshot, mode) {
    return snapshot.summary?.by_mode?.[mode] || {};
  }

  function sumTokens(mode) {
    return tasks.reduce((sum, task) => {
      if (mode === "baseline_text") return sum + Number(task.baselineTokens || 0);
      return sum + Number(task.tokens || 0);
    }, 0);
  }

  function contractPassRate() {
    const summary = appState.snapshot?.summary?.by_mode?.runtime_lite || {};
    const checked = Number(summary.contract_guard_checked_count || 0);
    const valid = Number(summary.contract_schema_valid_count || 0);
    if (!checked) return "--";
    return `${(valid / checked * 100).toFixed(1)}%`;
  }

  function countRefs(messages, field) {
    return (messages || []).reduce((sum, message) => sum + ((message[field] || []).length), 0);
  }

  function stateTierCounts() {
    return states.reduce((acc, state) => {
      const tier = state.lifecycle === "deleted" || state.lifecycle === "tombstoned" ? "tombstone" : (state.tier || "hot");
      acc[tier] = (acc[tier] || 0) + 1;
      acc.bytes[tier] = (acc.bytes[tier] || 0) + Number(state.size || 0);
      return acc;
    }, { hot: 0, warm: 0, cold: 0, tombstone: 0, bytes: {} });
  }

  function totalStateBytes() {
    return states.reduce((sum, state) => sum + Number(state.size || 0), 0);
  }

  function poolSummary() {
    const counts = stateTierCounts();
    return [
      poolTier("Hot", counts.hot || 0, formatBytes(counts.bytes.hot || 0), "var(--red)"),
      poolTier("Warm", counts.warm || 0, formatBytes(counts.bytes.warm || 0), "var(--amber)"),
      poolTier("Cold", counts.cold || 0, formatBytes(counts.bytes.cold || 0), "var(--blue)"),
      poolTier("Tombstone", counts.tombstone || 0, "仅元数据", "#8996a0")
    ].join("");
  }

  function tierTrack() {
    const counts = stateTierCounts();
    const total = Math.max(1, states.length);
    return `<div class="tier-track"><span class="tier-hot" style="width:${counts.hot / total * 100}%"></span><span class="tier-warm" style="width:${counts.warm / total * 100}%"></span><span class="tier-cold" style="width:${counts.cold / total * 100}%"></span><span class="tier-tomb" style="width:${counts.tombstone / total * 100}%"></span></div>`;
  }

  function stateTierItems() {
    const counts = stateTierCounts();
    return `<div class="legend-item"><span class="legend-dot tier-hot"></span>Hot<strong>${counts.hot || 0} · ${formatBytes(counts.bytes.hot || 0)}</strong></div><div class="legend-item"><span class="legend-dot tier-warm"></span>Warm<strong>${counts.warm || 0} · ${formatBytes(counts.bytes.warm || 0)}</strong></div><div class="legend-item"><span class="legend-dot tier-cold"></span>Cold<strong>${counts.cold || 0} · ${formatBytes(counts.bytes.cold || 0)}</strong></div><div class="legend-item"><span class="legend-dot tier-tomb"></span>Tombstone<strong>${counts.tombstone || 0}</strong></div>`;
  }

  function asideStateStats() {
    const counts = stateTierCounts();
    return `<div class="aside-stat"><span>Hot State</span><strong>${counts.hot || 0}</strong></div><div class="aside-stat"><span>Cold State</span><strong>${counts.cold || 0}</strong></div><div class="aside-stat"><span>Memory View</span><strong>${memoryNodes.filter((node) => node.type === "view").length}</strong></div><div class="aside-stat"><span>Active Claim</span><strong>${memoryNodes.filter((node) => node.type === "claim").length}</strong></div>`;
  }

  function memoryTypeCounts() {
    return memoryNodes.reduce((acc, node) => {
      acc[node.type] = (acc[node.type] || 0) + 1;
      return acc;
    }, {});
  }

  function memoryType(type) {
    return {
      MemoryView: "view",
      ClaimCard: "claim",
      PromotionView: "promotion",
      MemoryObject: "object",
      MemoryCandidate: "candidate",
      StateObject: "stateobj"
    }[type] || "object";
  }

  function normalizeStatus(status) {
    const value = String(status || "").toLowerCase();
    if (["success", "succeeded", "complete", "completed", "done"].includes(value)) return "success";
    if (["running", "active", "started"].includes(value)) return "running";
    if (["failed", "error"].includes(value)) return "failed";
    if (["stopped", "cancelled", "canceled"].includes(value)) return "stopped";
    return "waiting";
  }

  function roleName(agentId) {
    if (!agentId) return "Agent";
    if (String(agentId).endsWith("Agent")) return String(agentId);
    return `${String(agentId).replace(/(^|_)([a-z])/g, (_, p, c) => `${p ? " " : ""}${c.toUpperCase()}`).replace(/\s+/g, "")}Agent`;
  }

  function agentMetrics(agent) {
    const chars = Number(agent.prompt_chars || 0) + Number(agent.output_chars || 0);
    const refs = (agent.state_refs || []).length + (agent.memory_refs || []).length;
    return `${chars} chars · ${refs} refs`;
  }

  function seconds(ms) {
    const value = Number(ms || 0);
    return value ? Number((value / 1000).toFixed(1)) : 0;
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "0 B";
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${value} B`;
  }

  function formatTimestamp(epochSeconds) {
    if (!epochSeconds) return "--";
    return new Date(epochSeconds * 1000).toLocaleString("zh-CN", { hour12: false });
  }

  function formatIsoTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString("zh-CN", { hour12: false });
  }

  function shortLabel(value) {
    const text = String(value || "--");
    return text.length > 8 ? text.slice(5, 10) : text;
  }

  function dataSourceFoot() {
    const sessions = tasks.filter((task) => task.sourceKind === "session").length;
    const runs = tasks.filter((task) => task.sourceKind === "run").length;
    return `${sessions} 会话 / ${runs} 实验`;
  }

  function updateBadges() {
    setText("#taskBadge", String(tasks.length));
    setText('[data-nav="agents"] .nav-badge', String(agents.length));
    setText('[data-nav="states"] .nav-badge', String(states.length));
    setText('[data-nav="memory"] .nav-badge', String(memoryNodes.length));
    setText('[data-nav="alerts"] .nav-badge', String(alerts.length));
    const footer = document.querySelector(".sidebar-footer .system-health div");
    if (footer) {
      footer.innerHTML = `<strong>${appState.error ? "Runtime Warning" : "Runtime Healthy"}</strong><br><span>${agents.length} agents · ${states.length} states · live</span>`;
    }
  }

  function setText(selector, value) {
    const el = document.querySelector(selector);
    if (el) el.textContent = value;
  }

  function renderLiveError(error) {
    alerts = [{
      id: "MONITOR-API-ERROR",
      severity: "error",
      title: "监控接口刷新失败",
      task: selectedTask().id,
      source: "web_monitor",
      time: "刚刚",
      status: "观察中",
      detail: String(error.message || error)
    }, ...alerts.filter((alert) => alert.id !== "MONITOR-API-ERROR")];
    updateBadges();
    render();
  }

  const form = document.getElementById("experimentForm");
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const payload = {
        title: document.getElementById("newGroup")?.value || "monitor",
        prompt: document.getElementById("newQuestion")?.value || "",
        rounds: Number(document.getElementById("newRounds")?.value || 1),
        engine: "deterministic"
      };
      try {
        const result = await apiJsonPost("/api/experiments", payload);
        closeModal();
        showToast(`实验 ${result.run_id || result.experiment_id} 已提交`);
        await refreshLiveData({ keepSelection: false });
        setView("tasks");
      } catch (error) {
        showToast(`创建失败：${error.message || error}`);
      }
    }, true);
  }

  async function apiJsonPost(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store"
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `${url} ${response.status}`);
    }
    return data;
  }

  window.AgentLiteLiveBoot = bootLiveData;
  bootLiveData().catch((error) => {
    appState.loading = false;
    appState.error = String(error.message || error);
    renderLiveError(error);
  });
})();
