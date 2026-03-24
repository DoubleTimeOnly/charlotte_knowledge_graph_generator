/* Charlotte Knowledge Graph — D3.js frontend
 *
 * State machine:
 *   HOME ──[explore]──► LOADING ──[success]──► GRAPH
 *                                └─[failure]──► HOME (error)
 *   GRAPH ──[node click]──► GRAPH + panel (instant, from graph data)
 *         ──[connection click]──► center + panel update
 *         ──[expand]──────► GRAPH + node spinner ──► GRAPH (merged)
 *         ──[search]──────► GRAPH (searchlight)
 */

'use strict';

// ── Theme system ──────────────────────────────────────────────────────────────

// Apply saved theme preference (or OS preference) before first paint
;(function () {
  const saved = localStorage.getItem('charlotte-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.setAttribute('data-theme', saved ?? (prefersDark ? 'dark' : 'light'));
})();

function getNodeColor(type) {
  const styles = getComputedStyle(document.documentElement);
  const map = {
    Person:       '--color-person',
    Event:        '--color-event',
    Concept:      '--color-concept',
    Organization: '--color-org',
    Document:     '--color-doc',
  };
  return styles.getPropertyValue(map[type] || '--color-concept').trim() || '#888';
}

function updateThemeIcon() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = isDark ? '☀' : '☽';
}

function updateGraphColors() {
  if (nodeCircleSel) nodeCircleSel.attr('fill', d => getNodeColor(d.type));
  document.querySelectorAll('.legend-dot[data-type]').forEach(dot => {
    dot.style.background = getNodeColor(dot.dataset.type);
  });
}


// ── App state ─────────────────────────────────────────────────────────────────

let graphData     = { nodes: [], edges: [], topic: '' };
let selectedId    = null;
let searchEnabled = false;
let researchMode  = false;   // true when Tavily Research API is active
let sourceMode    = 'web_search';  // 'web_search' | 'readwise'
let expandingId   = null;
let simulation    = null;
let svgG          = null;          // zoomable <g> inside <svg>
let linkSel       = null;
let linkLabelSel  = null;
let nodeGroupSel  = null;          // <g class="node-group"> wrappers
let nodeCircleSel = null;          // <circle> inside each group
let labelSel      = null;
let zoomBehavior  = null;          // exposed for centerOnNode
let currentTopic  = '';
let graphFitted   = false;         // true after initial auto-fit; prevents re-fit on simulation restart

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
  return Math.max(8, Math.min(22, 6 + deg * 1.8));
}


// ── Connected-set helpers (shared by hover + applyHighlight) ──────────────────

function getConnectedSet(nodeId) {
  const connected = new Set([nodeId]);
  graphData.edges.forEach(e => {
    const s = typeof e.source === 'object' ? e.source.id : e.source;
    const t = typeof e.target === 'object' ? e.target.id : e.target;
    if (s === nodeId) connected.add(t);
    if (t === nodeId) connected.add(s);
  });
  return connected;
}

function isEdgeConnected(edge, nodeId) {
  const s = typeof edge.source === 'object' ? edge.source.id : edge.source;
  const t = typeof edge.target === 'object' ? edge.target.id : edge.target;
  return s === nodeId || t === nodeId;
}

// ── D3 SVG setup ──────────────────────────────────────────────────────────────

