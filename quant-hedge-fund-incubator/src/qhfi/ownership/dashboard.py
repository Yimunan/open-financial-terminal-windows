"""Self-contained HTML dashboard for the 13F manager relationship graph.

``write_dashboard`` renders ONE standalone .html file with every quarter's graph embedded as JSON
and an interactive 3D force-directed network (3d-force-graph via CDN). No server, no build step, no
new Python dependency — it opens offline in any browser. Controls: a quarter slider, an edge-weight
filter, and a side panel ranking managers by centrality plus the emerging/fading movers vs the
previous quarter. Clicking a node shows that manager's top holdings and its closest managers,
ranked — both embedded at build time (the graph data alone has no per-manager positions).
"""

from __future__ import annotations

import json
from pathlib import Path

from qhfi.data import _io
from qhfi.ownership.viz import to_node_link


def _holdings_top(holdings_store, period: str, cik: int, n: int = 12) -> list[dict]:
    """Top-``n`` holdings of one manager-quarter, by value — for the click-to-inspect panel.
    Grouped by issuer (merges share classes) with portfolio weight; reads only the two needed
    columns from the parquet."""
    try:
        df = _io.read_columns(holdings_store._path(cik, period), ["issuer", "value_usd"])
    except Exception:  # noqa: BLE001 - missing file for a manager that didn't file that quarter
        return []
    if df.empty:
        return []
    g = df.groupby("issuer")["value_usd"].sum()
    total = float(g.sum())
    if total <= 0:
        return []
    return [{"issuer": str(i), "weight": round(float(v) / total, 4)}
            for i, v in g.sort_values(ascending=False).head(n).items()]


def _knn_prune(graph: dict, k: int) -> dict:
    """Keep only each node's ``k`` strongest incident edges (union over both endpoints).

    A top-N manager graph is near-complete — index/asset managers hold near-identical large-cap
    books, so almost every pair overlaps. A k-nearest-neighbour view turns that hairball into a
    readable structure (and shrinks the embedded data) while preserving each node's tightest ties.
    """
    links = graph["links"]
    incident: dict[int, list[tuple[float, int]]] = {}
    for i, l in enumerate(links):
        incident.setdefault(l["source"], []).append((l["weight"], i))
        incident.setdefault(l["target"], []).append((l["weight"], i))
    keep: set[int] = set()
    for lst in incident.values():
        lst.sort(reverse=True)
        keep.update(i for _, i in lst[:k])
    return {**graph, "links": [links[i] for i in sorted(keep)]}


