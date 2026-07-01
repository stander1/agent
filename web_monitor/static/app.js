const state = {
  currentRunId: "",
  currentExperimentId: "",
  pollTimer: 0,
  snapshot: null,
};

const els = {
  statusBadge: document.getElementById("statusBadge"),
  runSelect: document.getElementById("runSelect"),
  refreshRunsBtn: document.getElementById("refreshRunsBtn"),
  titleInput: document.getElementById("titleInput"),
  promptInput: document.getElementById("promptInput"),
  engineSelect: document.getElementById("engineSelect"),
  roundsInput: document.getElementById("roundsInput"),
  startBtn: document.getElementById("startBtn"),
  baselineFlow: document.getElementById("baselineFlow"),
  runtimeFlow: document.getElementById("runtimeFlow"),
  baselineMeta: document.getElementById("baselineMeta"),
  runtimeMeta: document.getElementById("runtimeMeta"),
  detailType: document.getElementById("detailType"),
  detailBody: document.getElementById("detailBody"),
  statePool: document.getElementById("statePool"),
  stateCount: document.getElementById("stateCount"),
  memoryGraph: document.getElementById("memoryGraph"),
  memoryCount: document.getElementById("memoryCount"),
};

window.addEventListener("DOMContentLoaded", async () => {
  els.refreshRunsBtn.addEventListener("click", loadRuns);
  els.runSelect.addEventListener("change", () => {
    state.currentExperimentId = "";
    state.currentRunId = els.runSelect.value;
    stopPolling();
    if (state.currentRunId) {
      loadRunSnapshot(state.currentRunId);
    }
  });
  els.startBtn.addEventListener("click", startExperiment);
  await loadRuns();
});

async function loadRuns() {
  const payload = await getJson("/api/runs");
  const runs = payload.runs || [];
  els.runSelect.innerHTML = "";
  for (const run of runs) {
    const option = document.createElement("option");
    option.value = run.run_id;
    option.textContent = `${run.run_id} (${run.status})`;
    els.runSelect.append(option);
  }
  if (!state.currentRunId && runs.length) {
    state.currentRunId = runs[0].run_id;
    els.runSelect.value = state.currentRunId;
    await loadRunSnapshot(state.currentRunId);
  }
}

async function startExperiment() {
  stopPolling();
  setStatus("running");
  const payload = {
    title: els.titleInput.value.trim() || "用户输入任务",
    prompt: els.promptInput.value.trim(),
    documents: [],
    engine: els.engineSelect.value,
    rounds: Number(els.roundsInput.value || 1),
  };
  if (!payload.prompt) {
    setStatus("failed");
    renderDetail("error", { error: "prompt is required" });
    return;
  }
  const response = await fetch("/api/experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const item = await response.json();
  if (!response.ok) {
    setStatus("failed");
    renderDetail("error", item);
    return;
  }
  state.currentExperimentId = item.experiment_id;
  state.currentRunId = item.run_id;
  pollExperiment();
}

async function pollExperiment() {
  if (!state.currentExperimentId) return;
  const snapshot = await getJson(`/api/experiments/${encodeURIComponent(state.currentExperimentId)}/snapshot`);
  renderSnapshot(snapshot);
  if (snapshot.status === "running") {
    state.pollTimer = window.setTimeout(pollExperiment, 900);
  } else {
    stopPolling();
    await loadRuns();
  }
}

async function loadRunSnapshot(runId) {
  const snapshot = await getJson(`/api/runs/${encodeURIComponent(runId)}/snapshot`);
  renderSnapshot(snapshot);
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  setStatus(snapshot.status || "unknown");
  renderMode("baseline_text", snapshot.modes?.baseline_text || {}, els.baselineFlow, els.baselineMeta);
  renderMode("runtime_lite", snapshot.modes?.runtime_lite || {}, els.runtimeFlow, els.runtimeMeta);
  const runtime = snapshot.modes?.runtime_lite || {};
  renderStatePool(runtime.state_pool || []);
  renderMemoryGraph(runtime.memory_graph || { nodes: [], edges: [] });
  if (!els.detailBody.dataset.locked) {
    renderDetail("run", {
      run_id: snapshot.run_id,
      status: snapshot.status,
      output_dir: snapshot.output_dir,
      errors: snapshot.errors || [],
    });
  }
}

function renderMode(modeName, mode, container, meta) {
  const agents = mode.agents || [];
  meta.textContent = `${agents.length} agents · ${(mode.messages || []).length} messages`;
  container.innerHTML = "";
  if (!agents.length) {
    container.innerHTML = `<div class="empty">等待 trace</div>`;
    return;
  }
  agents.forEach((agent, index) => {
    const row = document.createElement("div");
    row.className = "agent-row";

    const node = document.createElement("button");
    node.type = "button";
    node.className = `agent-node ${agent.status || "pending"}`;
    node.innerHTML = `
      <span class="agent-name">${escapeHtml(agent.agent_id)}</span>
      <span class="agent-status">${escapeHtml(agent.status || "pending")} · prompt ${agent.prompt_chars || 0}</span>
      <span class="small-muted">${escapeHtml(agent.received_summary || "")}</span>
    `;
    node.addEventListener("click", () => renderDetail(`${modeName} / ${agent.agent_id}`, agent));

    const arrow = document.createElement("div");
    arrow.className = "arrow";
    arrow.textContent = index < agents.length - 1 || agent.handoff_to ? "→" : "•";

    const output = document.createElement("button");
    output.type = "button";
    output.className = "agent-output";
    output.innerHTML = `
      <div class="summary">${escapeHtml(agent.output_summary || "尚无输出")}</div>
      <div class="refs">${renderRefs(agent.state_refs, "S")}${renderRefs(agent.memory_refs, "M")}</div>
    `;
    output.addEventListener("click", () => renderDetail(`${modeName} / ${agent.agent_id}`, agent));

    row.append(node, arrow, output);
    container.append(row);
  });
}

function renderStatePool(states) {
  const tiers = ["hot", "warm", "cold", "tombstone"];
  els.stateCount.textContent = `${states.length} states`;
  els.statePool.innerHTML = "";
  for (const tier of tiers) {
    const col = document.createElement("div");
    col.className = "state-tier";
    const items = states.filter((item) => {
      if (tier === "tombstone") return ["deleted", "tombstoned"].includes(item.lifecycle);
      return item.tier === tier && !["deleted", "tombstoned"].includes(item.lifecycle);
    });
    col.innerHTML = `<div class="tier-title">${tier} · ${items.length}</div>`;
    const list = document.createElement("div");
    list.className = "state-list";
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `state-item ${tier}`;
      button.innerHTML = `
        <div class="state-id">${escapeHtml(item.state_id)}</div>
        <div class="small-muted">${escapeHtml(item.source_agent || "")} · ${escapeHtml(item.state_type || "")}</div>
        <div class="summary">${escapeHtml(item.summary || "")}</div>
        <div class="small-muted">${item.size_bytes || 0} bytes · ${escapeHtml(item.access_policy || "")}</div>
      `;
      button.addEventListener("click", () => renderDetail(`state / ${item.state_id}`, item));
      list.append(button);
    }
    if (!items.length) {
      list.innerHTML = `<div class="empty">空</div>`;
    }
    col.append(list);
    els.statePool.append(col);
  }
}

