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
  domain: null,     // the briefing, fetched once and kept
};

/* ── boot ─────────────────────────────────────────────────────── */

async function boot() {
  const data = await (await fetch('/api/patterns')).json();
  state.patterns = data.patterns;
  state.tiers = data.tiers;

  paintProviderPill(data);

  $('devuiLink').href = data.devuiUrl;
  $('devuiOpen').href = data.devuiUrl;

  buildRail();
  wireControls();
  wireSettings();
  wireStage();
  wireDomain();

  // Deep-link support, so a slide can point straight at one pattern.
  const wanted = new URLSearchParams(location.search).get('pattern');
  select(state.patterns.find((p) => p.slug === wanted) || state.patterns[0]);

  refreshDevui();
  loadStore();
  loadDomain();
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
  fillCase(pattern.case);
  $('prompt').value = pattern.defaultPrompt;

  const tier = $('pTier');
  tier.textContent = pattern.newThisYear ? 'new this year' : pattern.tier;
  tier.className = 'badge badge-' + pattern.tier;

  fillList($('pUse'), pattern.useWhen);
  fillList($('pAvoid'), pattern.avoidWhen);
  fillList($('pApi'), pattern.mafApi);
  fillEndings(pattern.promptExamples || []);
  // The agents panel is per pattern, so refresh it if it is the one on screen.
  if ($('panel-agents').classList.contains('is-on')) loadAgents();
  // Same for the stage header, if someone switches pattern while it is up.
  if (stageIsOpen()) {
    $('stageNumber').textContent = String(pattern.number).padStart(2, '0');
    $('stageName').textContent = pattern.name;
    $('stageTagline').textContent = pattern.tagline;
  }

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

/**
 * The business case, for an audience that has never seen a travel desk.
 *
 * Every pattern carries one, so the card is never empty - but guard anyway,
 * because an older API response without `case` should degrade to a hidden card
 * rather than a page of `undefined`.
 */
function fillCase(brief) {
  const card = document.querySelector('.card-case');
  if (!brief) { card.hidden = true; return; }
  card.hidden = false;

  $('caseAbout').textContent = brief.about;
  $('caseWhy').textContent = brief.why;

  const facts = $('caseFacts');
  facts.innerHTML = '';
  for (const fact of brief.facts || []) {
    facts.appendChild(el('dt', null, fact.label));
    facts.appendChild(el('dd', null, fact.value));
  }
}

/**
 * One prompt per way this pattern can finish.
 *
 * Only the branching patterns carry these. The other eight end one way
 * whatever you type, and an "endings" box on those would imply a choice that
 * does not exist - so the box is hidden rather than shown empty.
 */
function fillEndings(examples) {
  const box = $('endings');
  const list = $('endingsList');
  list.innerHTML = '';
  box.hidden = examples.length === 0;
  if (box.hidden) return;

  for (const ex of examples) {
    const btn = el('button', 'ending-btn');
    btn.type = 'button';
    btn.title = 'Load this prompt';
    btn.appendChild(el('span', 'ending-name', '→ ' + ex.ending));
    btn.appendChild(el('span', 'ending-prompt', ex.prompt));
    if (ex.why) btn.appendChild(el('span', 'ending-why', ex.why));
    btn.addEventListener('click', () => {
      $('prompt').value = ex.prompt;
      $('prompt').focus();
      // Setting .value in script fires no input event, so say so explicitly.
      invalidateRunView();
    });
    const row = el('li');
    row.appendChild(btn);
    list.appendChild(row);
  }
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
      if (tab.dataset.tab === 'agents') loadAgents();
      if (tab.dataset.tab === 'traces') renderTraces();
      if (tab.dataset.tab === 'audit') loadAudit();
      if (tab.dataset.tab === 'store') loadStore();
      if (tab.dataset.tab === 'devui') refreshDevui();
    });
  });

  $('prompt').addEventListener('input', invalidateRunView);
  $('tracePlumbing').addEventListener('change', renderTraces);
  $('approveBtn').addEventListener('click', () => answerApproval('approve'));
  $('rejectBtn').addEventListener('click', () => answerApproval('reject'));
}

async function run() {
  if (!state.current) return;
  stopRun();
  clearNodes();
  resetConsole();
  approvalRound = 0;

  state.traces = [];
  renderTraces();
  $('runBtn').disabled = true;
  setRunState('running…');

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
  source.onerror = () => { stopRun(); setRunState('disconnected'); };
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
      appendToken(frame.source, frame.text, frame.t);
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
      setRunState('complete');
      $('runBtn').disabled = false;
      stopRun();
      loadStore();
      break;
  }
}

