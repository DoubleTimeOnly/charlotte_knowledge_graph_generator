/* Charlotte Knowledge Graph — D3.js frontend
 *
 * State machine:
 *   HOME ──[explore]──► LOADING ──[success]──► GRAPH
 *                                └─[failure]──► HOME (error)
 *   GRAPH ──[node click]──► GRAPH + panel loading ──► GRAPH + panel content
 *         ──[expand]──────► GRAPH + node spinner ──► GRAPH (merged)
 *         ──[search]──────► GRAPH (searchlight)
 */

'use strict';

// ── Design tokens ─────────────────────────────────────────────────────────────

const NODE_COLORS = {
  Person:       '#0F7075',
  Event:        '#C2581A',
  Concept:      '#6B4FA0',
  Organization: '#1A7A4A',
  Document:     '#B45309',
};

const TYPE_BADGE_COLORS = NODE_COLORS;

// ── Custom hexagon symbol for D3 ──────────────────────────────────────────────
const symbolHexagon = {
  draw(context, size) {
    const r = Math.sqrt(size / (Math.PI * 0.8));
    for (let i = 0; i < 6; i++) {
      const angle = (i * Math.PI) / 3 - Math.PI / 6;
      if (i === 0) context.moveTo(r * Math.cos(angle), r * Math.sin(angle));
      else         context.lineTo(r * Math.cos(angle), r * Math.sin(angle));
    }
    context.closePath();
  },
};

const SYMBOL_MAP = {
  Person:       d3.symbolCircle,
  Event:        d3.symbolDiamond,
  Concept:      symbolHexagon,
  Organization: d3.symbolSquare,
  Document:     d3.symbolTriangle,
};

// ── App state ─────────────────────────────────────────────────────────────────

let graphData   = { nodes: [], edges: [], topic: '' };
let selectedId  = null;
let expandingId = null;
let simulation  = null;
let svgG        = null;       // zoomable <g> inside <svg>
let linkSel     = null;
let nodeSel     = null;
let labelSel    = null;
let currentTopic = '';

// ── Node sizing ───────────────────────────────────────────────────────────────

function getDegree(nodeId, edges) {
  return edges.filter(e => {
    const s = typeof e.source === 'object' ? e.source.id : e.source;
    const t = typeof e.target === 'object' ? e.target.id : e.target;
    return s === nodeId || t === nodeId;
  }).length;
}

function nodeRadius(d) {
  const deg = d._degree ?? 0;
  if (deg >= 5) return 22;
  if (deg >= 2) return 16;
  return 11;
}

function nodeSymbolSize(d) {
  const r = nodeRadius(d);
  return Math.PI * r * r * 2.5;
}

function nodePath(d) {
  const sym = SYMBOL_MAP[d.type] || d3.symbolCircle;
  return d3.symbol().type(sym).size(nodeSymbolSize(d))();
}

// ── D3 SVG setup ──────────────────────────────────────────────────────────────

function initSVG() {
  const svg = d3.select('#graph-svg');
  svg.selectAll('*').remove();

  const zoom = d3.zoom()
    .scaleExtent([0.1, 6])
    .on('zoom', (event) => { if (svgG) svgG.attr('transform', event.transform); });

  svg.call(zoom);
  svg.on('click', (event) => {
    if (event.target === svg.node() || event.target.tagName === 'svg') deselectNode();
  });

  svgG = svg.append('g').attr('class', 'graph-root');
  return svg;
}

// ── Graph render ──────────────────────────────────────────────────────────────

