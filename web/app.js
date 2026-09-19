/* ==========================================================================
   From Chaos to Symphony — showcase front end

   Three jobs: switch pattern, draw the interaction diagram, and stream a live
   run. No framework and no CDN on purpose — the whole repo is built to run
   with the network unplugged, and a conference demo that waits on unpkg is a
   conference demo that fails.
   ========================================================================== */

'use strict';

const $  = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

const state = {
  patterns: [],
  tiers: [],
  current: null,
  devui: { available: false, url: '', entities: {} },
  source: null,      // EventSource for the live run
  runId: null,
  traces: [],        // OpenTelemetry spans from the last run
  nodes: new Map(),  // diagram node id -> <g>
  approvalTimer: null,
};

/* ── boot ─────────────────────────────────────────────────────── */

async function boot() {
  const data = await (await fetch('/api/patterns')).json();
  state.patterns = data.patterns;
  state.tiers = data.tiers;

  const pill = $('providerPill');
  pill.textContent = data.offline ? 'offline · no keys needed' : `live · ${data.provider}`;
  pill.classList.add(data.offline ? 'pill-offline' : 'pill-live');

  $('devuiLink').href = data.devuiUrl;
  $('devuiOpen').href = data.devuiUrl;

  buildRail();
  wireControls();

  // Deep-link support, so a slide can point straight at one pattern.
  const wanted = new URLSearchParams(location.search).get('pattern');
  select(state.patterns.find((p) => p.slug === wanted) || state.patterns[0]);

  refreshDevui();
  loadStore();
}

/* ── rail ─────────────────────────────────────────────────────── */

function buildRail() {
  const host = $('railGroups');
  host.innerHTML = '';
  $('railCount').textContent = `${state.patterns.length} patterns`;

  for (const tier of state.tiers) {
    host.appendChild(el('div', 'rail-group-label', tier.title));
    host.appendChild(el('div', 'rail-group-blurb', tier.blurb));
    for (const pattern of state.patterns.filter((p) => p.tier === tier.id)) {
      const item = el('button', 'rail-item');
      item.dataset.slug = pattern.slug;
      item.appendChild(el('span', 'n', String(pattern.number).padStart(2, '0')));
      item.appendChild(el('span', 'label', pattern.name));
      if (pattern.newThisYear) item.appendChild(el('span', 'dot dot-new'));
      item.addEventListener('click', () => select(pattern));
      host.appendChild(item);
    }
  }
}

/* ── pattern selection ────────────────────────────────────────── */

function select(pattern) {
  if (!pattern) return;
  stopRun();
  state.current = pattern;

  document.querySelectorAll('.rail-item').forEach((n) =>
    n.classList.toggle('is-on', n.dataset.slug === pattern.slug));

  $('pNumber').textContent = String(pattern.number).padStart(2, '0');
  $('pName').textContent = pattern.name;
  $('pTagline').textContent = pattern.tagline;
  $('pSummary').textContent = pattern.summary;
  $('pFail').textContent = pattern.failureMode;
  $('pScenario').textContent = pattern.scenario;
  $('prompt').value = pattern.defaultPrompt;

  const tier = $('pTier');
  tier.textContent = pattern.newThisYear ? 'new this year' : pattern.tier;
  tier.className = 'badge badge-' + pattern.tier;

  fillList($('pUse'), pattern.useWhen);
  fillList($('pAvoid'), pattern.avoidWhen);
  fillList($('pApi'), pattern.mafApi);

  $('diagramHint').textContent = pattern.hasCustomRunner
    ? 'this pattern drives itself — watch the log'
    : 'nodes light up as the workflow runs';

  drawDiagram(pattern.diagram);
  state.traces = [];
  renderTraces();
  resetConsole();
  pointDevuiAt(pattern.slug);

  const url = new URL(location.href);
  url.searchParams.set('pattern', pattern.slug);
  history.replaceState({}, '', url);
}