function initSVG() {
  const svg = d3.select('#graph-svg');
  svg.selectAll('*').remove();

  zoomBehavior = d3.zoom()
    .scaleExtent([0.1, 6])
    .on('zoom', (event) => { if (svgG) svgG.attr('transform', event.transform); });

  svg.call(zoomBehavior);
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
  if (preservePositions && nodeGroupSel) {
    nodeGroupSel.each(d => { oldPos[d.id] = { x: d.x, y: d.y }; });
  }

  nodes.forEach(n => {
    if (oldPos[n.id]) { n.x = oldPos[n.id].x; n.y = oldPos[n.id].y; }
  });

  const links = data.edges.map(e => ({ ...e }));

  svgG.selectAll('*').remove();

  // Arrowhead marker definition
  const defs = svgG.append('defs');
  defs.append('marker')
    .attr('id', 'arrow')
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 10)
    .attr('refY', 0)
    .attr('markerWidth', 7)
    .attr('markerHeight', 7)
    .attr('markerUnits', 'userSpaceOnUse')
    .attr('orient', 'auto')
    .append('path')
      .attr('d', 'M0,-4L10,0L0,4Z')
      .attr('fill', 'context-stroke')
      .attr('stroke', 'none');

  // Links group
  const linksG = svgG.append('g').attr('class', 'links');

  linkSel = linksG.selectAll('line')
    .data(links)
    .enter().append('line')
      .attr('class', 'link-line')
      .attr('marker-end', 'url(#arrow)')
      .on('mouseenter', onEdgeEnter)
      .on('mousemove',  onEdgeMove)
      .on('mouseleave', onEdgeLeave);

  // Edge relationship labels (shown on hover)
  linkLabelSel = linksG.selectAll('.link-label')
    .data(links)
    .enter().append('text')
      .attr('class', 'link-label')
      .text(d => d.relationship_type);

  // Nodes: <g class="node-group"> containing <circle> + <text>
  nodeGroupSel = svgG.append('g').attr('class', 'nodes')
    .selectAll('.node-group')
    .data(nodes)
    .enter().append('g')
      .attr('class', 'node-group')
      .attr('tabindex', 0)
      .attr('role', 'button')
      .attr('aria-label', d => `${d.label}, type: ${d.type}`)
      .on('click', onNodeClick)
      .on('keydown', (event, d) => { if (event.key === 'Enter') onNodeClick(event, d); })
      .on('mouseover', (_event, d) => {
        if (selectedId) return;
        const connected = getConnectedSet(d.id);
        nodeGroupSel.classed('dimmed', n => !connected.has(n.id));
        nodeGroupSel.classed('highlighted', n => n.id === d.id);
        linkSel.classed('dimmed', l => !isEdgeConnected(l, d.id))
               .classed('highlighted', l => isEdgeConnected(l, d.id));
        linkLabelSel.classed('dimmed', l => !isEdgeConnected(l, d.id))
                    .classed('visible', l => isEdgeConnected(l, d.id));
      })
      .on('mouseleave', () => {
        if (selectedId) return;
        nodeGroupSel.classed('dimmed', false).classed('highlighted', false);
        linkSel.classed('dimmed', false).classed('highlighted', false);
        linkLabelSel.classed('dimmed', false).classed('visible', false);
      })
      .call(dragBehavior());

  // Transparent hit-area circle for touch — always at least 22px radius
  nodeGroupSel.append('circle')
    .attr('class', 'node-hit-area')
    .attr('r', d => Math.max(22, nodeRadius(d)))
    .attr('fill', 'transparent')
    .attr('stroke', 'none');

  nodeCircleSel = nodeGroupSel.append('circle')
    .attr('class', 'node-circle')
    .attr('r', d => nodeRadius(d))
    .attr('fill', d => getNodeColor(d.type));

  // Labels inside each group — positioned below the circle
  labelSel = nodeGroupSel.append('text')
    .attr('class', 'node-label')
    .attr('dy', d => nodeRadius(d) + 14)
    .text(d => d.label.length > 22 ? d.label.slice(0, 20) + '…' : d.label);
  labelSel.append('title').text(d => d.label);

  // Force simulation
  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(120).strength(0.4))
    .force('charge',    d3.forceManyBody().strength(-400).distanceMax(500))
    .force('center',    d3.forceCenter(w / 2, h / 2))
    .force('x',         d3.forceX(w / 2).strength(0.04))
    .force('y',         d3.forceY(h / 2).strength(0.04))
    .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 15))
    .alphaDecay(0.05)
    .alphaMin(0.001);

  simulation.on('tick', () => {
    linkSel
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => {
        const dx = d.target.x - d.source.x;
        const dy = d.target.y - d.source.y;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        return d.target.x - (dx / len) * nodeRadius(d.target);
      })
      .attr('y2', d => {
        const dx = d.target.x - d.source.x;
        const dy = d.target.y - d.source.y;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        return d.target.y - (dy / len) * nodeRadius(d.target);
      });
    linkLabelSel
      .attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2);
    nodeGroupSel.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Auto-fit graph into viewport once simulation settles (only on initial load)
  if (!preservePositions) {
    graphFitted = false;
    simulation.on('end', () => {
      if (!graphFitted) {
        graphFitted = true;
        fitGraph();
      }
    });
  }

  if (selectedId) applyHighlight(selectedId);
}