function renderGraph(data, preservePositions = false) {
  graphData = data;
  const svg = d3.select('#graph-svg');
  const w = svg.node().clientWidth  || 800;
  const h = svg.node().clientHeight || 600;

  // Copy node objects so D3 can mutate x/y
  const nodes = data.nodes.map(d => {
    const deg = getDegree(d.id, data.edges);
    return { ...d, _degree: deg };
  });

  // Build old positions map for smooth expansion
  const oldPos = {};
  if (preservePositions && nodeSel) {
    nodeSel.each(d => { oldPos[d.id] = { x: d.x, y: d.y }; });
  }

  nodes.forEach(n => {
    if (oldPos[n.id]) { n.x = oldPos[n.id].x; n.y = oldPos[n.id].y; }
  });

  const links = data.edges.map(e => ({ ...e }));

  svgG.selectAll('*').remove();

  // Edges
  linkSel = svgG.append('g').attr('class', 'links')
    .selectAll('line')
    .data(links)
    .enter().append('line')
      .attr('class', 'graph-link')
      .attr('stroke', 'rgba(0,0,0,0.15)')
      .attr('stroke-width', 1.5)
      .on('mouseenter', onEdgeEnter)
      .on('mousemove',  onEdgeMove)
      .on('mouseleave', onEdgeLeave);

  // Nodes
  nodeSel = svgG.append('g').attr('class', 'nodes')
    .selectAll('path')
    .data(nodes)
    .enter().append('path')
      .attr('class', 'node-path')
      .attr('d', nodePath)
      .attr('fill', d => NODE_COLORS[d.type] || '#888')
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)
      .attr('tabindex', 0)
      .attr('role', 'button')
      .attr('aria-label', d => `${d.label}, type: ${d.type}`)
      .on('click', onNodeClick)
      .on('keydown', (event, d) => { if (event.key === 'Enter') onNodeClick(event, d); })
      .call(dragBehavior());

  // Labels (always visible, offset right of node)
  labelSel = svgG.append('g').attr('class', 'labels')
    .selectAll('text')
    .data(nodes)
    .enter().append('text')
      .attr('class', 'node-label')
      .text(d => d.label.length > 22 ? d.label.slice(0, 20) + '…' : d.label)
      .append('title').text(d => d.label);  // full label on hover for truncated

  // Tooltip on label parent
  labelSel = svgG.selectAll('text.node-label');

  // Force simulation
  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(100))
    .force('charge',    d3.forceManyBody().strength(-380))
    .force('center',    d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 38))
    .alphaDecay(0.05)
    .alphaMin(0.001);

  let ticks = 0;
  simulation.on('tick', () => {
    ticks++;
    if (ticks >= 300) { simulation.stop(); return; }
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
    labelSel.attr('transform', d => `translate(${d.x + nodeRadius(d) + 5},${d.y})`);
  });

  if (selectedId) applyHighlight(selectedId);
}

// ── Drag behaviour ────────────────────────────────────────────────────────────

function dragBehavior() {
  return d3.drag()
    .on('start', (event, d) => {
      if (!event.active) simulation?.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on('end',   (event, d) => {
      if (!event.active) simulation?.alphaTarget(0);
      d.fx = null; d.fy = null;
    });
}

// ── Edge tooltip ──────────────────────────────────────────────────────────────

const tooltip = document.getElementById('edge-tooltip');

function onEdgeEnter(event, d) {
  tooltip.textContent = d.relationship_type;
  tooltip.removeAttribute('hidden');
  positionTooltip(event);
}
function onEdgeMove(event) { positionTooltip(event); }
function onEdgeLeave()     { tooltip.setAttribute('hidden', ''); }

function positionTooltip(event) {
  tooltip.style.left = event.clientX + 'px';
  tooltip.style.top  = (event.clientY - 36) + 'px';
}

// ── Node interaction ──────────────────────────────────────────────────────────

function onNodeClick(event, d) {
  event.stopPropagation();
  if (selectedId === d.id) return;
  selectNode(d);
}

function selectNode(d) {
  selectedId = d.id;
  applyHighlight(d.id);
  showPanelLoading();
  loadNodeDetail(d);
}

function deselectNode() {
  selectedId = null;
  applyHighlight(null);
  showPanelDefault();
}

function applyHighlight(id) {
  if (!nodeSel) return;
  if (id === null) {
    nodeSel.style('opacity', 1);
    linkSel?.style('opacity', 0.35);
    labelSel?.style('opacity', 1);
    return;
  }
  // Dim everything, brighten selected + its neighbours
  const connected = new Set([id]);
  graphData.edges.forEach(e => {
    const s = typeof e.source === 'object' ? e.source.id : e.source;
    const t = typeof e.target === 'object' ? e.target.id : e.target;
    if (s === id) connected.add(t);
    if (t === id) connected.add(s);
  });
  nodeSel.style('opacity', d => connected.has(d.id) ? 1 : 0.2);
  linkSel?.style('opacity', d => {
    const s = typeof d.source === 'object' ? d.source.id : d.source;
    const t = typeof d.target === 'object' ? d.target.id : d.target;
    return (s === id || t === id) ? 0.8 : 0.08;
  });
  labelSel?.style('opacity', d => connected.has(d.id) ? 1 : 0.2);
}

// ── Search / searchlight ──────────────────────────────────────────────────────

document.getElementById('search-input').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!nodeSel) return;

  if (!q) {
    nodeSel.style('opacity', selectedId ? null : 1);
    linkSel?.style('opacity', 0.35);
    labelSel?.style('opacity', 1);
    if (selectedId) applyHighlight(selectedId);
    return;
  }

  const matches = new Set(
    graphData.nodes.filter(n => n.label.toLowerCase().includes(q)).map(n => n.id)
  );

  if (matches.size === 0) {
    showToast(`No nodes match "${e.target.value}"`, 'info');
  }

  nodeSel.style('opacity', d => matches.has(d.id) ? 1 : 0.08);
  linkSel?.style('opacity', d => {
    const s = typeof d.source === 'object' ? d.source.id : d.source;
    const t = typeof d.target === 'object' ? d.target.id : d.target;
    return matches.has(s) || matches.has(t) ? 0.5 : 0.04;
  });
  labelSel?.style('opacity', d => matches.has(d.id) ? 1 : 0.08);
});