function fillList(host, items) {
  host.innerHTML = '';
  for (const item of items) host.appendChild(el('li', null, item));
}

/* ── diagram ──────────────────────────────────────────────────── */

const NODE_W = 152, NODE_H = 46, ROW_GAP = 22, PAD = 16;
const MIN_COL_GAP = 78;
const LABEL_CHAR_W = 5.2;   // the edge-label face is monospaced at 8.5px

/** Column gap wide enough that the longest edge label fits between two boxes. */
function columnGap(edges) {
  const longest = edges.reduce((n, e) => Math.max(n, (e.label || '').length), 0);
  return Math.max(MIN_COL_GAP, Math.ceil(longest * LABEL_CHAR_W) + 26);
}

/**
 * Assign each node a column by longest path from a root, so the drawing reads
 * left to right in execution order.
 *
 * Several patterns are genuinely cyclic — the reflection loop closes a cycle,
 * and handoff lets a specialist return a case to triage. A naive longest-path
 * walk over a cyclic graph diverges, so back-edges are found with a depth-first
 * search (an edge into a node already on the stack) and excluded from the depth
 * calculation. They are still drawn, as curves routed under the boxes.
 */
function layout(nodes, edges, colGap) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const out = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) if (byId.has(e.source) && byId.has(e.target)) out.get(e.source).push(e);

  // 1. Find back-edges with an iterative DFS (recursion would risk a deep stack
  //    on a pathological graph, and this runs on every pattern switch).
  const back = new Set();
  const colour = new Map(nodes.map((n) => [n.id, 0])); // 0 unseen, 1 on stack, 2 done
  const edgeKey = (e) => `${e.source}\u0000${e.target}`;

  const targeted = new Set(edges.filter((e) => byId.has(e.target)).map((e) => e.target));
  const roots = nodes.filter((n) => !targeted.has(n.id)).map((n) => n.id);
  const starts = roots.length ? roots : [nodes[0].id];

  for (const start of [...starts, ...nodes.map((n) => n.id)]) {
    if (colour.get(start) !== 0) continue;
    const stack = [{ id: start, i: 0 }];
    colour.set(start, 1);
    while (stack.length) {
      const frame = stack[stack.length - 1];
      const outgoing = out.get(frame.id) || [];
      if (frame.i >= outgoing.length) {
        colour.set(frame.id, 2);
        stack.pop();
        continue;
      }
      const edge = outgoing[frame.i++];
      const state = colour.get(edge.target);
      if (state === 1) back.add(edgeKey(edge));       // points at an ancestor: cycle
      else if (state === 0) {
        colour.set(edge.target, 1);
        stack.push({ id: edge.target, i: 0 });
      }
    }
  }

  // 2. Longest-path depth over the remaining acyclic edges.
  const forward = edges.filter((e) => byId.has(e.source) && byId.has(e.target) && !back.has(edgeKey(e)));
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const e of forward) {
      const candidate = depth.get(e.source) + 1;
      if (candidate > depth.get(e.target)) { depth.set(e.target, candidate); moved = true; }
    }
    if (!moved) break;
  }

  // 3. Column layout, vertically centred.
  const columns = new Map();
  for (const n of nodes) {
    const d = depth.get(n.id);
    if (!columns.has(d)) columns.set(d, []);
    columns.get(d).push(n);
  }
  const tallest = Math.max(...[...columns.values()].map((c) => c.length));
  const maxDepth = Math.max(...depth.values());
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * ROW_GAP;
  const width = PAD * 2 + (maxDepth + 1) * NODE_W + maxDepth * colGap;

  const positions = new Map();
  for (const [d, column] of columns) {
    const blockHeight = column.length * NODE_H + (column.length - 1) * ROW_GAP;
    const top = (height - blockHeight) / 2;
    column.forEach((n, i) => positions.set(n.id, {
      x: PAD + d * (NODE_W + colGap),
      y: top + i * (NODE_H + ROW_GAP),
    }));
  }
  const returns = edges.filter((e) => {
    const a = positions.get(e.source), b = positions.get(e.target);
    return a && b && b.x <= a.x;
  }).length;
  // Return edges are routed in a lane beneath every box; reserve room for it.
  const lane = returns ? 22 + Math.min(returns, 3) * 15 + 10 : 12;
  return { positions, width, height: height + lane, floorY: height - PAD };
}