// ── Fit graph to viewport ──────────────────────────────────────────────────────

function fitGraph(durationMs = 400) {
  if (!nodeGroupSel || !zoomBehavior) return;
  const svg = d3.select('#graph-svg');
  const { width: W, height: H } = svg.node().getBoundingClientRect();
  if (!W || !H) return;

  const nodes = nodeGroupSel.data();
  if (!nodes.length) return;

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  nodes.forEach(d => {
    const r = nodeRadius(d);
    minX = Math.min(minX, d.x - r);
    maxX = Math.max(maxX, d.x + r);
    minY = Math.min(minY, d.y - r);
    maxY = Math.max(maxY, d.y + r);
  });

  const pad = 40;
  const scale = Math.min(
    (W - pad * 2) / (maxX - minX),
    (H - pad * 2) / (maxY - minY),
    1   // never zoom in beyond 1× on auto-fit
  );
  const tx = (W - scale * (minX + maxX)) / 2;
  const ty = (H - scale * (minY + maxY)) / 2;
  const t = d3.zoomIdentity.translate(tx, ty).scale(scale);

  svg.transition().duration(durationMs).call(zoomBehavior.transform, t);
}

// Re-fit on window resize so mobile/desktop switches don't leave nodes off-screen
window.addEventListener('resize', () => {
  if (nodeGroupSel) fitGraph(0);
});

// ── Drag behaviour ────────────────────────────────────────────────────────────

function dragBehavior() {
  let dragging = false;
  return d3.drag()
    .on('start', (_event, d) => {
      dragging = false;
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag',  (event, d) => {
      if (!dragging) {
        dragging = true;
        simulation?.alphaTarget(0.3).restart();
      }
      d.fx = event.x; d.fy = event.y;
    })
    .on('end',   (event, d) => {
      if (!event.active) simulation?.alphaTarget(0);
      d.fx = null; d.fy = null;
      dragging = false;
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
  // Blur to prevent the browser's native blue focus ring on SVG elements.
  // The .selected CSS class provides the white ring instead.
  if (event.detail > 0) event.currentTarget.blur();
  selectNode(d);
}

function selectNode(d) {
  selectedId = d.id;
  applyHighlight(d.id);
  renderPanel(d);
}

function deselectNode() {
  selectedId = null;
  applyHighlight(null);
  showPanelDefault();
}

function applyHighlight(id) {
  if (!nodeGroupSel) return;
  // Always clear stale hover CSS classes — they multiply with inline opacity and cause
  // intersection-only highlighting when clicking a node while hover classes are present.
  nodeGroupSel.classed('dimmed', false).classed('highlighted', false).classed('selected', d => d.id === id);
  linkSel?.classed('dimmed', false).classed('highlighted', false);
  linkLabelSel?.classed('dimmed', false).classed('visible', false);
  if (id === null) {
    nodeGroupSel.style('opacity', null);
    linkSel?.style('opacity', null);
    linkLabelSel?.style('opacity', null);
    return;
  }
  const connected = getConnectedSet(id);
  nodeGroupSel.style('opacity', d => connected.has(d.id) ? 1 : 0.2);
  linkSel?.style('opacity', d => isEdgeConnected(d, id) ? 0.8 : 0.08);
  linkLabelSel?.style('opacity', d => isEdgeConnected(d, id) ? 1 : 0);
}

// ── Search / searchlight ──────────────────────────────────────────────────────

document.getElementById('search-input').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!nodeGroupSel) return;

  if (!q) {
    nodeGroupSel.style('opacity', null);
    linkSel?.style('opacity', null);
    linkLabelSel?.style('opacity', null);
    if (selectedId) applyHighlight(selectedId);
    return;
  }

  const matches = new Set(
    graphData.nodes.filter(n => n.label.toLowerCase().includes(q)).map(n => n.id)
  );

  if (matches.size === 0) {
    showToast(`No nodes match "${e.target.value}"`, 'info');
  }

  nodeGroupSel.style('opacity', d => matches.has(d.id) ? 1 : 0.08);
  linkSel?.style('opacity', d => {
    const s = typeof d.source === 'object' ? d.source.id : d.source;
    const t = typeof d.target === 'object' ? d.target.id : d.target;
    return matches.has(s) || matches.has(t) ? 0.5 : 0.04;
  });
  linkLabelSel?.style('opacity', null);
});