/**
 * Drop the last run's result once the prompt no longer matches it.
 *
 * The diagram and the run state describe *the run that happened*. Change the
 * prompt and they are answering a question nobody asked any more - and a green
 * branch is read as this prompt's answer, not the previous one's. Loading an
 * example made that easy to hit: one click swaps the prompt and leaves the
 * previous run lit, so picking "DUTY DESK" after running the
 * standard-queue case shows standard_queue in green above it.
 *
 * The log is left alone. It names its own booking in every line, it is the
 * record of what happened, and wiping it on a keystroke would throw away
 * something worth reading. The next run clears it anyway.
 */
function invalidateRunView() {
  if (state.source) return;   // a run is in flight - it owns the diagram
  clearNodes();
  setRunState('prompt changed');
}

function stopRun() {
  if (state.source) { state.source.close(); state.source = null; }
  $('runBtn').disabled = false;
  hideApproval();
}

/* ── console ─────────────────────────────────────────────────── */

/**
 * The open streaming block, or null.
 *
 * Agents stream: every delta arrives as its own frame. Rendering one row per
 * frame gave a hundred timestamped lines for a single paragraph once a real
 * model was answering - offline the same text arrived in about a dozen chunks,
 * which is why it looked fine. Consecutive frames of the same kind from the
 * same source now grow one block, so a reply reads as a reply and still
 * types itself out live.
 */
let block = null;

function resetConsole() {
  const box = $('console');
  box.innerHTML = '';
  block = null;
  setRunState('idle');
  box.appendChild(el('p', 'console-empty',
    'Press Run pattern to stream this workflow\'s events.'));
}

function consoleBox() {
  const box = $('console');
  const empty = box.querySelector('.console-empty');
  if (empty) empty.remove();
  return box;
}

function stamp(t) {
  return t !== undefined ? t.toFixed(1) + 's' : '';
}

/** A discrete event: always its own row, and it closes any open block. */
function logLine(level, source, message, t) {
  const box = consoleBox();
  block = null;
  const line = el('div', `line line-${level}`);
  line.appendChild(el('span', 't', stamp(t)));
  line.appendChild(el('span', 'src', source));
  line.appendChild(el('span', 'msg', message));
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

/** Streamed text: appends to the open block, or starts one. */
function appendStream(kind, source, text, t) {
  const box = consoleBox();
  if (!block || block.kind !== kind || block.source !== source) {
    const line = el('div', `line line-${kind}`);
    line.appendChild(el('span', 't', stamp(t)));   // when this block started
    line.appendChild(el('span', 'src', source));
    const msg = el('span', 'msg', '');
    line.appendChild(msg);
    box.appendChild(line);
    block = { kind, source, msg };
  }
  block.msg.textContent += text;
  box.scrollTop = box.scrollHeight;
}

function logOutput(source, text, t) {
  appendStream('output', source, text, t);
}

function appendToken(source, text, t) {
  appendStream('token', source, text, t);
}

/* ── approval modal ───────────────────────────────────────────── */

let pendingRequestId = null;

/**
 * How many times this run has stopped at the gate.
 *
 * A send-back re-runs the gated agent and suspends again, so the modal closes
 * and reopens within a second or so. Without saying which round this is, that
 * reads as a button that did nothing - which is exactly the wrong lesson for
 * the one pattern whose whole point is that the human's answer changes the run.
 */
let approvalRound = 0;

function showApproval(frame) {
  pendingRequestId = frame.requestId;
  approvalRound += 1;
  $('approvalProposal').textContent = frame.proposal || '(no proposal text)';

  const round = $('approvalRound');
  round.hidden = approvalRound < 2;
  if (!round.hidden) {
    round.textContent =
      `Round ${approvalRound}. You sent the last proposal back; the settlement agent re-priced it ` +
      `and the gate suspended again.`;
  }

  const facts = $('approvalFacts');
  facts.innerHTML = '';
  const labels = {
    booking: 'Booking', customer: 'Customer', tier: 'Tier',
    packagePriceEur: 'Package price', approvalThresholdEur: 'Approval threshold',
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
    `${data.bookings} bookings, ${data.customers} customers, ${data.invoices} supplier invoices, ` +
    `${data.openIncidents} open incidents — all held in process memory. No database.`;
  const body = $('storeTable').querySelector('tbody');
  body.innerHTML = '';
  for (const row of data.rows) {
    const tr = el('tr');
    tr.appendChild(el('td', null, row.id));
    tr.appendChild(el('td', null, row.trip));
    tr.appendChild(el('td', null, row.route));
    tr.appendChild(el('td', null, row.incident));
    tr.appendChild(el('td', null, row.severity));
    const value = el('td', 'right', 'EUR ' + row.valueEur.toLocaleString('en-GB'));
    tr.appendChild(value);
    body.appendChild(tr);
  }
}

/* ── stage ────────────────────────────────────────────────────── */

/**
 * Full-screen log and diagram, for the people at the back of the room.
 *
 * The panes are not copies. The real #console and .diagram-wrap are *moved*
 * into the stage and put back on exit, so a run already in flight keeps
 * streaming into the same elements and the diagram keeps lighting up without
 * any of it knowing where it is being displayed. Copies would need syncing,
 * and a diagram that disagrees with itself is worse than no diagram.
 */
const stageHomes = new Map();

function moveToStage(el, host) {
  if (!stageHomes.has(el)) stageHomes.set(el, { parent: el.parentNode, next: el.nextSibling });
  host.appendChild(el);
}

function sendHome(el) {
  const home = stageHomes.get(el);
  if (home) home.parent.insertBefore(el, home.next);
}

function stageIsOpen() {
  return !$('stage').hidden;
}

function enterStage() {
  if (!state.current || stageIsOpen()) return;

  $('stageNumber').textContent = String(state.current.number).padStart(2, '0');
  $('stageName').textContent = state.current.name;
  $('stageTagline').textContent = state.current.tagline;
  $('stageState').textContent = $('runState').textContent;

  moveToStage($('console'), $('stageLog'));
  moveToStage(document.querySelector('.diagram-wrap'), $('stageDiagram'));

  $('stage').hidden = false;
  $('stageExit').focus();
  // Keep the page behind from scrolling under the overlay.
  document.body.style.overflow = 'hidden';
}

function exitStage() {
  if (!stageIsOpen()) return;
  $('stage').hidden = true;
  document.body.style.overflow = '';
  sendHome($('console'));
  sendHome(document.querySelector('.diagram-wrap'));
  $('stageBtn').focus();
}

function wireStage() {
  $('stageBtn').addEventListener('click', enterStage);
  $('stageExit').addEventListener('click', exitStage);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && stageIsOpen()) exitStage();
  });
}