function drawDiagram(diagram) {
  const svg = $('diagram');
  svg.innerHTML = '';
  state.nodes.clear();

  const { nodes, edges } = diagram;
  if (!nodes.length) return;
  const gap = columnGap(edges);
  const { positions, width, height, floorY } = layout(nodes, edges, gap);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  const defs = svgEl('defs');
  const marker = svgEl('marker', {
    id: 'arrow', viewBox: '0 0 10 10', refX: '9', refY: '5',
    markerWidth: '6', markerHeight: '6', orient: 'auto-start-reverse',
  });
  marker.appendChild(svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#AEBCCC' }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  // Edges first, so nodes sit on top of them.
  //
  // Labels are the fiddly part: a fan-out puts every edge's midpoint at nearly
  // the same spot, so midpoint labels pile into an unreadable stack. Placing
  // each label near its *target* spreads them out by the thing that actually
  // differs - which branch they name. Return edges get a per-edge stagger for
  // the same reason, since they all dip under the same pair of boxes.
  let returnIndex = 0;
  for (const e of edges) {
    const a = positions.get(e.source), b = positions.get(e.target);
    if (!a || !b) continue;
    const backwards = b.x <= a.x;
    const x1 = backwards ? a.x : a.x + NODE_W, y1 = a.y + NODE_H / 2;
    const x2 = backwards ? b.x + NODE_W : b.x,  y2 = b.y + NODE_H / 2;

    let d, labelX, labelY;
    if (backwards) {
      const stagger = (returnIndex++ % 3) * 15;
      const dip = floorY + 16 + stagger;
      d = `M ${x1} ${y1} C ${x1 - 34} ${dip}, ${x2 + 34} ${dip}, ${x2} ${y2}`;
      labelX = (x1 + x2) / 2;
      // Lowest point of this cubic is at t=0.5, which is not `dip` itself -
      // the control points pull the curve only three quarters of the way down.
      labelY = (y1 + y2 + 6 * dip) / 8 + 10;
    } else {
      const mid = (x1 + x2) / 2;
      d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
      // For this cubic, t=0.5 lands exactly at (mid, midpoint of y) - the
      // centre of the gap the column width was sized for.
      labelX = mid;
      labelY = (y1 + y2) / 2 - 6;
    }

    const cls = 'edge-line' + (e.style === 'dashed' ? ' edge-dashed' : e.style === 'loop' ? ' edge-loop' : '');
    svg.appendChild(svgEl('path', { d, class: cls }));

    if (e.label) {
      const label = svgEl('text', {
        x: labelX, y: labelY, class: 'edge-label', 'text-anchor': 'middle',
      });
      label.textContent = e.label;
      svg.appendChild(label);
    }
  }

  for (const n of nodes) {
    const p = positions.get(n.id);
    const group = svgEl('g', { class: 'node' });
    group.appendChild(svgEl('rect', {
      x: p.x, y: p.y, width: NODE_W, height: NODE_H, rx: 8,
      class: `node-box node-${n.kind}`,
    }));
    const kind = svgEl('text', { x: p.x + 10, y: p.y + 16, class: 'node-kind' });
    kind.textContent = n.kind.toUpperCase();
    group.appendChild(kind);

    const label = svgEl('text', { x: p.x + 10, y: p.y + 32, class: 'node-label' });
    label.textContent = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label;
    const title = svgEl('title');
    title.textContent = n.label;
    label.appendChild(title);
    group.appendChild(label);

    svg.appendChild(group);
    state.nodes.set(n.id, group);
  }
}

function markNode(id, status) {
  const group = state.nodes.get(id);
  if (!group) return;
  group.classList.remove('is-active', 'is-done');
  if (status === 'active') group.classList.add('is-active');
  if (status === 'done') group.classList.add('is-done');
}

function clearNodes() {
  state.nodes.forEach((g) => g.classList.remove('is-active', 'is-done'));
}

/* ── run ──────────────────────────────────────────────────────── */

function wireControls() {
  $('runBtn').addEventListener('click', run);
  $('resetBtn').addEventListener('click', async () => {
    await fetch('/api/reset', { method: 'POST' });
    resetConsole();
    clearNodes();
    loadStore();
    loadAudit();
  });

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-on', t === tab));
      document.querySelectorAll('.panel').forEach((p) =>
        p.classList.toggle('is-on', p.id === 'panel-' + tab.dataset.tab));
      if (tab.dataset.tab === 'traces') renderTraces();
      if (tab.dataset.tab === 'audit') loadAudit();
      if (tab.dataset.tab === 'store') loadStore();
      if (tab.dataset.tab === 'devui') refreshDevui();
    });
  });

  $('tracePlumbing').addEventListener('change', renderTraces);
  $('approveBtn').addEventListener('click', () => answerApproval('approve'));
  $('rejectBtn').addEventListener('click', () => answerApproval('reject'));
}