// ── Panel helpers ─────────────────────────────────────────────────────────────

function showPanelDefault()  { togglePanel('default'); }
function showPanelContent()  { togglePanel('content'); }

function togglePanel(state) {
  ['default', 'content'].forEach(s => {
    const el = document.getElementById(`panel-${s}`);
    if (s === state) el.removeAttribute('hidden');
    else             el.setAttribute('hidden', '');
  });
}

// ── Node detail — rendered from graph data (instant, no API call) ─────────────

function renderPanel(d) {
  const badge = document.getElementById('panel-type-badge');
  badge.textContent = d.type;
  badge.style.background = getNodeColor(d.type);

  document.getElementById('panel-title').textContent = d.label;

  const dateEl = document.getElementById('panel-date-range');
  if (d.era) {
    dateEl.textContent = d.era;
    dateEl.removeAttribute('hidden');
  } else {
    dateEl.setAttribute('hidden', '');
  }

  const summaryEl = document.getElementById('panel-summary');
  summaryEl.textContent = d.description || '';
  summaryEl.classList.remove('panel-summary--collapsed');
  const existingToggle = summaryEl.nextElementSibling;
  if (existingToggle?.classList.contains('panel-summary-toggle')) existingToggle.remove();

  // Pre-generated descriptions are 2-4 sentences — no expand toggle needed
  document.getElementById('panel-facts').setAttribute('hidden', '');

  // Citations: show sources section only when the node has source_urls
  const sourcesSection = document.getElementById('panel-sources');
  const sourcesList = document.getElementById('sources-list');
  if (sourcesSection && sourcesList) {
    if (d.source_urls && d.source_urls.length > 0) {
      sourcesList.innerHTML = d.source_urls
        .slice(0, 4)
        .map(url => {
          try {
            const domain = new URL(url).hostname.replace(/^www\./, '');
            return `<li><a href="${escHtml(url)}" target="_blank" rel="noopener noreferrer">${escHtml(domain)} ↗</a></li>`;
          } catch { return ''; }
        })
        .filter(Boolean)
        .join('');
      sourcesSection.removeAttribute('hidden');
    } else {
      sourcesSection.setAttribute('hidden', '');
    }
  }

  // Connections — split into Inbound / Outbound subsections
  const connSection = document.getElementById('panel-connections');
  const { inbound, outbound } = buildConnections(d.id);
  populateConnList('inbound-list', 'panel-inbound', inbound);
  populateConnList('outbound-list', 'panel-outbound', outbound);
  if (inbound.length || outbound.length) {
    connSection.removeAttribute('hidden');
  } else {
    connSection.setAttribute('hidden', '');
  }

  showPanelContent();
}