// ── Panel helpers ─────────────────────────────────────────────────────────────

function showPanelDefault()  { togglePanel('default'); }
function showPanelLoading()  { togglePanel('loading'); }
function showPanelContent()  { togglePanel('content'); }
function showPanelError()    { togglePanel('error'); }

function togglePanel(state) {
  ['default', 'loading', 'content', 'error'].forEach(s => {
    const el = document.getElementById(`panel-${s}`);
    if (s === state) el.removeAttribute('hidden');
    else             el.setAttribute('hidden', '');
  });
}

// ── Node detail ───────────────────────────────────────────────────────────────

let _lastDetailNode = null;

async function loadNodeDetail(d) {
  _lastDetailNode = d;
  const contextNodes = graphData.nodes
    .filter(n => n.id !== d.id)
    .map(n => n.label)
    .slice(0, 20);

  try {
    const res = await fetch('/api/node/detail', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: d.label, node_type: d.type, context_nodes: contextNodes }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const detail = await res.json();
    renderPanel(d, detail);
  } catch (err) {
    console.error('Node detail error:', err);
    showPanelError();
  }
}

function renderPanel(d, detail) {
  const badge = document.getElementById('panel-type-badge');
  badge.textContent = d.type;
  badge.style.background = NODE_COLORS[d.type] || '#888';

  document.getElementById('panel-title').textContent = detail.label || d.label;

  const dateEl = document.getElementById('panel-date-range');
  if (detail.date_range) {
    dateEl.textContent = detail.date_range;
    dateEl.removeAttribute('hidden');
  } else {
    dateEl.setAttribute('hidden', '');
  }

  const summaryEl = document.getElementById('panel-summary');
  summaryEl.textContent = detail.summary || '';
  summaryEl.classList.remove('panel-summary--expanded');
  const existingToggle = summaryEl.nextElementSibling;
  if (existingToggle?.classList.contains('panel-summary-toggle')) existingToggle.remove();
  if ((detail.summary || '').length > 200) {
    summaryEl.classList.add('panel-summary--collapsed');
    const toggle = document.createElement('button');
    toggle.className = 'panel-summary-toggle btn-link';
    toggle.textContent = 'Show more ↓';
    toggle.addEventListener('click', () => {
      const collapsed = summaryEl.classList.toggle('panel-summary--collapsed');
      toggle.textContent = collapsed ? 'Show more ↓' : 'Show less ↑';
    });
    summaryEl.after(toggle);
  } else {
    summaryEl.classList.remove('panel-summary--collapsed');
  }

  const factsEl = document.getElementById('panel-facts');
  if (detail.key_facts?.length) {
    factsEl.innerHTML = detail.key_facts.map(f => `<li>${escHtml(f)}</li>`).join('');
    factsEl.removeAttribute('hidden');
  } else {
    factsEl.setAttribute('hidden', '');
  }

  // Connections from graph data (not from LLM — derived locally)
  const connList = document.getElementById('connections-list');
  const connSection = document.getElementById('panel-connections');
  const conns = buildConnections(d.id);
  if (conns.length) {
    connList.innerHTML = conns.map(c => `
      <li>
        <span class="conn-dot" style="background:${NODE_COLORS[c.type] || '#888'}"></span>
        <span class="conn-label" title="${escHtml(c.label)}">${escHtml(c.label)}</span>
        <span class="conn-rel">${escHtml(c.rel)}</span>
      </li>
    `).join('');
    connSection.removeAttribute('hidden');
  } else {
    connSection.setAttribute('hidden', '');
  }

  const sourcesEl = document.getElementById('panel-sources');
  if (detail.sources?.length) {
    document.getElementById('sources-text').textContent = detail.sources.join(', ');
    sourcesEl.removeAttribute('hidden');
  } else {
    sourcesEl.setAttribute('hidden', '');
  }

  showPanelContent();
}