/** Mirror the run state into the stage header, which has its own copy of it. */
function setRunState(text) {
  $('runState').textContent = text;
  $('stageState').textContent = text;
}

/* ── provider settings ────────────────────────────────────────── */

/**
 * The badge that says what the demos are really running against.
 *
 * Driven by the *effective* provider rather than the configured one, so it can
 * never claim a live model that quietly fell back to the scripted client -
 * which is the failure this whole panel exists to make visible.
 */
function paintProviderPill(status) {
  const pill = $('providerPill');
  pill.classList.remove('pill-warn', 'pill-offline', 'pill-live');
  if (status.note || status.providerNote) {
    pill.textContent = `${status.requestedProvider} unavailable · offline`;
    pill.classList.add('pill-warn');
    pill.title = status.note || status.providerNote;
  } else if (status.offline) {
    pill.textContent = 'offline · no keys needed';
    pill.classList.add('pill-offline');
    pill.title = 'Agents run against a deterministic scripted client. No model is called.';
  } else {
    pill.textContent = `live · ${status.provider}`;
    pill.classList.add('pill-live');
    pill.title = 'Agents are calling a real model.';
  }
}

function wireSettings() {
  $('settingsBtn').addEventListener('click', openSettings);
  $('cfgClose').addEventListener('click', () => { $('settingsModal').hidden = true; });
  $('cfgProvider').addEventListener('change', () => {
    $('foundryFields').hidden = $('cfgProvider').value !== 'foundry';
  });
  $('settingsForm').addEventListener('submit', (event) => {
    event.preventDefault();
    sendSettings('PUT', {
      provider: $('cfgProvider').value,
      foundryProjectEndpoint: $('cfgEndpoint').value,
      foundryModel: $('cfgModel').value,
    });
  });
  $('cfgReset').addEventListener('click', () => sendSettings('DELETE', null));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('settingsModal').hidden) $('settingsModal').hidden = true;
  });
}

async function openSettings() {
  $('settingsError').hidden = true;
  $('settingsModal').hidden = false;
  try {
    paintSettings(await (await fetch('/api/config')).json());
  } catch (err) {
    showSettingsError(`Could not read the settings (${err.message}).`);
  }
}