async function run() {
  if (!state.current) return;
  stopRun();
  clearNodes();
  resetConsole();

  state.traces = [];
  renderTraces();
  $('runBtn').disabled = true;
  $('runState').textContent = 'running…';

  const response = await fetch('/api/run/' + state.current.slug, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: $('prompt').value }),
  });
  if (!response.ok) {
    logLine('error', 'ui', 'could not start the run');
    $('runBtn').disabled = false;
    return;
  }
  const { runId } = await response.json();
  state.runId = runId;

  const source = new EventSource('/api/stream/' + runId);
  state.source = source;
  source.onmessage = (event) => handleFrame(JSON.parse(event.data));
  source.onerror = () => { stopRun(); $('runState').textContent = 'disconnected'; };
}

function handleFrame(frame) {
  switch (frame.kind) {
    case 'start':
      logLine('info', 'run', `pattern "${frame.pattern}" started`);
      break;
    case 'log':
      logLine(frame.level, frame.source, frame.message, frame.t);
      break;
    case 'node':
      markNode(frame.node, frame.state);
      break;
    case 'token':
      appendToken(frame.source, frame.text);
      break;
    case 'output':
      logOutput(frame.source, frame.text, frame.t);
      break;
    case 'approval':
      showApproval(frame);
      break;
    case 'approvalResolved':
      hideApproval();
      logLine('info', 'request_info', `resolved: ${frame.decision}`);
      break;
    case 'traces':
      state.traces = frame.rows || [];
      renderTraces();
      break;
    case 'audit':
      renderAudit(frame.rows);
      break;
    case 'end':
      $('runState').textContent = 'complete';
      $('runBtn').disabled = false;
      stopRun();
      loadStore();
      break;
  }
}

function stopRun() {
  if (state.source) { state.source.close(); state.source = null; }
  $('runBtn').disabled = false;
  hideApproval();
}

/* ── console ──────────────────────────────────────────────────── */

let tokenLine = null;

function resetConsole() {
  const box = $('console');
  box.innerHTML = '';
  tokenLine = null;
  $('runState').textContent = 'idle';
  box.appendChild(el('p', 'console-empty',
    'Press Run pattern to stream this workflow\'s events.'));
}

function consoleBox() {
  const box = $('console');
  const empty = box.querySelector('.console-empty');
  if (empty) empty.remove();
  return box;
}