function buildConnections(nodeId) {
  const nodeMap = Object.fromEntries(graphData.nodes.map(n => [n.id, n]));
  const conns = [];
  graphData.edges.forEach(e => {
    const s = typeof e.source === 'object' ? e.source.id : e.source;
    const t = typeof e.target === 'object' ? e.target.id : e.target;
    if (s === nodeId) {
      const target = nodeMap[t];
      if (target) conns.push({ label: target.label, type: target.type, rel: e.relationship_type });
    } else if (t === nodeId) {
      const source = nodeMap[s];
      if (source) conns.push({ label: source.label, type: source.type, rel: `← ${e.relationship_type}` });
    }
  });
  return conns;
}

// ── Node expansion ────────────────────────────────────────────────────────────

document.getElementById('expand-btn').addEventListener('click', async () => {
  if (!selectedId || expandingId) return;
  const node = graphData.nodes.find(n => n.id === selectedId);
  if (!node) return;

  expandingId = selectedId;
  document.getElementById('expand-btn').disabled = true;
  addNodeSpinner(selectedId);

  const contextNodes = graphData.nodes.map(n => n.label);
  try {
    const res = await fetch('/api/expand', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: node.id,
        node_label: node.label,
        node_type: node.type,
        context_nodes: contextNodes,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const addition = await res.json();

    // Merge into current graph (client-side dedup)
    const existingIds = new Set(graphData.nodes.map(n => n.id));
    const newNodes = (addition.nodes || []).filter(n => !existingIds.has(n.id));
    const allIds = new Set([...existingIds, ...newNodes.map(n => n.id)]);
    const newEdges = (addition.edges || []).filter(e => {
      const s = e.source; const t = e.target;
      return allIds.has(s) && allIds.has(t);
    });

    const merged = {
      nodes: [...graphData.nodes, ...newNodes],
      edges: [...graphData.edges, ...newEdges],
      topic: graphData.topic,
    };

    renderGraph(merged, true);
    showToast(`Added ${newNodes.length} new nodes`);
  } catch (err) {
    console.error('Expand error:', err);
    showToast('Could not expand this node — try again', 'error');
  } finally {
    expandingId = null;
    document.getElementById('expand-btn').disabled = false;
    removeNodeSpinner();
  }
});

function addNodeSpinner(nodeId) {
  if (!svgG || !nodeSel) return;
  const d = graphData.nodes.find(n => n.id === nodeId);
  if (!d || d.x === undefined) return;
  const r = nodeRadius(d) + 6;
  svgG.append('circle')
    .attr('class', 'node-spinner')
    .attr('cx', d.x).attr('cy', d.y)
    .attr('r', r);
}
function removeNodeSpinner() {
  svgG?.select('.node-spinner').remove();
}

// ── Graph generation ──────────────────────────────────────────────────────────

async function generateGraph(topic) {
  if (!topic.trim()) {
    showToast('Enter a topic to explore', 'error');
    return;
  }
  currentTopic = topic;
  switchToGraphScreen();
  document.getElementById('top-topic-input').value = topic;
  const si = document.getElementById('search-input');
  si.value = '';
  si.dispatchEvent(new Event('input'));
  showGraphLoading();

  try {
    const res = await fetch('/api/graph', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic.trim(), depth: 2 }),
    });

    if (res.status === 429) {
      showToast('Too many requests — wait a moment and try again', 'error');
      showGraphError();
      return;
    }
    if (res.status === 422) {
      const err = await res.json();
      showToast(err.detail || 'Topic not supported', 'error');
      showGraphError();
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    hideGraphOverlays();

    if (!data.nodes?.length) {
      showGraphEmpty();
      return;
    }

    selectedId = null;
    showPanelDefault();
    renderGraph(data);
    document.getElementById('graph-svg').setAttribute('aria-label',
      `Knowledge graph: ${topic}, ${data.nodes.length} nodes, ${data.edges.length} connections`);

  } catch (err) {
    console.error('Graph generation error:', err);
    showGraphError();
  }
}

// ── Screen transitions ────────────────────────────────────────────────────────

function switchToGraphScreen() {
  document.getElementById('home-screen').style.display = 'none';
  const gs = document.getElementById('graph-screen');
  gs.removeAttribute('hidden');
  initSVG();
}

function switchToHomeScreen() {
  document.getElementById('graph-screen').setAttribute('hidden', '');
  document.getElementById('home-screen').style.display = '';
  if (simulation) { simulation.stop(); simulation = null; }
  graphData = { nodes: [], edges: [], topic: '' };
  selectedId = null;
}