function populateConnList(listId, sectionId, items) {
  const list = document.getElementById(listId);
  const section = document.getElementById(sectionId);
  list.innerHTML = '';
  if (items.length) {
    items.forEach(c => {
      const li = document.createElement('li');
      li.innerHTML =
        `<span class="conn-dot" style="background:${getNodeColor(c.type)}"></span>` +
        `<span class="conn-label" title="${escHtml(c.label)}">${escHtml(c.label)}</span>` +
        `<span class="conn-rel">${escHtml(c.rel)}</span>`;
      li.addEventListener('click', () => jumpToNode(c.id));
      list.appendChild(li);
    });
    section.removeAttribute('hidden');
  } else {
    section.setAttribute('hidden', '');
  }
}

function buildConnections(nodeId) {
  const nodeMap = Object.fromEntries(graphData.nodes.map(n => [n.id, n]));
  const inbound = [], outbound = [];
  graphData.edges.forEach(e => {
    const s = typeof e.source === 'object' ? e.source.id : e.source;
    const t = typeof e.target === 'object' ? e.target.id : e.target;
    if (s === nodeId) {
      const target = nodeMap[t];
      if (target) outbound.push({ id: target.id, label: target.label, type: target.type, rel: e.relationship_type });
    } else if (t === nodeId) {
      const source = nodeMap[s];
      if (source) inbound.push({ id: source.id, label: source.label, type: source.type, rel: e.relationship_type });
    }
  });
  return { inbound, outbound };
}

// ── Connection navigation ──────────────────────────────────────────────────────

function jumpToNode(nodeId) {
  if (!nodeGroupSel) return;
  let target = null;
  nodeGroupSel.each(d => { if (d.id === nodeId) target = d; });
  if (!target) return;
  selectNode(target);
  centerOnNode(target);
}