function logLine(level, source, message, t) {
  const box = consoleBox();
  tokenLine = null;
  const line = el('div', `line line-${level}`);
  line.appendChild(el('span', 't', t !== undefined ? t.toFixed(1) + 's' : ''));
  line.appendChild(el('span', 'src', source));
  line.appendChild(el('span', 'msg', message));
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function logOutput(source, text, t) {
  const box = consoleBox();
  tokenLine = null;
  const line = el('div', 'line line-output');
  line.appendChild(el('span', 't', t !== undefined ? t.toFixed(1) + 's' : ''));
  line.appendChild(el('span', 'src', source));
  line.appendChild(el('span', 'msg', text));
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

/** Streamed tokens coalesce into one growing line, the way a chat UI would. */
function appendToken(source, text) {
  const box = consoleBox();
  if (!tokenLine || tokenLine.dataset.src !== source) {
    tokenLine = el('div', 'line line-token');
    tokenLine.dataset.src = source;
    tokenLine.appendChild(el('span', 't', ''));
    tokenLine.appendChild(el('span', 'src', source));
    tokenLine.appendChild(el('span', 'msg', ''));
    box.appendChild(tokenLine);
  }
  tokenLine.querySelector('.msg').textContent += text;
  box.scrollTop = box.scrollHeight;
}

/* ── approval modal ───────────────────────────────────────────── */

let pendingRequestId = null;

function showApproval(frame) {
  pendingRequestId = frame.requestId;
  $('approvalProposal').textContent = frame.proposal || '(no proposal text)';

  const facts = $('approvalFacts');
  facts.innerHTML = '';
  const labels = {
    shipment: 'Shipment', customer: 'Customer', tier: 'Tier',
    declaredValueEur: 'Declared value', approvalThresholdEur: 'Approval threshold',
    goodwillCeilingEur: 'Goodwill ceiling',
  };
  for (const [key, label] of Object.entries(labels)) {
    const value = frame.context?.[key];
    if (value === undefined || value === null) continue;
    facts.appendChild(el('dt', null, label));
    facts.appendChild(el('dd', null,
      key.endsWith('Eur') ? 'EUR ' + Number(value).toLocaleString('en-GB') : String(value)));
  }

  $('approvalModal').hidden = false;

  let remaining = frame.timeoutSeconds || 90;
  const timer = $('approvalTimer');
  timer.textContent = `auto-approves in ${remaining}s`;
  clearInterval(state.approvalTimer);
  state.approvalTimer = setInterval(() => {
    remaining -= 1;
    timer.textContent = remaining > 0 ? `auto-approves in ${remaining}s` : 'auto-approving…';
    if (remaining <= 0) clearInterval(state.approvalTimer);
  }, 1000);
}

function hideApproval() {
  $('approvalModal').hidden = true;
  pendingRequestId = null;
  clearInterval(state.approvalTimer);
}

async function answerApproval(decision) {
  if (!pendingRequestId || !state.runId) return;
  await fetch('/api/approve/' + state.runId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requestId: pendingRequestId, decision }),
  });
  hideApproval();
}

/* ── traces ───────────────────────────────────────────────────── */

/**
 * Waterfall of the run's OpenTelemetry spans.
 *
 * This is the framework's own view of the run - the same substance DevUI's
 * trace panel shows - rendered here because DevUI can only display runs it
 * started itself. On the concurrent pattern the overlapping bars are the point:
 * they are what parallelism actually looks like.
 */