function showGraphLoading() {
  document.getElementById('graph-loading').removeAttribute('hidden');
  document.getElementById('graph-empty').setAttribute('hidden', '');
  document.getElementById('graph-error').setAttribute('hidden', '');
}

function hideGraphOverlays() {
  document.getElementById('graph-loading').setAttribute('hidden', '');
  document.getElementById('graph-empty').setAttribute('hidden', '');
  document.getElementById('graph-error').setAttribute('hidden', '');
}

function showGraphEmpty() {
  hideGraphOverlays();
  document.getElementById('graph-empty').removeAttribute('hidden');
}

function showGraphError() {
  document.getElementById('graph-loading').setAttribute('hidden', '');
  document.getElementById('graph-error').removeAttribute('hidden');
}

// ── Export ────────────────────────────────────────────────────────────────────

document.getElementById('export-json').addEventListener('click', () => {
  const json = JSON.stringify(graphData, null, 2);
  downloadBlob(new Blob([json], { type: 'application/json' }), `${slugify(graphData.topic)}.json`);
});

document.getElementById('export-svg').addEventListener('click', () => {
  const svgEl = document.getElementById('graph-svg');
  injectFontsIntoSVG(svgEl);
  const serial = new XMLSerializer().serializeToString(svgEl);
  const blob = new Blob(['<?xml version="1.0"?>\n' + serial], { type: 'image/svg+xml' });
  downloadBlob(blob, `${slugify(graphData.topic)}.svg`);
});

document.getElementById('export-png').addEventListener('click', async () => {
  const svgEl = document.getElementById('graph-svg');
  const w = svgEl.clientWidth || 1200;
  const h = svgEl.clientHeight || 800;
  injectFontsIntoSVG(svgEl);
  const serial = new XMLSerializer().serializeToString(svgEl);
  const blob = new Blob([serial], { type: 'image/svg+xml' });
  const url  = URL.createObjectURL(blob);
  const img  = new Image();
  img.onload = () => {
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#F7F5F0';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0);
    URL.revokeObjectURL(url);
    canvas.toBlob(b => downloadBlob(b, `${slugify(graphData.topic)}.png`));
  };
  img.onerror = () => showToast('PNG export failed — try SVG instead', 'error');
  img.src = url;
});

function injectFontsIntoSVG(svgEl) {
  if (svgEl.querySelector('style')) return;
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
  style.textContent = `text { font-family: Inter, sans-serif; }`;
  svgEl.insertBefore(style, svgEl.firstChild);
}

function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function slugify(str) {
  return (str || 'graph').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// ── Toast ─────────────────────────────────────────────────────────────────────

let _toastTimer = null;
function showToast(msg, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = type === 'error' ? 'toast-error' : '';
  el.removeAttribute('hidden');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.setAttribute('hidden', ''), 4000);
}

// ── Utility ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Event listeners ───────────────────────────────────────────────────────────

// Home screen
document.getElementById('home-explore-btn').addEventListener('click', () => {
  generateGraph(document.getElementById('home-topic-input').value);
});
document.getElementById('home-topic-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') generateGraph(e.target.value);
});
document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => generateGraph(chip.dataset.topic));
});

// Top bar
document.getElementById('top-explore-btn').addEventListener('click', () => {
  generateGraph(document.getElementById('top-topic-input').value);
});
document.getElementById('top-topic-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') generateGraph(e.target.value);
});
document.getElementById('app-name-link').addEventListener('click', switchToHomeScreen);
document.getElementById('app-name-link').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') switchToHomeScreen();
});

// Panel
document.getElementById('panel-close-btn').addEventListener('click', deselectNode);
document.getElementById('panel-retry-btn').addEventListener('click', () => {
  if (_lastDetailNode) { showPanelLoading(); loadNodeDetail(_lastDetailNode); }
});

// Error retry buttons
document.getElementById('error-retry-btn').addEventListener('click', () => {
  if (currentTopic) generateGraph(currentTopic);
});
document.getElementById('empty-retry-btn').addEventListener('click', () => {
  switchToHomeScreen();
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if (!document.getElementById('graph-screen').hidden) {
    if (e.key === '/' && document.activeElement.id !== 'search-input' &&
        document.activeElement.id !== 'top-topic-input') {
      e.preventDefault();
      document.getElementById('search-input').focus();
    }
    if (e.key === 'Escape') {
      deselectNode();
      document.getElementById('search-input').value = '';
      document.getElementById('search-input').dispatchEvent(new Event('input'));
    }
  }
});