function renderMemoryGraph(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  els.memoryCount.textContent = `${nodes.length} nodes · ${edges.length} edges`;
  const svg = els.memoryGraph;
  svg.innerHTML = "";
  if (!nodes.length) {
    svg.innerHTML = `<text x="24" y="42" fill="#667481">暂无记忆节点</text>`;
    return;
  }
  const width = 960;
  const height = 360;
  const columns = groupNodes(nodes);
  const positions = {};
  const columnKeys = Object.keys(columns);
  columnKeys.forEach((key, columnIndex) => {
    const items = columns[key];
    const x = 70 + columnIndex * Math.max(140, (width - 140) / Math.max(1, columnKeys.length - 1));
    items.forEach((node, rowIndex) => {
      const y = 50 + rowIndex * Math.max(46, (height - 100) / Math.max(1, items.length));
      positions[node.id] = { x, y };
    });
  });

  for (const edge of edges) {
    const a = positions[edge.source];
    const b = positions[edge.target];
    if (!a || !b) continue;
    const line = svgEl("line", {
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      class: "graph-edge",
    });
    svg.append(line);
  }

  for (const node of nodes) {
    const p = positions[node.id];
    if (!p) continue;
    const g = svgEl("g", { class: "graph-node", tabindex: "0" });
    const typeClass = `node-${String(node.type || "node").toLowerCase()}`;
    g.append(svgEl("circle", { cx: p.x, cy: p.y, r: 16, class: typeClass }));
    const label = svgEl("text", { x: p.x + 22, y: p.y + 4 });
    label.textContent = shorten(node.label || node.id, 22);
    g.append(label);
    g.addEventListener("click", () => renderDetail(`memory / ${node.id}`, node));
    svg.append(g);
  }
}

function renderDetail(type, payload) {
  els.detailBody.dataset.locked = "1";
  els.detailType.textContent = type;
  els.detailBody.innerHTML = "";
  const blocks = [];
  if (payload.received_summary) blocks.push(["接收", payload.received_summary]);
  if (payload.output_summary) blocks.push(["输出摘要", payload.output_summary]);
  if (payload.output_content) blocks.push(["完整消息", payload.output_content]);
  if (payload.contract_guard && Object.keys(payload.contract_guard).length) {
    blocks.push(["契约状态", JSON.stringify(payload.contract_guard, null, 2)]);
  }
  if (payload.retries && payload.retries.length) {
    blocks.push(["重试", JSON.stringify(payload.retries, null, 2)]);
  }
  blocks.push(["原始数据", JSON.stringify(payload, null, 2)]);
  for (const [title, text] of blocks) {
    const block = document.createElement("div");
    block.className = "detail-block";
    block.innerHTML = `<h3>${escapeHtml(title)}</h3><pre>${escapeHtml(text)}</pre>`;
    els.detailBody.append(block);
  }
}

function renderRefs(refs, prefix) {
  if (!Array.isArray(refs) || !refs.length) return "";
  return refs
    .slice(0, 4)
    .map((ref) => {
      const id = typeof ref === "string" ? ref : ref.state_id || ref.memory_id || JSON.stringify(ref);
      return `<span class="ref">${prefix}:${escapeHtml(id)}</span>`;
    })
    .join("");
}

function groupNodes(nodes) {
  const order = ["StateObject", "PromotionView", "ClaimCard", "MemoryView", "MemoryObject", "MemoryCandidate", "MemoryRef", "Agent"];
  const groups = {};
  for (const key of order) groups[key] = [];
  for (const node of nodes) {
    const key = order.includes(node.type) ? node.type : "MemoryObject";
    groups[key].push(node);
  }
  return Object.fromEntries(Object.entries(groups).filter(([, items]) => items.length));
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function setStatus(status) {
  els.statusBadge.textContent = status;
  els.statusBadge.className = `status ${status}`;
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = 0;
  }
}

function svgEl(name, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    el.setAttribute(key, value);
  }
  return el;
}

function shorten(text, limit) {
  const compact = String(text || "").replace(/\s+/g, " ").trim();
  return compact.length <= limit ? compact : `${compact.slice(0, limit - 1)}…`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