function renderTraces() {
  const host = $('waterfall');
  const note = $('tracesNote');
  const showPlumbing = $('tracePlumbing').checked;
  host.innerHTML = '';

  const all = state.traces || [];
  if (!all.length) {
    note.textContent = 'Run a pattern to capture its spans.';
    host.appendChild(el('p', 'wf-empty', 'No spans yet — press Run pattern.'));
    return;
  }

  const rows = showPlumbing ? all : all.filter((s) => s.kind !== 'plumbing');
  const span = Math.max(...all.map((s) => s.offsetMs + s.durationMs), 1);
  const hidden = all.length - rows.length;
  note.textContent =
    `${rows.length} span${rows.length === 1 ? '' : 's'} over ${span.toFixed(1)}ms` +
    (hidden ? ` · ${hidden} edge/message span${hidden === 1 ? '' : 's'} hidden` : '');

  for (const s of rows) {
    const row = el('div', 'wf-row');
    row.appendChild(el('span', `wf-kind wf-${s.kind}`, s.kind));

    const track = el('div', 'wf-track');
    const bar = el('div', `wf-bar is-${s.kind}`);
    // Percentages, so the waterfall scales with the panel rather than a fixed px width.
    bar.style.left = `${(s.offsetMs / span) * 100}%`;
    bar.style.width = `${Math.max((s.durationMs / span) * 100, 0.6)}%`;
    track.appendChild(bar);
    track.appendChild(el('div', 'wf-label', s.subject || s.name));
    track.title = `${s.name}  +${s.offsetMs}ms  ${s.durationMs}ms`;
    row.appendChild(track);

    row.appendChild(el('span', 'wf-ms', `${s.durationMs.toFixed(1)}ms`));
    host.appendChild(row);
  }
}

/* ── devui ────────────────────────────────────────────────────── */

async function refreshDevui() {
  try {
    const data = await (await fetch('/api/devui/entities')).json();
    state.devui = data;
    $('devuiState').textContent = data.available
      ? `DevUI connected at ${data.devuiUrl} — showing this pattern's workflow graph and raw events`
      : `DevUI is not running. Start it with:  python -m chaos_to_symphony.devui_app`;
    if (state.current) pointDevuiAt(state.current.slug);
  } catch {
    $('devuiState').textContent = 'DevUI is not reachable.';
  }
}

function pointDevuiAt(slug) {
  const frame = $('devuiFrame');
  const entity = state.devui.entities?.[slug];
  const base = state.devui.devuiUrl || '';
  if (!state.devui.available || !base) { frame.removeAttribute('src'); return; }
  const url = entity ? `${base}/?entity_id=${encodeURIComponent(entity)}` : base;
  if (frame.getAttribute('src') !== url) frame.setAttribute('src', url);
  $('devuiOpen').href = url;
}

/* ── tables ───────────────────────────────────────────────────── */

async function loadAudit() {
  const data = await (await fetch('/api/audit')).json();
  renderAudit(data.rows);
}

function renderAudit(rows) {
  const body = $('auditTable').querySelector('tbody');
  body.innerHTML = '';
  if (!rows?.length) {
    const tr = el('tr');
    const td = el('td', null, 'No audit entries yet — run a pattern.');
    td.colSpan = 4;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const tr = el('tr');
    tr.appendChild(el('td', null, row.at.slice(11, 19)));
    tr.appendChild(el('td', null, row.actor));
    tr.appendChild(el('td', null, row.action));
    tr.appendChild(el('td', null, row.detail));
    body.appendChild(tr);
  }
}

async function loadStore() {
  const data = await (await fetch('/api/store')).json();
  $('storeNote').textContent =
    `${data.shipments} shipments, ${data.customers} customers, ${data.tariffLines} tariff lines, ` +
    `${data.openExceptions} open exceptions — all held in process memory. No database.`;
  const body = $('storeTable').querySelector('tbody');
  body.innerHTML = '';
  for (const row of data.rows) {
    const tr = el('tr');
    tr.appendChild(el('td', null, row.id));
    tr.appendChild(el('td', null, row.lane));
    tr.appendChild(el('td', null, row.goods));
    tr.appendChild(el('td', null, row.exception));
    tr.appendChild(el('td', null, row.severity));
    const value = el('td', 'right', 'EUR ' + row.valueEur.toLocaleString('en-GB'));
    tr.appendChild(value);
    body.appendChild(tr);
  }
}

boot();