function centerOnNode(d) {
  if (d.x === undefined || d.y === undefined) return;
  const svg = d3.select('#graph-svg');
  const w = svg.node().clientWidth  || 800;
  const h = svg.node().clientHeight || 600;
  const scale = d3.zoomTransform(svg.node()).k;
  const x = w / 2 - d.x * scale;
  const y = h / 2 - d.y * scale;
  svg.transition().duration(500)
    .call(zoomBehavior.transform, d3.zoomIdentity.translate(x, y).scale(scale));
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

  // Compute direct neighbors of the selected node (handle D3's object mutation on edges)
  const neighborIds = new Set();
  graphData.edges.forEach(e => {
    const srcId = typeof e.source === 'object' ? e.source.id : e.source;
    const tgtId = typeof e.target === 'object' ? e.target.id : e.target;
    if (srcId === node.id) neighborIds.add(tgtId);
    if (tgtId === node.id) neighborIds.add(srcId);
  });
  const seedNodes = [node, ...graphData.nodes.filter(n => neighborIds.has(n.id))];

  try {
    const res = await fetch('/api/expand', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        node_id: node.id,
        node_label: node.label,
        node_type: node.type,
        context_nodes: contextNodes,
        seed_nodes: seedNodes,
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
  if (!svgG || !nodeGroupSel) return;
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

async function generateGraph(topic, forceRefresh = false) {
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

  // Disable regenerate button while loading
  const regenBtn = document.getElementById('regen-btn');
  if (regenBtn) { regenBtn.disabled = true; regenBtn.setAttribute('aria-disabled', 'true'); }

  // Stage 0: first stage shown immediately at t=0 depends on active backend
  // Stages advance on a 35s interval to reflect real search+LLM timing (~2-3 min total)
  const stages = sourceMode === 'readwise'
    ? ['Fetching your highlights…', 'Surveying entities…', 'Building connections…', 'Validating graph…', 'Finalizing…']
    : researchMode
      ? ['Researching topic in depth…', 'Surveying entities…', 'Building connections…', 'Validating graph…', 'Finalizing…']
      : ['Searching the web…', 'Surveying entities…', 'Building connections…', 'Validating graph…', 'Finalizing…'];
  let stageIdx = 0;
  let stageTimer = null;
  const stageTextEl = document.getElementById('loading-stage-text');
  if (stageTextEl) stageTextEl.textContent = stages[0];

  // Start cycling stages immediately so users see progress during the entire wait
  stageTimer = setInterval(() => {
    if (stageIdx < stages.length - 1) {
      stageIdx++;
      if (stageTextEl) stageTextEl.textContent = stages[stageIdx];
    }
  }, 35000);

  try {
    const res = await fetch('/api/graph', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topic.trim(), depth: 2, force_refresh: forceRefresh, mode: sourceMode }),
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

    // Use resolved book title as the displayed topic for Readwise mode
    const displayTopic = data.resolved_title || topic;
    if (data.resolved_title) {
      document.getElementById('top-topic-input').value = data.resolved_title;
      currentTopic = data.resolved_title;
    }

    renderGraph(data);
    document.getElementById('graph-svg').setAttribute('aria-label',
      `Knowledge graph: ${displayTopic}, ${data.nodes.length} nodes, ${data.edges.length} connections`);

    // Show info bar with timestamp or Readwise provenance
    const infoBar = document.getElementById('graph-info-bar');
    const tsEl = document.getElementById('graph-timestamp');
    if (infoBar && tsEl) {
      if (sourceMode === 'readwise' && data.resolved_title) {
        tsEl.textContent = `Readwise \u2022 ${data.resolved_title}`;
      } else if (data.generated_at) {
        const d = new Date(data.generated_at);
        tsEl.textContent = `Generated ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;
      } else {
        tsEl.textContent = '';
      }
      infoBar.removeAttribute('hidden');
    }

  } catch (err) {
    console.error('Graph generation error:', err);
    showGraphError();
  } finally {
    clearInterval(stageTimer);
    if (stageTextEl) stageTextEl.textContent = '';
    if (regenBtn) { regenBtn.disabled = false; regenBtn.removeAttribute('aria-disabled'); }
  }
}

// Wire up Regenerate button once DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  const regenBtn = document.getElementById('regen-btn');
  if (regenBtn) {
    regenBtn.addEventListener('click', () => {
      if (currentTopic) generateGraph(currentTopic, true);
    });
  }
});

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
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-canvas').trim() || '#F7F5F0';
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
  style.textContent = `text { font-family: 'General Sans', Inter, sans-serif; }`;
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

// Panel resize handle
(function () {
  const handle = document.getElementById('panel-resize-handle');
  const panel  = document.getElementById('side-panel');
  let resizing = false, startX = 0, startW = 0;

  handle.addEventListener('mousedown', e => {
    resizing = true;
    startX = e.clientX;
    startW = panel.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!resizing) return;
    const newW = Math.min(Math.max(startW + (startX - e.clientX), 220), 700);
    panel.style.width = newW + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!resizing) return;
    resizing = false;
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

// Theme toggle
document.getElementById('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('charlotte-theme', next);
  updateThemeIcon();
  updateGraphColors();
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

// Init
updateThemeIcon();
fetch('/api/config').then(r => r.json()).then(cfg => {
  searchEnabled = cfg.search_enabled;
  researchMode  = cfg.research_mode ?? false;
  const label = cfg.research_mode ? 'Deep research enabled' : 'Web search enabled';
  for (const id of ['home-search-status', 'top-search-status']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.querySelector('.search-status-dot').classList.add('on');
    el.querySelector('.search-status-label').textContent = label;
    el.removeAttribute('hidden');
  }

  // Show mode selector only when Readwise is available
  const modeSelect = document.getElementById('source-mode-select');
  if (cfg.readwise_available && modeSelect) {
    modeSelect.removeAttribute('hidden');
    modeSelect.addEventListener('change', () => {
      sourceMode = modeSelect.value;
      _applyModeUX();
    });
  }
}).catch(() => {});

function _applyModeUX() {
  const input = document.getElementById('home-topic-input');
  const chips = document.getElementById('example-chips');
  if (sourceMode === 'readwise') {
    if (input) input.placeholder = 'Enter Readwise book title or ID…';
    if (chips) chips.setAttribute('hidden', '');
  } else {
    if (input) input.placeholder = 'Ask about anything — conflicts, economics, papers...';
    if (chips) chips.removeAttribute('hidden');
  }
}