function paintSettings(data) {
  const values = data.values || {};
  const provider = (values.CHAOS_PROVIDER || 'offline').toLowerCase();
  const selectable = data.selectableProviders || [];

  $('cfgProvider').value = selectable.includes(provider) ? provider : 'offline';
  $('cfgEndpoint').value = values.FOUNDRY_PROJECT_ENDPOINT || '';
  $('cfgModel').value = values.FOUNDRY_MODEL || '';
  $('foundryFields').hidden = $('cfgProvider').value !== 'foundry';

  const status = data.status || {};
  const box = $('settingsStatus');
  box.classList.remove('is-live', 'is-warn');
  if (status.note) {
    box.classList.add('is-warn');
    box.textContent = status.note;
  } else if (status.offline) {
    box.textContent = `Running offline on ${status.client}. No model is called.`;
  } else {
    box.classList.add('is-live');
    box.textContent = `Live on ${status.provider} via ${status.client}` +
      (status.baseUrl ? ` — ${status.baseUrl}` : '') + '.';
  }

  // Say where each value came from, so "why is it still offline" is answerable
  // without reading the deploy script.
  const sources = data.sources || {};
  const saved = Object.values(sources).some((s) => s === 'saved');
  const parts = [];
  parts.push(saved
    ? `Saved settings are in use, from ${data.configPath}.`
    : `No saved settings — these values come from the environment the app was started with.`);
  if (!data.writable) parts.push('Read-only here (CHAOS_CONFIG_API=0).');
  parts.push('Patterns pick this up on their next run. DevUI builds its workflows at start-up, ' +
             'so restart it to change what it uses.');
  $('settingsFoot').textContent = parts.join(' ');

  const disabled = data.writable === false;
  $('cfgSave').disabled = disabled;
  $('cfgReset').disabled = disabled;
}

