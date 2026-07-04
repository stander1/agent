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
    baselineTokens: 0,
    costBreakdown: {},
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

  const SHOW_BASELINE_TOGGLE = new URLSearchParams(window.location.search).get("debugBaseline") === "1";
  const CANONICAL_AGENT_ORDER = [
    "planner",
    "retriever",
    "writer",
    "reviewer",
    "memory_manager",
    "team",
    "team_manager",
    "runtime_bridge",
    "autogen_driver"
  ];
  const CAPABILITY_PROFILES = {
    planner: {
      role: "PlannerAgent",
      summary: "任务拆解、路线规划、协作入口判断",
      capabilities: ["任务拆解", "路由种子", "目标约束整理"],
      accepted: ["agent_output", "route_decision"]
    },
    retriever: {
      role: "RetrieverAgent",
      summary: "检索状态、embedding 状态与证据排序",
      capabilities: ["检索", "embedding 生成", "证据排序"],
      accepted: ["artifact_state", "memory_ref_handoff"]
    },
    writer: {
      role: "WriterAgent",
      summary: "综合生成、Prompt View 使用、最终草案输出",
      capabilities: ["综合生成", "记忆使用", "交付草案"],
      accepted: ["retrieval_state", "embedding_state", "artifact_state"]
    },
    reviewer: {
      role: "ReviewerAgent",
      summary: "结果校验、schema 守卫、失败状态审查",
      capabilities: ["质量校验", "schema 审查", "失败复核"],
      accepted: ["artifact_state", "failure_state", "final_deliverable"]
    },
    memory_manager: {
      role: "MemoryManagerAgent",
      summary: "记忆候选治理、声明压缩、晋升判断",
      capabilities: ["记忆治理", "Claim 压缩", "候选入池"],
      accepted: ["artifact_state", "retrieval_state"]
    },
    team: {
      role: "AutoGenTeam",
      summary: "AutoGen Team 入口，负责组级任务广播",
      capabilities: ["Team 调度", "广播入口", "任务流转"],
      accepted: ["user_task", "team_input"]
    },
    team_manager: {
      role: "AutoGenTeamManager",
      summary: "AutoGen 组管理器，维护回合与参与者顺序",
      capabilities: ["回合管理", "参与者编排"],
      accepted: ["team_output", "handoff_message"]
    },
    runtime_bridge: {
      role: "AutoGenRuntimeBridge",
      summary: "AutoGen Core 运行时桥接层，承接底层消息投递",
      capabilities: ["Core 桥接", "消息投递", "水合还原"],
      accepted: ["core_request", "core_response"]
    },
    autogen_driver: {
      role: "AgentLiteDriver",
      summary: "AgentLite 注入驱动，记录 hook、改写与 trace",
      capabilities: ["运行时注入", "trace 记录", "协议改写"],
      accepted: ["driver_event"]
    }
  };

  let tasks = [];
  let agents = [];
  let states = [];
  let alerts = [];
  let memoryNodes = [];
  let memoryEdges = [];
  let runtimeNodes = [];
  let runtimeEdges = [];
  let baselineNodes = [];
  let baselineEdges = [];
  let render;
  let setView;
  let selectedTask;
  let trendChart;
  let renderDashboard;
  let taskRow;
  let renderWorkflow;
  let renderWorkflowList;
  let renderAgents;
  let renderStates;
  let renderMemory;
  let renderAlerts;
  let workflowSet;
  let drawWorkflowEdges;
  let fitWorkflow;
  let initWorkflowPan;
  let drawMemoryEdges;
  let fitMemory;
  let detailGrid;

  const root = document.getElementById("viewRoot");
  const statusText = {
    success: "成功",
    running: "运行中",
    failed: "失败",
    waiting: "等待",
    stopped: "已停止"
  };
  const appState = {
    view: "dashboard",
    selectedTask: "",
    workflowMode: "runtime_lite",
    taskQuery: "",
    taskStatus: "all",
    taskMode: "all",
    sortKey: "startedAt",
    sortDir: "desc",
    zoom: 0.82,
    panX: 80,
    panY: 10,
    drawer: null,
    drawerTab: "overview",
    snapshot: null,
    pollTimer: 0,
    loading: false,
    error: "",
    memoryTypeFilter: "all"
  };

  render = function () {
    if (!root) return;
    if (appState.view === "dashboard") root.innerHTML = renderDashboard();
    else if (appState.view === "tasks") root.innerHTML = renderTasks();
    else if (appState.view === "workflowList") root.innerHTML = renderWorkflowList();
    else if (appState.view === "workflow") root.innerHTML = renderWorkflow();
    else if (appState.view === "agents") root.innerHTML = renderAgents();
    else if (appState.view === "states") root.innerHTML = renderStates();
    else if (appState.view === "memory") root.innerHTML = renderMemory();
    else if (appState.view === "alerts") root.innerHTML = renderAlerts();
    if (appState.view === "workflow") {
      requestAnimationFrame(() => {
        drawWorkflowEdges();
        applyWorkflowTransform();
        initWorkflowPan();
      });
    }
    if (appState.view === "memory") {
      requestAnimationFrame(() => {
        drawMemoryEdges();
        fitMemory();
      });
    }
  };

  setView = function (view) {
    appState.view = view;
    document.querySelectorAll("[data-nav]").forEach((button) => {
      button.classList.toggle(
        "active",
        button.dataset.nav === view || (view === "workflow" && button.dataset.nav === "workflowList")
      );
    });
    document.getElementById("sidebar")?.classList.remove("mobile-open");
    render();
  };

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
      ? `AgentLite ${fmtTokens(runtimeTokens)} / 参考 ${fmtTokens(baselineTokens)}`
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
        <div class="panel"><div class="panel-head"><span class="panel-title">Token 成本拆解</span><span class="panel-meta">${esc(selectedTask().id)}</span></div><div class="panel-body">${tokenCostPanel(selectedTask())}</div></div>
        <div class="panel"><div class="panel-head"><span class="panel-title">最近告警</span><button class="ghost-btn" data-nav="alerts">全部告警</button></div><div class="dense-list">${alerts.length ? alerts.slice(0,4).map((alert) => denseAlert(alert)).join("") : `<div class="empty-hint">暂无异常事件</div>`}</div></div>
        <div class="panel span-2"><div class="panel-head"><span class="panel-title">最近任务</span><button class="ghost-btn" data-nav="tasks">任务列表</button></div><div class="table-wrap" style="border:0;border-radius:0"><table style="min-width:760px"><thead><tr><th>任务</th><th>模式</th><th>状态</th><th>耗时</th><th>Token</th><th>开始时间</th></tr></thead><tbody>${tasks.length ? tasks.slice(0,5).map((task) => `<tr data-task-id="${task.id}"><td><div class="question-cell truncate">${esc(task.question)}</div><div class="dense-sub mono">${task.id}</div></td><td><span class="mode-tag ${task.mode.startsWith("baseline") ? "baseline" : ""}">${task.mode}</span></td><td>${statusPill(task.status)}</td><td>${task.duration ? task.duration + "s" : "--"}</td><td>${tokenCell(task)}</td><td>${task.startedAt}</td></tr>`).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--muted);height:100px">等待真实运行数据</td></tr>`}</tbody></table></div></div>
      </div></section>`;
  };

  taskRow = function (task) {
    return `<tr data-task-id="${task.id}"><td class="mono">${task.id}</td><td><div class="question-cell truncate" title="${esc(task.question)}">${esc(task.question)}</div><div class="dense-sub">${task.group}</div></td><td><span class="mode-tag ${task.mode.startsWith("baseline") ? "baseline" : ""}">${task.mode}</span></td><td>${statusPill(task.status)}</td><td><div class="progress"><span style="width:${task.progress}%"></span></div><div class="dense-sub">${task.progress}%</div></td><td>${task.agents}</td><td>${task.duration ? task.duration + "s" : "--"}</td><td>${tokenCell(task)}</td><td>${task.memoryHits}</td><td>${task.startedAt}</td></tr>`;
  };

  function renderTasks() {
    const filtered = getFilteredTasks();
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">任务实例</h1><div class="page-desc">真实 AutoGen 接管会话与实验运行记录</div></div><button class="primary-btn" data-action="new-experiment">＋ 新建实验</button></div>
      <div class="toolbar"><div class="toolbar-group"><input class="field" id="taskSearch" value="${esc(appState.taskQuery)}" placeholder="搜索问题、任务 ID 或 Agent..." style="width:320px"><select class="filter-select" id="taskStatus"><option value="all">全部状态</option>${Object.entries(statusText).map(([key, label]) => `<option value="${key}" ${appState.taskStatus === key ? "selected" : ""}>${label}</option>`).join("")}</select><select class="filter-select" id="taskMode"><option value="all">全部模式</option><option value="runtime_lite" ${appState.taskMode === "runtime_lite" ? "selected" : ""}>runtime_lite</option><option value="baseline_text" ${appState.taskMode === "baseline_text" ? "selected" : ""}>baseline_text</option></select></div><span class="muted" id="taskSummary">${filtered.length} / ${tasks.length} 条</span></div>
      <div class="table-wrap"><table style="min-width:1080px"><thead><tr><th data-sort="id">任务 ID</th><th>问题</th><th>模式</th><th data-sort="status">状态</th><th>进度</th><th>Agent</th><th data-sort="duration">耗时</th><th>Token 成本</th><th>记忆命中</th><th data-sort="startedAt">开始时间</th></tr></thead><tbody id="taskRows">${filtered.map(taskRow).join("") || `<tr><td colspan="10" style="text-align:center;color:var(--muted);height:110px">暂无匹配任务</td></tr>`}</tbody></table></div></section>`;
  }

  function getFilteredTasks() {
    const query = appState.taskQuery.trim().toLowerCase();
    const filtered = tasks.filter((task) => {
      const text = `${task.id} ${task.question} ${task.group}`.toLowerCase();
      const matchesQuery = !query || text.includes(query);
      const matchesStatus = appState.taskStatus === "all" || task.status === appState.taskStatus;
      const matchesMode = appState.taskMode === "all" || task.mode === appState.taskMode;
      return matchesQuery && matchesStatus && matchesMode;
    });
    return filtered.sort((left, right) => {
      const key = appState.sortKey;
      const leftValue = left[key] ?? "";
      const rightValue = right[key] ?? "";
      const diff = typeof leftValue === "number" || typeof rightValue === "number"
        ? Number(leftValue || 0) - Number(rightValue || 0)
        : String(leftValue).localeCompare(String(rightValue), "zh-CN");
      return appState.sortDir === "asc" ? diff : -diff;
    });
  }

  function updateTaskTable() {
    const tbody = document.getElementById("taskRows");
    const summary = document.getElementById("taskSummary");
    const filtered = getFilteredTasks();
    if (tbody) {
      tbody.innerHTML = filtered.map(taskRow).join("") || `<tr><td colspan="10" style="text-align:center;color:var(--muted);height:110px">暂无匹配任务</td></tr>`;
    }
    if (summary) {
      summary.textContent = `${filtered.length} / ${tasks.length} 条`;
    }
  }

  renderWorkflow = function () {
    const task = selectedTask();
    const runtime = appState.workflowMode === "runtime_lite";
    const nodes = runtime ? runtimeNodes : baselineNodes;
    const modeSwitch = SHOW_BASELINE_TOGGLE
      ? `<div class="segment"><button data-mode="runtime_lite" class="${runtime ? "active" : ""}">AgentLite 接管</button><button data-mode="baseline_text" class="${!runtime ? "active" : ""}">实验基线</button></div>`
      : `<span class="mode-tag">AgentLite 接管视图</span>`;
    return `<section class="workflow-view"><div class="workflow-header"><div class="breadcrumb"><button data-nav="workflowList">工作流任务</button> / <span class="mono">${task.id}</span></div><div class="workflow-title-row"><div><div class="workflow-question">${esc(task.question)}</div><div class="workflow-meta"><span>状态 ${statusPill(task.status)}</span><span>耗时<strong>${task.duration || "--"}s</strong></span><span>Agent<strong>${task.agents}</strong></span><span>记忆命中<strong>${task.memoryHits}</strong></span></div>${workflowTokenStrip(task)}</div>${modeSwitch}</div></div>
      <div class="workflow-shell"><aside class="workflow-aside"><div class="aside-title">Agent 执行链</div>${nodes.filter((node) => node.type === "agent").map((node) => `<button class="aside-agent" data-node-id="${node.id}"><span class="mini-status"></span><span><strong>${esc(node.label)}</strong><br><span class="dense-sub">${esc(node.metrics || "等待执行")}</span></span></button>`).join("") || `<div class="empty-hint">等待 Agent 轨迹</div>`}<div class="aside-title" style="margin-top:14px">Token 成本</div>${tokenCostAside(task)}<div class="aside-title" style="margin-top:14px">资源池快照</div>${asideStateStats()}</aside>
      <div class="canvas-wrap"><div class="workflow-viewport" id="workflowViewport"><div class="workflow-stage" id="workflowStage"><svg class="edge-layer" id="wfEdges"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#6c98ab"/></marker></defs></svg>${nodes.map(renderWorkflowNode).join("")}</div></div><div class="canvas-controls"><button data-canvas="zoom-out" title="缩小">−</button><button data-canvas="zoom-in" title="放大">＋</button><button data-canvas="fit" title="适配画布">□</button><button data-canvas="reset" title="重置">↺</button></div><div class="canvas-legend"><span><i class="legend-shape" style="border-color:var(--teal);background:var(--teal-soft)"></i>Agent</span><span><i class="legend-shape" style="border-color:var(--blue);background:var(--blue-soft)"></i>Message</span><span><i class="legend-shape" style="border-color:var(--amber);background:var(--amber-soft)"></i>State</span><span><i class="legend-shape" style="border-color:var(--purple);background:var(--purple-soft)"></i>Memory</span></div><div class="minimap">${nodes.map((node) => `<i class="minimap-node ${node.type}" style="left:${node.x * .115}px;top:${node.y * .115}px;width:${node.type === "agent" ? 25 : 16}px;height:${node.type === "agent" ? 10 : 7}px"></i>`).join("")}</div></div></div></section>`;
  };

  renderWorkflowList = function () {
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">工作流观测</h1><div class="page-desc">先选择一个真实任务，再进入可拖拽工作流画布</div></div><button class="secondary-btn" data-nav="tasks">任务实例表</button></div>
      <div class="toolbar"><div class="toolbar-group"><input class="field" id="taskSearch" value="${esc(appState.taskQuery)}" placeholder="搜索任务 ID、问题或 Agent..." style="width:320px"><select class="filter-select" id="taskStatus"><option value="all">全部状态</option>${Object.entries(statusText).map(([key, label]) => `<option value="${key}" ${appState.taskStatus === key ? "selected" : ""}>${label}</option>`).join("")}</select></div><span class="muted">${tasks.length} 个可观测任务</span></div>
      <div class="table-wrap"><table style="min-width:880px"><thead><tr><th>任务</th><th>状态</th><th>Agent</th><th>Token 成本</th><th>资源池</th><th>开始时间</th></tr></thead><tbody>${getFilteredTasks().map((task) => `<tr data-task-id="${task.id}"><td><div class="question-cell truncate" title="${esc(task.question)}">${esc(task.question)}</div><div class="dense-sub mono">${esc(task.id)}</div></td><td>${statusPill(task.status)}</td><td>${task.agents}</td><td>${tokenCell(task)}</td><td><span class="dense-sub">${states.length} states / ${memoryNodes.length} memory nodes</span></td><td>${esc(task.startedAt)}</td></tr>`).join("") || `<tr><td colspan="6" style="text-align:center;color:var(--muted);height:110px">暂无可观测任务</td></tr>`}</tbody></table></div></section>`;
  };

  renderAgents = function () {
    const rows = agents.length
      ? agents.map((agent) => {
        const caps = (agent.capabilities || []).slice(0, 4).map((item) => `<span class="mode-tag">${esc(item)}</span>`).join(" ");
        return `<tr data-agent-id="${agent.id}"><td><strong>${esc(agent.displayName || agent.id)}</strong><div class="dense-sub mono">${esc(agent.id)}</div></td><td><strong>${esc(agent.role)}</strong><div class="dense-sub">${esc(agent.description)}</div></td><td>${statusPill(agent.status === "busy" ? "running" : "success")}</td><td>${caps || `<span class="dense-sub">暂无能力画像</span>`}</td><td>${(agent.accepted || []).join("<br>") || "--"}</td><td>${agent.calls}</td><td>${fmtTokens(agent.tokens)}</td><td>${agent.memoryWrites}</td></tr>`;
      }).join("")
      : `<tr><td colspan="8" style="text-align:center;color:var(--muted);height:100px">等待 Agent 轨迹</td></tr>`;
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">Agent 注册表</h1><div class="page-desc">按能力画像聚合展示，隐藏 AutoGen 内部 UUID 派生对象</div></div><button class="secondary-btn">导出指标</button></div><div class="table-wrap" style="border-top:1px solid var(--line);border-radius:8px"><table style="min-width:980px"><thead><tr><th>Agent</th><th>角色说明</th><th>状态</th><th>能力画像</th><th>可接收状态 / 消息</th><th>调用量</th><th>Token</th><th>记忆写入</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  };

  renderStates = function () {
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">状态池</h1><div class="page-desc">非文本状态分层、访问策略与生命周期</div></div><div class="page-actions"><select class="filter-select" id="stateTier"><option value="all">全部层级</option><option>hot</option><option>warm</option><option>cold</option><option>tombstone</option></select><button class="secondary-btn">运行 GC 预检</button></div></div><div class="pool-summary">${poolSummary()}</div><div class="table-wrap" style="border-top:1px solid var(--line);border-radius:8px"><table><thead><tr><th>State ID</th><th>类型</th><th>层级</th><th>生命周期</th><th>来源 Agent</th><th>任务</th><th>大小</th><th>访问策略</th><th>读租约</th><th>Lineage</th></tr></thead><tbody id="stateRows">${states.map(stateRow).join("") || `<tr><td colspan="10" style="text-align:center;color:var(--muted);height:100px">暂无状态对象</td></tr>`}</tbody></table></div></section>`;
  };

  renderMemory = function () {
    const typeCounts = memoryTypeCounts();
    const visibleNodes = filteredMemoryNodes();
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">记忆池</h1><div class="page-desc">声明、记忆视图与证据晋升关系</div></div><div class="page-actions"><select class="filter-select" id="memoryTypeFilter"><option value="all">全部节点类型</option><option value="view" ${appState.memoryTypeFilter === "view" ? "selected" : ""}>MemoryView</option><option value="claim" ${appState.memoryTypeFilter === "claim" ? "selected" : ""}>ClaimCard</option><option value="promotion" ${appState.memoryTypeFilter === "promotion" ? "selected" : ""}>PromotionView</option><option value="object" ${appState.memoryTypeFilter === "object" ? "selected" : ""}>MemoryObject</option><option value="candidate" ${appState.memoryTypeFilter === "candidate" ? "selected" : ""}>Candidate</option><option value="stateobj" ${appState.memoryTypeFilter === "stateobj" ? "selected" : ""}>StateObject</option></select><button class="secondary-btn" data-memory-fit>适配图谱</button></div></div><div class="memory-layout"><div class="panel memory-canvas" id="memoryCanvas"><div class="memory-stage" id="memoryStage"><svg class="memory-edge-layer" id="memoryEdges"></svg>${visibleNodes.length ? visibleNodes.map((node) => `<button class="memory-node ${node.type}" data-memory-id="${node.id}" style="left:${node.x}px;top:${node.y}px"><strong title="${esc(node.id)}">${esc(node.label)}</strong><span class="dense-sub">${esc(shortText(node.summary, 86))}</span></button>`).join("") : `<div class="empty-hint" style="padding:80px;text-align:center">暂无该类型图谱节点</div>`}</div></div><aside class="memory-side"><div class="panel"><div class="panel-head"><span class="panel-title">节点类型</span><span class="panel-meta">${memoryNodes.length} nodes</span></div><div class="type-list">${[["MemoryView","view"],["ClaimCard","claim"],["PromotionView","promotion"],["MemoryObject","object"],["Candidate","candidate"],["StateObject","stateobj"]].map(([label,type]) => `<button class="type-item" data-memory-filter="${type}" style="width:100%;border:0;background:transparent;text-align:left"><i class="legend-shape memory-node ${type}" style="position:static;width:10px;min-height:10px;padding:0"></i>${label}<strong>${typeCounts[type] || 0}</strong></button>`).join("")}</div></div><div class="panel"><div class="panel-head"><span class="panel-title">记忆健康度</span></div><div class="panel-body"><div class="legend"><div class="legend-item">Active views<strong>${typeCounts.view || 0}</strong></div><div class="legend-item">Active claims<strong>${typeCounts.claim || 0}</strong></div><div class="legend-item">Pending candidates<strong>${typeCounts.candidate || 0}</strong></div><div class="legend-item">Graph edges<strong>${memoryEdges.length}</strong></div></div></div></div></aside></div></section>`;
  };

  renderAlerts = function () {
    const open = alerts.filter((alert) => alert.status !== "已关闭").length;
    const watching = alerts.filter((alert) => alert.status === "观察中").length;
    const closed = alerts.filter((alert) => alert.status === "已关闭").length;
    const high = alerts.filter((alert) => alert.severity === "error").length;
    return `<section class="view"><div class="page-head"><div><h1 class="page-title">告警中心</h1><div class="page-desc">运行异常、契约治理与资源生命周期事件</div></div><button class="secondary-btn">告警规则</button></div><div class="metrics-strip" style="grid-template-columns:repeat(4,minmax(0,1fr))">${metric("未处理", open, "需要关注", open ? "metric-down" : "")}${metric("观察中", watching, "自动恢复")}${metric("今日已关闭", closed, "来自当前数据")}${metric("高优先级", high, "error 级别", high ? "metric-down" : "")}</div><div class="table-wrap" style="border-top:1px solid var(--line);border-radius:8px"><table><thead><tr><th>严重级别</th><th>告警</th><th>任务</th><th>来源</th><th>时间</th><th>状态</th></tr></thead><tbody>${alerts.length ? alerts.map((alert) => `<tr data-alert-id="${alert.id}"><td><span class="status-pill ${alert.severity === "error" ? "status-failed" : alert.severity === "warn" ? "status-running" : "status-waiting"}"><span class="status-dot"></span>${alert.severity.toUpperCase()}</span></td><td><strong>${esc(alert.title)}</strong><div class="dense-sub mono">${alert.id}</div></td><td class="mono">${alert.task}</td><td>${esc(alert.source)}</td><td>${esc(alert.time)}</td><td>${esc(alert.status)}</td></tr>`).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--muted);height:100px">暂无告警</td></tr>`}</tbody></table></div></section>`;
  };

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function fmtTokens(value) {
    const n = Number(value || 0);
    if (!n) return "0";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(Math.round(n));
  }

  function statusPill(status) {
    const normalized = normalizeStatus(status);
    const label = statusText[normalized] || statusText.waiting;
    return `<span class="status-pill status-${normalized}"><span class="status-dot"></span>${label}</span>`;
  }

  function metric(label, value, foot, tone = "") {
    return `<div class="metric ${tone}"><div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(value)}</div><div class="metric-foot">${esc(foot)}</div></div>`;
  }

  function denseAlert(alert) {
    return `<button class="dense-item" data-alert-id="${esc(alert.id)}"><span class="status-pill ${alert.severity === "error" ? "status-failed" : alert.severity === "warn" ? "status-running" : "status-waiting"}"><span class="status-dot"></span>${esc(alert.severity || "info")}</span><span><strong>${esc(alert.title)}</strong><br><span class="dense-sub mono">${esc(alert.task || "--")}</span></span></button>`;
  }

  function renderWorkflowNode(node) {
    const mark = node.status === "success" ? "✓" : node.status === "running" ? "●" : "";
    return `<button class="wf-node ${node.type} ${node.status === "failed" ? "failed" : ""}" data-node-id="${esc(node.id)}" style="left:${node.x}px;top:${node.y}px"><div class="node-head"><span>${node.type === "agent" ? "AGENT" : node.type.toUpperCase()}</span><span>${mark}</span></div><div class="node-body"><div class="node-title">${esc(node.label)}</div><div class="node-summary">${esc(shortText(node.summary, 110))}</div>${node.metrics ? `<div class="node-foot"><span>${esc(node.metrics)}</span></div>` : ""}</div></button>`;
  }

  function applyWorkflowTransform() {
    const stage = document.getElementById("workflowStage");
    if (stage) {
      stage.style.transform = `translate(${appState.panX}px,${appState.panY}px) scale(${appState.zoom})`;
    }
  }

  function poolTier(name, count, size, color) {
    return `<div class="pool-tier"><div class="pool-tier-row"><span><i class="pool-dot" style="display:inline-block;background:${color};margin-right:7px"></i>${esc(name)}</span><strong>${count}</strong></div><div class="dense-sub" style="margin-top:4px">${esc(size)}</div></div>`;
  }

  function stateRow(state) {
    const tier = state.lifecycle === "deleted" || state.lifecycle === "tombstoned" ? "tombstone" : state.tier;
    return `<tr data-state-id="${esc(state.id)}" data-tier="${esc(tier)}"><td class="mono">${esc(state.id)}</td><td>${esc(state.type)}</td><td><span class="mode-tag">${esc(tier)}</span></td><td>${esc(state.lifecycle)}</td><td class="mono">${esc(state.agent)}</td><td class="mono">${esc(state.task)}</td><td>${state.size ? formatBytes(state.size) : "--"}</td><td>${esc(state.policy)}</td><td>${esc(state.readers)}</td><td>${state.protected ? "已保护" : "--"}</td></tr>`;
  }

  function objectTabs(drawer) {
    if (drawer.kind === "agent") return ["overview", "input", "output", "contract", "logs"];
    if (drawer.kind === "payload") return ["overview", "payload", "refs", "cost"];
    return ["overview", "raw"];
  }

  const tabLabels = {
    overview: "概览",
    input: "接收内容",
    output: "输出内容",
    contract: "契约与重试",
    logs: "日志",
    payload: "Payload",
    refs: "Refs",
    cost: "成本",
    raw: "原始数据"
  };

  function openDrawer(kind, obj, title, sub) {
    appState.drawer = { kind, obj: obj || {}, title, sub };
    appState.drawerTab = "overview";
    renderDrawer();
    document.getElementById("drawerBackdrop")?.classList.add("open");
    const drawer = document.getElementById("drawer");
    drawer?.classList.add("open");
    drawer?.setAttribute("aria-hidden", "false");
  }

  function closeDrawer() {
    document.getElementById("drawerBackdrop")?.classList.remove("open");
    const drawer = document.getElementById("drawer");
    drawer?.classList.remove("open");
    drawer?.setAttribute("aria-hidden", "true");
  }

  function renderDrawer() {
    const drawer = appState.drawer;
    if (!drawer) return;
    setText("#drawerTitle", drawer.title || "对象详情");
    setText("#drawerSub", drawer.sub || "");
    const tabs = document.getElementById("drawerTabs");
    const body = document.getElementById("drawerBody");
    if (tabs) {
      tabs.innerHTML = objectTabs(drawer).map((tab) => `<button class="drawer-tab ${appState.drawerTab === tab ? "active" : ""}" data-drawer-tab="${tab}">${tabLabels[tab]}</button>`).join("");
    }
    if (body) {
      body.innerHTML = drawerContent(drawer);
    }
  }

  function drawerContent(drawer) {
    const obj = drawer.obj || {};
    const tab = appState.drawerTab;
    if (tab === "overview") return detailGrid(obj);
    if (tab === "input") return `<pre class="code-block">${esc(obj.input || "无输入记录")}</pre>`;
    if (tab === "output") return `<pre class="code-block">${esc(obj.output || "无输出记录")}</pre>`;
    if (tab === "contract") return detailGrid({ contract_status: obj.contract || "not_checked", schema_valid: obj.contract === "schema_valid", retry_count: obj.contract === "schema_valid" ? 0 : 1, fallback: false });
    if (tab === "logs") return (obj.logs || []).map((log) => `<div class="log-line"><span>${esc(log.time)}</span><span class="log-level ${String(log.level || "info").toLowerCase()}">${esc(log.level || "INFO")}</span><span>${esc(log.text)}</span></div>`).join("") || `<div class="empty-hint">暂无日志</div>`;
    if (tab === "payload") return `<pre class="code-block">${esc(JSON.stringify(obj.payload || obj, null, 2))}</pre>`;
    if (tab === "refs") return detailGrid({ state_refs: (obj.refs || []).filter((item) => String(item).startsWith("state_")).join("\n") || "--", memory_refs: (obj.refs || []).filter((item) => String(item).startsWith("mem_")).join("\n") || "--" });
    if (tab === "cost") return detailGrid(obj.cost || {});
    return `<pre class="code-block">${esc(JSON.stringify(obj.raw || obj, null, 2))}</pre>`;
  }

  function showToast(text) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function openModal() {
    document.getElementById("experimentModal")?.classList.add("open");
    document.getElementById("newQuestion")?.focus();
  }

  function closeModal() {
    document.getElementById("experimentModal")?.classList.remove("open");
  }

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
    const snapshotTokenSummary = snapshot.token_summary || snapshot.summary?.token_summary || tokenSummaryFromModes(runtimeSummary, baselineSummary);
    applyTokenSummaryToTask(task, snapshotTokenSummary);
    task.status = normalizeStatus(snapshot.status);
    task.agents = runtime.agents.length || baseline.agents.length || task.agents;
    task.duration = seconds(runtime.finished?.latency_ms || runtimeSummary.latency_ms || baseline.finished?.latency_ms || baselineSummary.latency_ms);
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
    const task = {
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
    return applyTokenSummaryToTask(task, session.token_summary);
  }

  function taskFromRun(run) {
    const updatedAt = Number(run.updated_at || 0);
    const task = {
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
    return applyTokenSummaryToTask(task, run.token_summary);
  }

  function applyTokenSummaryToTask(task, rawSummary) {
    const summary = normalizeTokenSummary(rawSummary);
    task.costBreakdown = summary;
    task.tokens = summary.end_to_end_collaboration_tokens || task.tokens || 0;
    task.baselineTokens = summary.native_baseline_tokens || task.baselineTokens || 0;
    task.tokenSavings = summary.token_savings || 0;
    task.tokenSavingsRatio = summary.token_savings_ratio || 0;
    return task;
  }

  function normalizeTokenSummary(rawSummary) {
    const raw = rawSummary || {};
    const direct = number(raw.direct_message_tokens);
    const promptView = number(raw.prompt_view_tokens);
    const retrieved = number(raw.retrieved_memory_tokens);
    const control = number(raw.control_llm_tokens);
    const retry = number(raw.retry_tokens);
    const llmPrompt = number(raw.llm_prompt_tokens);
    const llmCompletion = number(raw.llm_completion_tokens);
    const llmTotal = number(raw.llm_total_tokens);
    let total = number(raw.end_to_end_collaboration_tokens || raw.runtime_tokens);
    if (!total) {
      total = direct + promptView + retrieved + control + retry + llmTotal;
    }
    const baseline = number(raw.native_baseline_tokens);
    const savings = baseline ? baseline - total : number(raw.token_savings);
    return {
      direct_message_tokens: direct,
      prompt_view_tokens: promptView,
      retrieved_memory_tokens: retrieved,
      control_llm_tokens: control,
      retry_tokens: retry,
      llm_prompt_tokens: llmPrompt,
      llm_completion_tokens: llmCompletion,
      llm_total_tokens: llmTotal,
      end_to_end_collaboration_tokens: total,
      native_baseline_tokens: baseline,
      runtime_tokens: total,
      token_savings: savings,
      token_savings_ratio: baseline ? savings / baseline : number(raw.token_savings_ratio),
      source: raw.source || ""
    };
  }

  function tokenSummaryFromModes(runtimeSummary, baselineSummary) {
    const runtime = normalizeTokenSummary({
      direct_message_tokens: runtimeSummary.direct_text_tokens,
      prompt_view_tokens: runtimeSummary.prompt_view_tokens,
      retrieved_memory_tokens: runtimeSummary.retrieved_memory_tokens,
      control_llm_tokens: runtimeSummary.control_llm_tokens,
      retry_tokens: runtimeSummary.retry_tokens,
      llm_prompt_tokens: runtimeSummary.llm_prompt_tokens,
      llm_completion_tokens: runtimeSummary.llm_completion_tokens,
      llm_total_tokens: runtimeSummary.llm_total_tokens,
      end_to_end_collaboration_tokens: runtimeSummary.end_to_end_collaboration_tokens || runtimeSummary.llm_total_tokens || runtimeSummary.prompt_tokens
    });
    const baseline = normalizeTokenSummary({
      end_to_end_collaboration_tokens: baselineSummary.end_to_end_collaboration_tokens || baselineSummary.llm_total_tokens || baselineSummary.prompt_tokens,
      direct_message_tokens: baselineSummary.direct_text_tokens,
      prompt_view_tokens: baselineSummary.prompt_view_tokens,
      retrieved_memory_tokens: baselineSummary.retrieved_memory_tokens,
      control_llm_tokens: baselineSummary.control_llm_tokens,
      retry_tokens: baselineSummary.retry_tokens,
      llm_total_tokens: baselineSummary.llm_total_tokens
    });
    runtime.native_baseline_tokens = baseline.end_to_end_collaboration_tokens;
    runtime.token_savings = baseline.end_to_end_collaboration_tokens - runtime.end_to_end_collaboration_tokens;
    runtime.token_savings_ratio = baseline.end_to_end_collaboration_tokens
      ? runtime.token_savings / baseline.end_to_end_collaboration_tokens
      : 0;
    runtime.source = "snapshot_by_mode";
    return runtime;
  }

  function tokenCell(task) {
    const cost = normalizeTokenSummary(task.costBreakdown);
    const total = cost.end_to_end_collaboration_tokens || task.tokens || 0;
    const baseline = cost.native_baseline_tokens || task.baselineTokens || 0;
    const ratio = baseline ? Math.max(0, (1 - total / baseline) * 100) : 0;
    const subline = baseline
      ? `参考 ${fmtTokens(baseline)} · 省 ${ratio.toFixed(1)}%`
      : cost.source === "autogen_trace"
        ? "AutoGen trace 聚合"
        : "等待成本数据";
    return `<div><strong>${fmtTokens(total)}</strong><div class="dense-sub">${subline}</div></div>`;
  }

  function tokenCostPanel(task) {
    const cost = normalizeTokenSummary(task.costBreakdown);
    if (!cost.end_to_end_collaboration_tokens && !cost.native_baseline_tokens) {
      return `<div class="empty-hint">等待 Token 成本数据</div>`;
    }
    return `<div class="legend">${tokenCostRows(cost).join("")}</div>`;
  }

  function tokenCostAside(task) {
    const cost = normalizeTokenSummary(task.costBreakdown);
    return [
      `<div class="aside-stat"><span>端到端</span><strong>${fmtTokens(cost.end_to_end_collaboration_tokens)}</strong></div>`,
      `<div class="aside-stat"><span>原生基线</span><strong>${cost.native_baseline_tokens ? fmtTokens(cost.native_baseline_tokens) : "--"}</strong></div>`,
      `<div class="aside-stat"><span>节省率</span><strong>${cost.native_baseline_tokens ? `${Math.max(0, cost.token_savings_ratio * 100).toFixed(1)}%` : "--"}</strong></div>`,
      `<div class="aside-stat"><span>Prompt View</span><strong>${fmtTokens(cost.prompt_view_tokens)}</strong></div>`
    ].join("");
  }

  function workflowTokenStrip(task) {
    const cost = normalizeTokenSummary(task.costBreakdown);
    const ratio = cost.native_baseline_tokens ? Math.max(0, cost.token_savings_ratio * 100).toFixed(1) + "%" : "--";
    return `<div class="workflow-token-strip">
      <div class="cost-chip"><span>本次任务 Token</span><strong>${fmtTokens(cost.end_to_end_collaboration_tokens || task.tokens)}</strong></div>
      <div class="cost-chip"><span>直接消息</span><strong>${fmtTokens(cost.direct_message_tokens)}</strong></div>
      <div class="cost-chip"><span>Prompt View</span><strong>${fmtTokens(cost.prompt_view_tokens)}</strong></div>
      <div class="cost-chip"><span>节省率</span><strong>${ratio}</strong></div>
    </div>`;
  }

  function tokenCostRows(cost) {
    const rows = [
      ["端到端总成本", cost.end_to_end_collaboration_tokens],
      ["实验参考成本", cost.native_baseline_tokens],
      ["直接消息", cost.direct_message_tokens],
      ["Prompt View", cost.prompt_view_tokens],
      ["记忆读取", cost.retrieved_memory_tokens],
      ["控制模块", cost.control_llm_tokens],
      ["重试", cost.retry_tokens],
      ["LLM usage", cost.llm_total_tokens]
    ];
    return rows.map(([label, value]) => `<div class="legend-item">${label}<strong>${fmtTokens(value)}</strong></div>`);
  }

  function number(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function mapAgents(items) {
    const grouped = new Map();
    (items || []).forEach((agent) => {
      const rawId = String(agent.agent_id || agent.id || "unknown");
      const id = canonicalAgentId(rawId);
      const profile = capabilityProfileFor(id);
      const row = grouped.get(id) || {
        id,
        agent_id: id,
        displayName: displayAgentName(id),
        role: profile.role,
        status: "online",
        calls: 0,
        p95: 0,
        error: 0,
        tokens: 0,
        memoryWrites: 0,
        description: profile.summary,
        capabilities: profile.capabilities,
        accepted: profile.accepted,
        rawIds: [],
        raw: [],
        input: "",
        output: "",
        contract: "not_checked",
        logs: []
      };
      row.calls += 1 + (agent.retries || []).length;
      row.status = agent.status === "running" || row.status === "busy" ? "busy" : "online";
      row.p95 = Math.max(row.p95, seconds(agent.latency_ms || 0));
      row.error += agent.contract_guard?.schema_valid === false ? 1 : 0;
      row.tokens += Number(agent.llm_total_tokens || agent.token_count || 0);
      row.memoryWrites += (agent.memory_refs || []).length;
      row.rawIds.push(rawId);
      row.raw.push(agent);
      row.input = row.input || agent.received_summary || "";
      row.output = row.output || agent.output_summary || agent.output_content || "";
      row.contract = agent.contract_guard?.contract_status || row.contract;
      grouped.set(id, row);
    });
    return [...grouped.values()].sort((left, right) => {
      const leftIndex = CANONICAL_AGENT_ORDER.indexOf(left.id);
      const rightIndex = CANONICAL_AGENT_ORDER.indexOf(right.id);
      return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex) || left.id.localeCompare(right.id);
    });
  }

  function canonicalAgentId(rawId) {
    const value = String(rawId || "").toLowerCase();
    if (value.includes("planner")) return "planner";
    if (value.includes("retriever")) return "retriever";
    if (value.includes("writer")) return "writer";
    if (value.includes("reviewer")) return "reviewer";
    if (value.includes("memory_manager") || value.includes("memorymanager")) return "memory_manager";
    if (value.includes("roundrobingroupchatmanager")) return "team_manager";
    if (value.includes("roundrobingroupchat")) return "team";
    if (value.includes("singlethreadedagentruntime")) return "runtime_bridge";
    if (value.includes("autogen")) return "autogen_driver";
    return value.replace(/_[0-9a-f-]{8,}.*/i, "").replace(/[^a-z0-9_]+/g, "_") || "autogen_driver";
  }

  function capabilityProfileFor(agentId) {
    const profile = appState.snapshot?.summary?.agent_profiles?.[agentId] || CAPABILITY_PROFILES[agentId];
    const fallback = CAPABILITY_PROFILES[agentId] || {};
    if (!profile) {
      return {
        role: roleName(agentId),
        summary: "来自运行轨迹的动态 Agent",
        capabilities: ["动态接入"],
        accepted: ["agent_output"]
      };
    }
    const capabilities = nonEmpty(profile.capabilities, fallback.capabilities).map(readableCapability);
    const accepted = nonEmpty(
      profile.accepted,
      profile.accepted_state_types,
      profile.message_types,
      fallback.accepted
    ).map(readableStateOrMessage);
    return {
      role: profile.role || roleName(agentId),
      summary: profile.summary || profile.description || fallback.summary || "规则版能力画像",
      capabilities,
      accepted
    };
  }

  function nonEmpty(...values) {
    for (const value of values) {
      if (Array.isArray(value) && value.length) return value;
    }
    return [];
  }

  function readableCapability(value) {
    return {
      task_decomposition: "任务拆解",
      routing_seed: "路由种子",
      retrieval: "检索",
      embedding_generation: "embedding 生成",
      evidence_ranking: "证据排序",
      synthesis: "综合生成",
      final_deliverable_draft: "交付草案",
      memory_use: "记忆使用",
      validation: "质量校验",
      schema_review: "schema 审查",
      failure_review: "失败复核",
      final_deliverable_review: "最终交付复核",
      memory_governance: "记忆治理",
      claim_compaction: "Claim 压缩"
    }[value] || value;
  }

  function readableStateOrMessage(value) {
    return {
      agent_output: "Agent 输出",
      route_decision: "路由决策",
      state_ref_handoff: "状态引用交接",
      retrieval_state: "检索状态",
      embedding_state: "Embedding 状态",
      artifact_state: "产物状态",
      memory_ref_handoff: "记忆引用交接",
      retry_loop_summary: "重试摘要",
      failure_state: "失败状态",
      final_deliverable: "最终交付",
      readiness_report: "就绪报告",
      memory_promotion: "记忆晋升",
      user_task: "用户任务",
      team_input: "Team 输入",
      team_output: "Team 输出",
      handoff_message: "交接消息",
      core_request: "Core 请求",
      core_response: "Core 响应",
      driver_event: "驱动事件"
    }[value] || value;
  }

  function displayAgentName(agentId) {
    return {
      planner: "planner",
      retriever: "retriever",
      writer: "writer",
      reviewer: "reviewer",
      memory_manager: "memory_manager",
      team: "AutoGen Team",
      team_manager: "Team Manager",
      runtime_bridge: "Runtime Bridge",
      autogen_driver: "AgentLite Driver"
    }[agentId] || agentId;
  }

  function mapStates(items, task) {
    return (items || []).map((state) => ({
      id: state.state_id || state.id || "state_unknown",
      type: state.state_type || state.type || "state",
      tier: state.tier || "hot",
      lifecycle: state.lifecycle || "active",
      agent: state.source_agent ? displayAgentName(canonicalAgentId(state.source_agent)) : (state.agent || "--"),
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
      label: readableMemoryLabel(node, index),
      x: 42 + (index % 4) * 260,
      y: 42 + Math.floor(index / 4) * 128,
      status: node.status || "active",
      summary: node.summary || "",
      slot: node.slot || node.type || "",
      confidence: node.confidence ?? "",
      raw: node
    }));
  }

  function readableMemoryLabel(node, index) {
    const raw = String(node.label || node.id || `Memory ${index + 1}`);
    if (raw.startsWith("state_")) return `${raw.slice(0, 18)}...`;
    if (raw.startsWith("mem_")) return `${raw.slice(0, 16)}...`;
    if (raw.length > 28) return `${raw.slice(0, 25)}...`;
    return raw;
  }

  function filteredMemoryNodes() {
    const selected = appState.memoryTypeFilter || "all";
    if (selected === "all") return memoryNodes;
    return memoryNodes.filter((node) => node.type === selected);
  }

  function buildWorkflow(mode, task, modeName) {
    const agentsInMode = mapAgents(mode.agents || []);
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
      const agentId = `${modeName}_${agent.id || agent.agent_id || index}`;
      const message = messages[index] || {};
      if (index > 0 || messages.length) {
        const payloadId = `${modeName}_payload_${index}`;
        nodes.push({
          id: payloadId,
          type: "payload",
          label: "Handoff Message",
          summary: message.summary || message.content || `${messages[index - 1]?.sender || "agent"} -> ${agent.id || agent.agent_id || "agent"}`,
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
        label: agent.role || roleName(agent.id || agent.agent_id || "agent"),
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
      edges.push([state.source_agent ? `${modeName}_${canonicalAgentId(state.source_agent)}` : previous, stateId]);
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
    const rawIds = agent.rawIds || [agent.agent_id || agent.id].filter(Boolean);
    const relevantLogs = (mode.timeline || [])
      .filter((event) => !event.agent_id || rawIds.includes(event.agent_id) || canonicalAgentId(event.agent_id) === (agent.id || agent.agent_id))
      .slice(-8)
      .map((event) => ({ time: formatIsoTime(event.ts), level: "INFO", text: `${event.event_type}: ${event.summary || ""}` }));
    const profile = capabilityProfileFor(agent.id || agent.agent_id || "agent");
    return {
      role: profile.role,
      capability_profile: profile.summary,
      capabilities: profile.capabilities,
      accepted_state_or_message_types: profile.accepted,
      raw_agent_ids: rawIds,
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

  workflowSet = function () {
    return appState.workflowMode === "runtime_lite"
      ? { nodes: runtimeNodes, edges: runtimeEdges }
      : { nodes: baselineNodes, edges: baselineEdges };
  };

  drawWorkflowEdges = function () {
    const svg = document.getElementById("wfEdges");
    if (!svg) return;
    svg.querySelectorAll(".wf-edge").forEach((edge) => edge.remove());
    const { edges } = workflowSet();
    edges.forEach(([fromId, toId], index) => {
      const from = document.querySelector(`.wf-node[data-node-id="${cssEscape(fromId)}"]`);
      const to = document.querySelector(`.wf-node[data-node-id="${cssEscape(toId)}"]`);
      if (!from || !to) return;
      const start = edgeAnchor(from, to, true);
      const end = edgeAnchor(to, from, false);
      const dx = Math.abs(end.x - start.x);
      const dy = Math.abs(end.y - start.y);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const d = dx > dy
        ? `M${start.x},${start.y} C${(start.x + end.x) / 2},${start.y} ${(start.x + end.x) / 2},${end.y} ${end.x},${end.y}`
        : `M${start.x},${start.y} C${start.x},${(start.y + end.y) / 2} ${end.x},${(start.y + end.y) / 2} ${end.x},${end.y}`;
      path.setAttribute("d", d);
      path.setAttribute("class", `wf-edge ${selectedTask().status === "running" && index > 4 ? "running" : ""}`);
      svg.append(path);
    });
  };

  function edgeAnchor(source, target, isStart) {
    const sx = source.offsetLeft;
    const sy = source.offsetTop;
    const sw = source.offsetWidth;
    const sh = source.offsetHeight;
    const tx = target.offsetLeft + target.offsetWidth / 2;
    const ty = target.offsetTop + target.offsetHeight / 2;
    const cx = sx + sw / 2;
    const cy = sy + sh / 2;
    const dx = tx - cx;
    const dy = ty - cy;
    if (Math.abs(dx) > Math.abs(dy)) {
      return { x: dx >= 0 ? sx + sw : sx, y: cy };
    }
    if (isStart) {
      return { x: cx, y: dy >= 0 ? sy + sh : sy };
    }
    return { x: cx, y: dy >= 0 ? sy : sy + sh };
  }

  fitWorkflow = function () {
    const vp = document.getElementById("workflowViewport");
    if (!vp) return;
    appState.zoom = Math.min((vp.clientWidth - 80) / 1400, (vp.clientHeight - 60) / 900, 0.95);
    appState.panX = (vp.clientWidth - 1400 * appState.zoom) / 2;
    appState.panY = Math.max(20, (vp.clientHeight - 900 * appState.zoom) / 2);
    applyWorkflowTransform();
  };

  initWorkflowPan = function () {
    const vp = document.getElementById("workflowViewport");
    const stage = document.getElementById("workflowStage");
    if (!vp || !stage || vp.dataset.ready) return;
    vp.dataset.ready = "1";
    let panning = false;
    let draggingNode = null;
    let sx = 0;
    let sy = 0;
    let px = 0;
    let py = 0;
    let nodeStartX = 0;
    let nodeStartY = 0;

    vp.addEventListener("pointerdown", (event) => {
      const nodeEl = event.target.closest(".wf-node");
      if (nodeEl) {
        draggingNode = { el: nodeEl, node: workflowSet().nodes.find((node) => node.id === nodeEl.dataset.nodeId), moved: false };
        if (!draggingNode.node) return;
        sx = event.clientX;
        sy = event.clientY;
        nodeStartX = draggingNode.node.x;
        nodeStartY = draggingNode.node.y;
        nodeEl.classList.add("dragging");
        nodeEl.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
      panning = true;
      sx = event.clientX;
      sy = event.clientY;
      px = appState.panX;
      py = appState.panY;
      document.body.style.userSelect = "none";
      vp.classList.add("dragging");
      vp.setPointerCapture(event.pointerId);
    });

    vp.addEventListener("pointermove", (event) => {
      if (draggingNode?.node) {
        const dx = (event.clientX - sx) / Math.max(appState.zoom, 0.1);
        const dy = (event.clientY - sy) / Math.max(appState.zoom, 0.1);
        if (Math.abs(dx) + Math.abs(dy) > 3) draggingNode.moved = true;
        draggingNode.node.x = Math.max(0, nodeStartX + dx);
        draggingNode.node.y = Math.max(0, nodeStartY + dy);
        draggingNode.el.style.left = `${draggingNode.node.x}px`;
        draggingNode.el.style.top = `${draggingNode.node.y}px`;
        drawWorkflowEdges();
        return;
      }
      if (!panning) return;
      appState.panX = px + event.clientX - sx;
      appState.panY = py + event.clientY - sy;
      applyWorkflowTransform();
    });

    function stopDrag(event) {
      if (draggingNode?.el) {
        draggingNode.el.classList.remove("dragging");
        if (draggingNode.moved) {
          window.__agentliteNodeDraggedUntil = Date.now() + 120;
        }
      }
      draggingNode = null;
      panning = false;
      document.body.style.userSelect = "";
      vp.classList.remove("dragging");
      try {
        vp.releasePointerCapture(event.pointerId);
      } catch (_) {
        // Pointer capture may already be released by the browser.
      }
    }

    vp.addEventListener("pointerup", stopDrag);
    vp.addEventListener("pointercancel", stopDrag);
    vp.addEventListener("wheel", (event) => {
      event.preventDefault();
      appState.zoom = Math.max(0.35, Math.min(1.5, appState.zoom + (event.deltaY < 0 ? 0.08 : -0.08)));
      applyWorkflowTransform();
    }, { passive: false });
  };

  drawMemoryEdges = function () {
    const svg = document.getElementById("memoryEdges");
    if (!svg) return;
    svg.querySelectorAll(".memory-edge").forEach((edge) => edge.remove());
    const visibleIds = new Set(filteredMemoryNodes().map((node) => node.id));
    memoryEdges.forEach(([fromId, toId]) => {
      if (!visibleIds.has(fromId) || !visibleIds.has(toId)) return;
      const from = memoryNodes.find((node) => node.id === fromId);
      const to = memoryNodes.find((node) => node.id === toId);
      if (!from || !to) return;
      const x1 = from.x + 172;
      const y1 = from.y + 34;
      const x2 = to.x;
      const y2 = to.y + 34;
      const mid = (x1 + x2) / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);
      path.setAttribute("class", "memory-edge");
      svg.append(path);
    });
  };

  fitMemory = function () {
    const canvas = document.getElementById("memoryCanvas");
    const stage = document.getElementById("memoryStage");
    if (!canvas || !stage) return;
    const visible = filteredMemoryNodes();
    const maxX = visible.reduce((max, node) => Math.max(max, node.x + 220), 900);
    const maxY = visible.reduce((max, node) => Math.max(max, node.y + 110), 650);
    stage.style.width = `${Math.max(900, maxX)}px`;
    stage.style.minHeight = `${Math.max(650, maxY)}px`;
    const svg = document.getElementById("memoryEdges");
    if (svg) {
      svg.setAttribute("width", String(Math.max(900, maxX)));
      svg.setAttribute("height", String(Math.max(650, maxY)));
      svg.style.width = `${Math.max(900, maxX)}px`;
      svg.style.height = `${Math.max(650, maxY)}px`;
    }
    const scale = Math.min(1, Math.max(0.42, (canvas.clientWidth - 48) / Math.max(900, maxX)));
    stage.style.transform = `scale(${scale})`;
    canvas.scrollTo({ left: 0, top: 0, behavior: "smooth" });
  };

  function cssEscape(value) {
    if (window.CSS?.escape) return CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
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

  function shortText(value, limit) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
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

  detailGrid = function (obj) {
    const hidden = new Set(["logs", "input", "output", "payload", "cost", "raw"]);
    return `<div class="detail-grid">${Object.entries(obj || {})
      .filter(([key]) => !hidden.has(key))
      .map(([key, value]) => `<div class="detail-key">${esc(key)}</div><div class="detail-value ${typeof value === "string" && value.length > 40 ? "mono" : ""}">${esc(Array.isArray(value) ? value.join("\n") : typeof value === "object" ? JSON.stringify(value, null, 2) : value)}</div>`)
      .join("")}</div>`;
  };

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-nav]");
    if (nav) {
      event.preventDefault();
      setView(nav.dataset.nav);
      return;
    }

    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "new-experiment") {
      event.preventDefault();
      openModal();
      return;
    }
    if (action === "close-modal") {
      event.preventDefault();
      closeModal();
      return;
    }
    if (action === "close-drawer") {
      event.preventDefault();
      closeDrawer();
      return;
    }

    const task = event.target.closest("[data-task-id]");
    if (task) {
      appState.selectedTask = task.dataset.taskId;
      appState.workflowMode = tasks.find((item) => item.id === appState.selectedTask)?.mode || "runtime_lite";
      setView("workflow");
      return;
    }

    const mode = event.target.closest("[data-mode]");
    if (mode) {
      appState.workflowMode = mode.dataset.mode;
      render();
      requestAnimationFrame(fitWorkflow);
      return;
    }

    const node = event.target.closest("[data-node-id]");
    if (node) {
      const found = workflowSet().nodes.find((item) => item.id === node.dataset.nodeId);
      if (found) {
        openDrawer(found.type === "agent" ? "agent" : found.type === "payload" ? "payload" : "object", found.detail, found.label, `${found.type} · ${found.id}`);
      }
      return;
    }

    const stateEl = event.target.closest("[data-state-id]");
    if (stateEl) {
      const state = states.find((item) => item.id === stateEl.dataset.stateId);
      if (state) openDrawer("object", state, "状态对象", state.id);
      return;
    }

    const memoryEl = event.target.closest("[data-memory-id]");
    if (memoryEl) {
      const memory = memoryNodes.find((item) => item.id === memoryEl.dataset.memoryId);
      if (memory) openDrawer("object", memory, "记忆对象", memory.id);
      return;
    }

    if (event.target.closest("[data-memory-fit]")) {
      fitMemory();
      showToast("图谱已适配当前视图");
      return;
    }

    const alertEl = event.target.closest("[data-alert-id]");
    if (alertEl) {
      const alert = alerts.find((item) => item.id === alertEl.dataset.alertId);
      if (alert) openDrawer("object", alert, "告警详情", alert.id);
      return;
    }

    const tab = event.target.closest("[data-drawer-tab]");
    if (tab) {
      appState.drawerTab = tab.dataset.drawerTab;
      renderDrawer();
      return;
    }

    const sort = event.target.closest("[data-sort]");
    if (sort) {
      if (appState.sortKey === sort.dataset.sort) {
        appState.sortDir = appState.sortDir === "asc" ? "desc" : "asc";
      } else {
        appState.sortKey = sort.dataset.sort;
        appState.sortDir = "asc";
      }
      render();
      return;
    }

    const canvas = event.target.closest("[data-canvas]")?.dataset.canvas;
    if (canvas) {
      if (canvas === "zoom-in") appState.zoom = Math.min(1.5, appState.zoom + 0.1);
      if (canvas === "zoom-out") appState.zoom = Math.max(0.35, appState.zoom - 0.1);
      if (canvas === "reset") {
        appState.zoom = 0.82;
        appState.panX = 80;
        appState.panY = 10;
      }
      if (canvas === "fit") fitWorkflow();
      else applyWorkflowTransform();
    }
  });

  document.getElementById("drawerBackdrop")?.addEventListener("click", closeDrawer);
  document.getElementById("mobileMenu")?.addEventListener("click", () => document.getElementById("sidebar")?.classList.toggle("mobile-open"));
  document.getElementById("globalSearch")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      appState.taskQuery = event.target.value;
      setView("tasks");
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id === "taskSearch") {
      appState.taskQuery = event.target.value;
      if (appState.view === "workflowList") render();
      else updateTaskTable();
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target?.id === "taskStatus") {
      appState.taskStatus = event.target.value;
      if (appState.view === "workflowList") render();
      else updateTaskTable();
    }
    if (event.target?.id === "taskMode") {
      appState.taskMode = event.target.value;
      updateTaskTable();
    }
    if (event.target?.id === "stateTier") {
      document.querySelectorAll("#stateRows tr").forEach((row) => {
        row.style.display = event.target.value === "all" || row.dataset.tier === event.target.value ? "" : "none";
      });
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      closeModal();
    }
  });

  window.addEventListener("resize", () => {
    if (appState.view === "workflow") fitWorkflow();
    if (appState.view === "memory") fitMemory();
  });

  document.addEventListener("click", (event) => {
    if (Date.now() < Number(window.__agentliteNodeDraggedUntil || 0) && event.target.closest(".wf-node")) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener("click", (event) => {
    const agent = event.target.closest("[data-agent-id]");
    if (!agent) return;
    const row = agents.find((item) => item.id === agent.dataset.agentId);
    if (!row) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openDrawer("agent", row, "Agent 能力画像", row.id);
  }, true);

  document.addEventListener("click", (event) => {
    const filter = event.target.closest("[data-memory-filter]");
    if (!filter) return;
    event.preventDefault();
    appState.memoryTypeFilter = filter.dataset.memoryFilter || "all";
    render();
  }, true);

  document.addEventListener("change", (event) => {
    if (event.target?.id === "memoryTypeFilter") {
      appState.memoryTypeFilter = event.target.value || "all";
      render();
    }
  }, true);

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