def dashboard_data(store, metric: str = "cosine", *, embed_floor: float = 0.0,
                   top_k: int | None = None) -> dict:
    """All quarters' node-link graphs keyed by period: {period: {nodes, links}, ...}.

    ``embed_floor`` drops edges below that weight (noise cut); ``top_k`` then keeps only each node's
    ``top_k`` strongest ties (k-NN view) — both shrink the HTML and de-hairball a dense roster.
    """
    out = {}
    for p in store.periods():
        g = to_node_link(store, p, metric=metric, min_weight=embed_floor)
        out[p] = _knn_prune(g, top_k) if top_k else g
    return out


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<script src="https://unpkg.com/3d-force-graph"></script>
<style>
  :root { --bg:#0e1116; --panel:#161b22; --line:#2b3340; --fg:#e6edf3; --mut:#8b949e; --accent:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); }
  header { padding:12px 18px; border-bottom:1px solid var(--line); display:flex; align-items:baseline; gap:14px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .sub { color:var(--mut); font-size:12px; }
  #wrap { display:flex; height:calc(100vh - 96px); overflow:hidden; }
  #net { flex:1; background:var(--bg); min-width:0; min-height:0; position:relative; }   /* min-width:0 so the canvas can't shove the panel off-screen */
  #labels { position:absolute; inset:0; overflow:hidden; pointer-events:none; }
  .nlabel { position:absolute; top:0; left:0; white-space:nowrap; color:#dfe7ef; opacity:0.4; text-shadow:0 1px 3px #000, 0 0 2px #000; transform:translate(-9999px,-9999px); will-change:transform, opacity; transition:opacity .12s ease; margin-top:-1.6em; margin-left:6px; }
  .nlabel.hot { opacity:1; font-weight:600; color:#fff; z-index:2; }
  #side { width:340px; flex:none; border-left:1px solid var(--line); background:var(--panel); padding:14px 16px 18px; display:flex; flex-direction:column; overflow:hidden; }
  #side h2 { flex:none; }
  .scrollbox { flex:1; min-height:64px; overflow-y:auto; border:1px solid var(--line); border-radius:6px; }
  .scrollbox table td { padding-left:8px; padding-right:8px; }
  .controls { padding:12px 18px; border-bottom:1px solid var(--line); display:flex; gap:26px; align-items:center; flex-wrap:wrap; background:var(--panel); }
  .controls label { color:var(--mut); font-size:12px; margin-right:8px; }
  .controls output { color:var(--accent); font-variant-numeric:tabular-nums; }
  input[type=range] { vertical-align:middle; width:220px; }
  .controls input[type=text] { width:210px; background:#0e1116; border:1px solid var(--line); color:var(--fg); border-radius:4px; padding:3px 8px; }
  .controls input[type=text]:focus { outline:none; border-color:var(--accent); }
  .smsg { margin-left:8px; font-size:11px; color:#f0758a; }
  .hsel { float:right; background:#0e1116; color:var(--fg); border:1px solid var(--line); border-radius:4px; font-size:11px; padding:1px 4px; text-transform:none; letter-spacing:normal; cursor:pointer; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--mut); margin:18px 0 8px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  td { padding:3px 4px; border-bottom:1px solid var(--line); }
  td.num { text-align:right; font-variant-numeric:tabular-nums; color:var(--mut); }
  .bar { height:6px; background:var(--accent); border-radius:3px; }
  .tag { font-size:11px; padding:1px 6px; border-radius:4px; }
  .emerging { background:#1f3a24; color:#5fd07a; } .fading { background:#3a1f24; color:#f0758a; }
  .new { background:#243a3a; color:#5fd0d0; } .dropped { background:#3a3320; color:#d0b85f; }
  .detail { flex:none; max-height:34vh; overflow-y:auto; background:#0e1116; border:1px solid var(--line); border-radius:6px; padding:10px 12px; font-size:12.5px; color:var(--mut); }
  .detail .nm { color:var(--fg); font-size:14px; font-weight:600; }
  .detail .sub2 { font-size:11px; color:var(--mut); margin-bottom:6px; }
  .detail .stat { display:flex; justify-content:space-between; margin-top:2px; }
  .detail .stat b { color:var(--accent); font-weight:600; }
  .detail .tie { display:flex; justify-content:space-between; gap:8px; }
  .detail .tie span:last-child { color:var(--accent); font-variant-numeric:tabular-nums; white-space:nowrap; }
  .detail .sec { margin:9px 0 3px; color:var(--mut); font-size:11px; text-transform:uppercase; letter-spacing:.04em; border-top:1px solid var(--line); padding-top:7px; }
  .reset { float:right; cursor:pointer; background:#21262d; color:var(--fg); border:1px solid var(--line); border-radius:4px; font-size:11px; padding:2px 8px; }
  .reset:hover { border-color:var(--accent); color:var(--accent); }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="sub">edge weight = portfolio-overlap <b>__METRIC__</b> · node size = eigenvector centrality · drag to rotate · scroll to zoom</span>
</header>
<div class="controls">
  <div><label>find</label><input type="text" id="search" list="mgrnames" placeholder="manager name…" autocomplete="off"><span id="searchmsg" class="smsg"></span></div>
  <div><label>quarter</label><input type="range" id="q" min="0" max="0" step="1" value="0"><output id="qlab"></output></div>
  <div><label>min edge weight</label><input type="range" id="w" min="0" max="1" step="0.01" value="__MINW__"><output id="wlab">__MINW__</output></div>
  <div><label>auto-rotate</label><input type="checkbox" id="rot"></div>
  <div><label>labels</label><input type="checkbox" id="lbl" checked></div>
</div>
<datalist id="mgrnames"></datalist>
<div id="wrap">
  <div id="net"></div>
  <div id="side">
    <h2>Selected manager</h2>
    <div id="detail" class="detail">Click a dot for its profile, holdings, and closest managers. Click empty space, press Esc, or hit “reset view” to return to the full graph.</div>
    <h2>Centrality — <span id="cenq"></span><select id="csort" class="hsel"><option value="desc">high → low</option><option value="asc">low → high</option></select></h2>
    <div class="scrollbox"><table id="central"></table></div>
    <h2>Movers vs previous quarter<select id="msort" class="hsel"><option value="desc">high → low</option><option value="asc">low → high</option></select></h2>
    <div class="scrollbox"><table id="movers"></table></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const PERIODS = __PERIODS__;
const HOLDINGS = __HOLDINGS__;
const qEl = document.getElementById('q'), wEl = document.getElementById('w');
qEl.max = PERIODS.length - 1; qEl.value = PERIODS.length - 1;

const container = document.getElementById('net');
const Graph = ForceGraph3D({ controlType: 'orbit' })(container)
  .backgroundColor('#0e1116')
  .showNavInfo(false)
  .nodeRelSize(4)
  .nodeVal(n => 1 + 6 * n.eigenvector_cent)
  .nodeColor(n => heat(n.eigenvector_cent))
  .nodeOpacity(0.95)
  .nodeLabel(n => `${n.manager} — centrality ${n.eigenvector_cent.toFixed(2)} · degree ${n.degree} · $${n.value_usd_bn.toFixed(1)}B`)
  .linkColor(() => 'rgba(130,150,180,0.35)')
  .linkOpacity(0.35)
  .linkWidth(l => 0.4 + 3 * l.weight)
  .onNodeClick(node => { showDetail(node); applyFocus(node.id); })
  .onNodeHover(node => {                 // light up the hovered dot's name (others stay dim)
    if (hotLabel) hotLabel.classList.remove('hot');
    hotLabel = node ? labelEls.get(node) : null;
    if (hotLabel) hotLabel.classList.add('hot');
    container.style.cursor = node ? 'pointer' : '';
  })
  .onBackgroundClick(() => resetView())
  .width(container.clientWidth).height(container.clientHeight);
const controls = Graph.controls();
controls.autoRotate = false; controls.autoRotateSpeed = 1.1;
window.addEventListener('resize', () => Graph.width(container.clientWidth).height(container.clientHeight));

// spread the dots out: stronger inter-node repulsion + longer links than the d3 defaults
Graph.d3Force('charge').strength(-160).distanceMax(600);
Graph.d3Force('link').distance(60);
let fitted = false;
Graph.onEngineStop(() => { if (!fitted){ fitted = true; Graph.zoomToFit(600, 90); } });

// Node names as an HTML overlay synced to each dot's projected screen position. This needs no
// THREE access (3d-force-graph bundles three privately and only exposes a version string), so it
// is immune to the three/three-spritetext version-coupling that WebGL text labels require.
let showLabels = true;
function shortName(s){ return s.length > 22 ? s.slice(0, 21) + '…' : s; }
const labelLayer = document.createElement('div');
labelLayer.id = 'labels';
container.appendChild(labelLayer);
let labelEls = new Map();           // node object (carries engine-assigned x/y/z) -> <div>
let hotLabel = null;                // the label currently highlighted by node hover

function rebuildLabels(nodes){
  labelLayer.textContent = '';
  labelEls = new Map();
  hotLabel = null;
  nodes.forEach(n => {
    const d = document.createElement('div');
    d.className = 'nlabel';
    d.textContent = shortName(n.manager);
    d.style.fontSize = (9.5 + 7 * n.eigenvector_cent).toFixed(1) + 'px';
    labelLayer.appendChild(d);
    labelEls.set(n, d);
  });
}

function updateLabels(){
  if (showLabels){
    const w = container.clientWidth, h = container.clientHeight;
    labelEls.forEach((d, n) => {
      if (n.x == null){ d.style.display = 'none'; return; }
      const c = Graph.graph2ScreenCoords(n.x, n.y, n.z);
      if (c.x < -60 || c.x > w + 60 || c.y < -20 || c.y > h + 20){ d.style.display = 'none'; }
      else { d.style.display = ''; d.style.transform = `translate(${c.x}px,${c.y}px)`; }
    });
  }
  requestAnimationFrame(updateLabels);
}
requestAnimationFrame(updateLabels);

function heat(t){ // centrality 0..1 -> blue→amber
  t = Math.max(0, Math.min(1, t));
  const lo=[88,166,255], hi=[247,176,86];
  const c = lo.map((v,i)=>Math.round(v+(hi[i]-v)*t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function pairKey(a,b){ return a<b ? a+'-'+b : b+'-'+a; }

let focusId = null;                  // when set, show only this manager + its connected dots
function applyFocus(id){
  focusId = id;
  render();
  setTimeout(() => Graph.zoomToFit(600, 80), 500);   // frame the subset once it re-lays-out
}

function findNode(query){
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const ns = DATA[PERIODS[+qEl.value]].nodes;
  return ns.find(n => n.manager.toLowerCase() === q)
      || ns.find(n => n.manager.toLowerCase().includes(q));
}

const DEFAULT_DETAIL = document.getElementById('detail').innerHTML;
function resetView(){                // back to the overview: clear focus + selection + search
  focusId = null;
  document.getElementById('search').value = '';
  document.getElementById('searchmsg').textContent = '';
  document.getElementById('detail').innerHTML = DEFAULT_DETAIL;
  render();
  setTimeout(() => Graph.zoomToFit(600, 70), 500);
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') resetView(); });

function showDetail(node){
  const period = PERIODS[+qEl.value];
  const g = DATA[period];
  const names = {}; g.nodes.forEach(n => names[n.id] = n.manager);
  const ties = g.links
    .filter(l => l.source === node.id || l.target === node.id)
    .map(l => ({name: names[l.source === node.id ? l.target : l.source], w: l.weight}))
    .sort((a,b) => b.w - a.w).slice(0, 12);
  const holds = (HOLDINGS[period] || {})[node.id] || [];
  const row = (a,b) => `<div class="stat"><span>${a}</span><b>${b}</b></div>`;
  const head = t => `<div class="sec">${t}</div>`;
  document.getElementById('detail').innerHTML =
    `<button class="reset" onclick="resetView()">reset view ✕</button>`
    + `<div class="nm">${node.manager}</div>`
    + `<div class="sub2">CIK ${node.id} · ${period}</div>`
    + row('equity AUM', '$' + node.value_usd_bn.toFixed(1) + 'B')
    + row('positions', node.n_positions ?? '—')
    + row('centrality', node.eigenvector_cent.toFixed(2))
    + row('degree / strength', `${node.degree} / ${(node.weighted_degree ?? 0).toFixed(1)}`)
    + head('top holdings')
    + (holds.map(h => `<div class="tie"><span>${h.issuer}</span><span>${(h.weight*100).toFixed(1)}%</span></div>`).join('')
       || '<div style="color:var(--mut)">holdings not embedded</div>')
    + head('closest managers (cosine)')
    + (ties.map((t,i) => `<div class="tie"><span>${i+1}. ${t.name}</span><span>${t.w.toFixed(2)}</span></div>`).join('')
       || '<div style="color:var(--mut)">no ties above the embed floor</div>');
}

function render(){
  const period = PERIODS[+qEl.value];
  const minW = +wEl.value;
  document.getElementById('qlab').textContent = ' ' + period;
  document.getElementById('wlab').textContent = minW.toFixed(2);
  const g = DATA[period];
  document.getElementById('cenq').textContent = `${period} · ${g.nodes.length}`;
  document.getElementById('mgrnames').innerHTML =
    g.nodes.map(n => `<option value="${n.manager.replace(/"/g, '&quot;')}">`).join('');

  // pick the dots to show: full graph, or — in focus mode — only the focused manager + its
  // directly-connected dots (edges incident to the focus), dropping all unrelated dots.
  let srcLinks = g.links.filter(l => l.weight >= minW);
  let srcNodes = g.nodes;
  if (focusId != null){
    const inc = srcLinks.filter(l => l.source === focusId || l.target === focusId);
    const keep = new Set([focusId]);
    inc.forEach(l => { keep.add(l.source); keep.add(l.target); });
    const fn = g.nodes.filter(n => keep.has(n.id));
    if (fn.length){ srcNodes = fn; srcLinks = inc; }     // (focus absent this quarter → show all)
  }

  // 3d-force-graph mutates the objects it is handed (adds x/y/z, rewrites link source/target to
  // node refs), so pass fresh clones each render or the second pass breaks.
  const nodes = srcNodes.map(n => ({...n}));
  const links = srcLinks.map(l => ({source:l.source, target:l.target, weight:l.weight, shared_n:l.shared_n}));
  Graph.graphData({nodes, links});
  rebuildLabels(nodes);

  // central rate — full ranked list (scroll to see all); user chooses the sort direction
  const cdir = document.getElementById('csort').value;
  const top = [...g.nodes].sort((a,b)=> cdir === 'asc'
    ? a.eigenvector_cent - b.eigenvector_cent
    : b.eigenvector_cent - a.eigenvector_cent);
  document.getElementById('central').innerHTML = top.map((n,i) =>
    `<tr><td>${i+1}. ${n.manager}</td><td class="num">${n.eigenvector_cent.toFixed(2)}</td>`+
    `<td style="width:70px"><div class="bar" style="width:${(n.eigenvector_cent*100).toFixed(0)}%"></div></td></tr>`).join('');

  // movers vs previous quarter
  const i = +qEl.value;
  const moversEl = document.getElementById('movers');
  if (i === 0){ moversEl.innerHTML = '<tr><td style="color:var(--mut)">no earlier quarter</td></tr>'; return; }
  const prev = DATA[PERIODS[i-1]];
  const pm = {}, names = {};
  prev.links.forEach(l => pm[pairKey(l.source,l.target)] = l.weight);
  [...prev.nodes, ...g.nodes].forEach(n => names[n.id] = n.manager);
  const rows = [];
  const seen = {};
  g.links.forEach(l => { const k=pairKey(l.source,l.target); seen[k]=1;
    const pv = pm[k]; const cv = l.weight;
    const delta = (pv===undefined?0:pv); const d = cv - delta;
    const status = pv===undefined ? 'new' : (d>0.05?'emerging':(d<-0.05?'fading':'stable'));
    rows.push({a:names[l.source], b:names[l.target], pv, cv, d, status}); });
  Object.keys(pm).forEach(k => { if(!seen[k]){ const [s,t]=k.split('-');
    rows.push({a:names[s]||s, b:names[t]||t, pv:pm[k], cv:undefined, d:-pm[k], status:'dropped'}); }});
  const mdir = document.getElementById('msort').value;   // signed change: high→low = increases first
  rows.sort((x,y)=> mdir === 'asc' ? x.d - y.d : y.d - x.d);
  moversEl.innerHTML = rows.filter(r=>r.status!=='stable').map(r =>
    `<tr><td>${r.a} ↔ ${r.b}</td>`+
    `<td class="num">${r.pv===undefined?'—':r.pv.toFixed(2)}→${r.cv===undefined?'—':r.cv.toFixed(2)}</td>`+
    `<td><span class="tag ${r.status}">${r.status}</span></td></tr>`).join('')
    || '<tr><td style="color:var(--mut)">no notable changes</td></tr>';
}

qEl.addEventListener('input', render);
wEl.addEventListener('input', render);
document.getElementById('csort').addEventListener('change', render);
document.getElementById('msort').addEventListener('change', render);
const searchEl = document.getElementById('search');
searchEl.addEventListener('change', () => {
  const q = searchEl.value;
  const msg = document.getElementById('searchmsg');
  if (!q.trim()){ resetView(); return; }
  const n = findNode(q);
  if (n){ msg.textContent = ''; showDetail(n); applyFocus(n.id); }
  else { msg.textContent = 'no match'; }
});
document.getElementById('rot').addEventListener('change', e => { controls.autoRotate = e.target.checked; });
document.getElementById('lbl').addEventListener('change', e => {
  showLabels = e.target.checked;
  labelLayer.style.display = showLabels ? '' : 'none';
});
render();
</script>
</body>
</html>
"""


def write_dashboard(store, out_path, *, holdings_store=None, metric: str = "cosine",
                    title: str | None = None, embed_floor: float = 0.1, top_k: int | None = 8,
                    default_min_weight: float = 0.0) -> Path:
    """Render the standalone HTML dashboard for all quarters in ``store`` to ``out_path``.

    ``embed_floor`` omits edges below that weight; ``top_k`` keeps each node's strongest ties
    (k-NN view — set None to embed every edge); ``default_min_weight`` is where the in-page slider
    starts. Pass ``holdings_store`` (a HoldingsStore) to embed each manager's top holdings for the
    click-to-inspect panel. Defaults suit a dense top-N roster: a per-node k-NN graph, slider open.
    """
    data = dashboard_data(store, metric=metric, embed_floor=embed_floor, top_k=top_k)
    periods = list(data)
    minw = f"{max(default_min_weight, embed_floor):.2f}"
    holdings: dict = {}
    if holdings_store is not None:
        for p in periods:
            holdings[p] = {str(n["id"]): _holdings_top(holdings_store, p, n["id"])
                           for n in data[p]["nodes"]}
    html = (_TEMPLATE
            .replace("__DATA__", json.dumps(data))
            .replace("__PERIODS__", json.dumps(periods))
            .replace("__HOLDINGS__", json.dumps(holdings))
            .replace("__METRIC__", metric)
            .replace("__MINW__", minw)
            .replace("__TITLE__", title or "13F Manager Relationship Graph"))
    out = Path(out_path)
    out.write_text(html, encoding="utf-8")
    return out