async function sendSettings(method, body) {
  $('settingsError').hidden = true;
  $('cfgSave').disabled = true;
  try {
    const response = await fetch('/api/config', {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    paintSettings(data);
    paintProviderPill(data.status || {});
    if (data.persisted === false) {
      showSettingsError('Applied for this process, but the settings file could not be written — ' +
                        'it will not survive a restart.');
    }
    // The agent cards name the client each agent drives, so they are now stale.
    if ($('panel-agents').classList.contains('is-on')) loadAgents();
  } catch (err) {
    showSettingsError(err.message);
  } finally {
    $('cfgSave').disabled = false;
  }
}

function showSettingsError(message) {
  const box = $('settingsError');
  box.textContent = message;
  box.hidden = false;
}

/* ── agents ───────────────────────────────────────────────────── */

/**
 * The prompt and tools behind each box in the diagram.
 *
 * Fetched per pattern rather than shipped with the catalogue, because reading
 * it means building the workflow - and it only matters when someone opens the
 * tab. The server caches per slug, so switching back and forth is free.
 *
 * Nothing here is written down twice: it is read out of the Agent objects the
 * pattern module built, which is why the handoff tools show up at all. They do
 * not exist in the source - HandoffBuilder generates one per permitted edge.
 */
async function loadAgents() {
  if (!state.current) return;
  const host = $('agentsList');
  const note = $('agentsNote');
  const slug = state.current.slug;

  host.innerHTML = '';
  note.textContent = 'Reading the built workflow…';

  let data;
  try {
    const response = await fetch('/api/agents/' + slug);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    data = await response.json();
  } catch (err) {
    note.textContent = `Could not read the agents for this pattern (${err.message}).`;
    return;
  }
  if (slug !== state.current.slug) return;   // the user moved on while we fetched

  const agents = data.agents || [];
  if (!agents.length) {
    note.textContent =
      'This pattern has no agents — it is built from plain executors, which is rather the point.';
    return;
  }
  const live = agents.some((a) => a.client !== 'ScriptedChatClient');
  note.textContent =
    `${agents.length} agent${agents.length === 1 ? '' : 's'} in ${data.pattern}, read back out of the ` +
    `built workflow — prompts, tools and the client each one drives` +
    (live ? '.' : '. Offline, so every client is the scripted one.');

  for (const agent of agents) host.appendChild(agentCard(agent));
}

function agentCard(agent) {
  const card = el('div', 'agent-card');

  const head = el('div', 'agent-head');
  head.appendChild(el('span', 'agent-name', agent.name));
  if (agent.description) head.appendChild(el('span', 'agent-role', agent.description));
  head.appendChild(el('span',
    'agent-tag ' + (agent.client === 'ScriptedChatClient' ? 'agent-tag-offline' : 'agent-tag-live'),
    agent.client));
  if (agent.structuredOutput) {
    head.appendChild(el('span', 'agent-tag agent-tag-schema', '→ ' + agent.structuredOutput));
  }
  if (agent.nested) {
    head.appendChild(el('span', 'agent-tag agent-tag-nested', 'nested workflow'));
  }
  card.appendChild(head);

  const prompt = el('div', 'agent-section');
  prompt.appendChild(el('h5', null, 'System prompt'));
  prompt.appendChild(el('pre', 'agent-prompt', agent.instructions || '(none set)'));
  card.appendChild(prompt);

  const tools = el('div', 'agent-section');
  tools.appendChild(el('h5', null, `Tools (${agent.tools.length})`));
  if (!agent.tools.length) {
    tools.appendChild(el('p', 'agent-none', 'No tools — this agent works from the conversation alone.'));
  } else {
    const list = el('ul', 'agent-tools');
    for (const tool of agent.tools) {
      const row = el('li', 'agent-tool' + (tool.kind === 'handoff' ? ' agent-tool-handoff' : ''));
      row.appendChild(el('code', null, tool.name));
      if (tool.description) row.appendChild(el('span', 'agent-tool-desc', tool.description));
      list.appendChild(row);
    }
    tools.appendChild(list);
  }
  card.appendChild(tools);

  return card;
}

/* ── the domain briefing ──────────────────────────────────────────
   Twelve patterns against one story. An attendee who does not know what a
   travel incident is cannot follow any of the twelve, so the briefing is one
   click away from the topbar and from every pattern's own case card.

   It deliberately does NOT open itself. A modal over the page on first load
   swallows the first click on it, which breaks scripts/drive.py - and would do
   the same to a speaker presenting from a fresh browser profile, on stage, in
   front of the people this briefing is for. Instead the button glows until it
   has been opened once, which says "start here" without taking the page away.
   ──────────────────────────────────────────────────────────────── */

const DOMAIN_SEEN = 'chaos:domain-seen';

function wireDomain() {
  $('domainBtn').addEventListener('click', () => openDomain());
  $('caseDomainLink').addEventListener('click', () => openDomain());
  $('domainClose').addEventListener('click', closeDomain);
  $('domainModal').addEventListener('click', (event) => {
    if (event.target === $('domainModal')) closeDomain();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('domainModal').hidden) closeDomain();
  });
}

async function loadDomain() {
  try {
    state.domain = await (await fetch('/api/domain')).json();
  } catch {
    return;                       // the button just stays quiet
  }
  paintDomain(state.domain);
  if (!domainSeen()) $('domainBtn').classList.add('is-fresh');
}

/** Storage can throw in a locked-down browser; a highlight is not worth a broken page. */
function domainSeen() {
  try { return localStorage.getItem(DOMAIN_SEEN) === '1'; } catch { return true; }
}

function openDomain() {
  if (!state.domain) return;
  $('domainModal').hidden = false;
  $('domainClose').focus();
  $('domainBtn').classList.remove('is-fresh');
  try { localStorage.setItem(DOMAIN_SEEN, '1'); } catch { /* private mode */ }
}

function closeDomain() {
  $('domainModal').hidden = true;
}

function paintDomain(brief) {
  $('domainTitle').textContent = brief.name;
  $('domainTagline').textContent = brief.tagline;
  $('domainFoot').textContent = brief.footnote;

  const story = $('domainStory');
  story.innerHTML = '';
  for (const para of brief.story) story.appendChild(el('p', null, para));

  const facts = $('domainFacts');
  facts.innerHTML = '';
  for (const fact of brief.facts) {
    const tile = el('div', 'domain-fact');
    tile.appendChild(el('span', 'domain-fact-value', fact.value));
    tile.appendChild(el('span', 'domain-fact-label', fact.label));
    facts.appendChild(tile);
  }

  const groups = $('domainGroups');
  groups.innerHTML = '';
  for (const group of brief.groups) {
    const box = el('section', 'domain-group');
    box.appendChild(el('h4', null, group.title));
    if (group.blurb) box.appendChild(el('p', 'domain-group-blurb', group.blurb));
    const list = el('dl', 'domain-terms');
    for (const term of group.terms) {
      list.appendChild(el('dt', null, term.term));
      list.appendChild(el('dd', null, term.meaning));
    }
    box.appendChild(list);
    groups.appendChild(box);
  }
}

boot();
