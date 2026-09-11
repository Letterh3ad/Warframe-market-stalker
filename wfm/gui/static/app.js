import { api } from "./lib/api.js";
import { el, showError, clearError } from "./lib/dom.js";
import { fmt, missingLine, statTable, signClass, numTd, statLine } from "./lib/fmt.js";
import { claimList, isCurrentList } from "./lib/panes.js";
import { selectItem, empty as detailEmpty, dispose as disposePriceChart, invalidate as invalidateDetail } from "./lib/detail.js";
import * as daemonStatus from "./lib/daemon_status.js";
import daemonTab from "./tabs/daemon.js";
import catalogTab from "./tabs/catalog.js";

// --- Watchlist tab -------------------------------------------------------
function renderWatchlist() {
  document.getElementById("list-pane").append(
    el("h1", null, "Watchlist"),
    el("div", { id: "watchlist-body" }),
  );
  detailEmpty();
  loadWatchlist();
}

async function loadWatchlist() {
  const body = document.getElementById("watchlist-body");
  if (!body) return;
  const seq = claimList();
  body.replaceChildren(el("p", { class: "muted" }, "loading..."));
  let rows;
  try {
    rows = await api("/watchlist");
  } catch (e) {
    if (!isCurrentList(seq)) return;
    body.replaceChildren(el("p", { class: "muted" }, "could not load watchlist."));
    showError(e.message);
    return;
  }
  if (!isCurrentList(seq)) return;
  paintWatchlist(body, rows || []);
}

function paintWatchlist(body, rows) {
  if (rows.length === 0) {
    body.replaceChildren(
      el("p", { class: "muted" }, "nothing watched yet. add items from the Catalog tab."),
    );
    return;
  }
  const trs = rows.map((row) => {
    const removeBtn = el("button", {
      class: "btn", type: "button",
      onclick: async (e) => {
        e.stopPropagation();
        removeBtn.disabled = true;
        try {
          await api(
            `/watchlist/${encodeURIComponent(row.slug)}/${encodeURIComponent(row.rank)}`,
            { method: "DELETE" },
          );
          loadWatchlist();
        } catch (err) {
          showError(err.message);
          removeBtn.disabled = false;
        }
      },
    }, "remove");
    const tr = el("tr", {
      class: "row",
      dataset: { slug: row.slug },
      onclick: () => {
        for (const s of body.querySelectorAll("tr.row.selected")) s.classList.remove("selected");
        tr.classList.add("selected");
        selectItem(row.slug, row.rank, row);
      },
    },
      el("td", null, row.name || row.slug),
      el("td", { class: "mono" }, String(row.rank)),
      el("td", { class: "mono" }, String(row.pin_weight)),
      el("td", null, row.alert_override ? "yes" : "no"),
      el("td", { class: "mono" }, (row.added_at || "").slice(0, 10)),
      el("td", null, removeBtn),
    );
    return tr;
  });
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Name"), el("th", null, "Rank"), el("th", null, "Pin"),
      el("th", null, "Alert"), el("th", null, "Added"), el("th", null, ""))),
    el("tbody", null, ...trs),
  );
  body.replaceChildren(el("div", { class: "table-scroll" }, table));
}

// --- Ledger tab (read-only; the GUI records no trades) ------------------
function renderLedger() {
  document.getElementById("list-pane").append(
    el("h1", null, "Ledger"),
    el("div", { id: "ledger-holdings", class: "ledger-section" }),
    el("div", { id: "ledger-pnl", class: "ledger-section" }),
  );
  detailEmpty();
  loadLedger();
}

async function loadLedger() {
  const hBox = document.getElementById("ledger-holdings");
  const pBox = document.getElementById("ledger-pnl");
  if (!hBox || !pBox) return;
  const seq = claimList();
  hBox.replaceChildren(el("h2", null, "Holdings"), el("p", { class: "muted" }, "loading..."));
  pBox.replaceChildren(el("h2", null, "P&L"), el("p", { class: "muted" }, "loading..."));

  let holdings, pnl;
  try {
    [holdings, pnl] = await Promise.all([api("/ledger/holdings"), api("/ledger/pnl")]);
  } catch (e) {
    if (!isCurrentList(seq)) return;
    hBox.replaceChildren(el("h2", null, "Holdings"),
      el("p", { class: "muted" }, "could not load ledger."));
    pBox.replaceChildren();
    showError(e.message);
    return;
  }
  if (!isCurrentList(seq)) return;
  const nameMap = {};
  for (const r of holdings || []) nameMap[r.slug] = r.name || r.slug;
  for (const r of (pnl && pnl.open_positions) || []) nameMap[r.slug] = r.name || r.slug;

  paintHoldings(hBox, holdings || []);
  paintPnl(pBox, pnl || {}, nameMap);
}

function paintHoldings(box, rows) {
  box.replaceChildren(el("h2", null, "Holdings"));
  if (rows.length === 0) {
    box.append(el("p", { class: "muted" }, "no holdings"));
    return;
  }
  const trs = rows.map((r) => el("tr", null,
    el("td", null, r.name || r.slug),
    el("td", { class: "mono" }, String(r.rank)),
    el("td", { class: "mono" }, String(r.quantity)),
    numTd(r.avg_cost, "p"),
    numTd(r.mark, "p"),
    numTd(r.unrealized_profit, "p", signClass(r.unrealized_profit)),
  ));
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Name"), el("th", null, "Rank"), el("th", null, "Qty"),
      el("th", null, "Cost basis"), el("th", null, "Mark"), el("th", null, "Unrealized"))),
    el("tbody", null, ...trs),
  );
  box.append(el("div", { class: "table-scroll" }, table));
}

function paintPnl(box, pnl, nameMap) {
  box.replaceChildren(
    el("h2", null, "P&L"),
    statLine("realized profit", pnl.realized_profit, { unit: "p", colorize: true }),
    statLine("trades", pnl.trades),
    el("h3", null, "Closed trades"),
  );
  const lots = pnl.lots || [];
  if (lots.length === 0) {
    box.append(el("p", { class: "muted" }, "no closed trades yet"));
    return;
  }
  const trs = lots.map((l) => el("tr", null,
    el("td", null, (nameMap && nameMap[l.slug]) || l.slug),
    el("td", { class: "mono" }, String(l.rank)),
    el("td", { class: "mono" }, String(l.quantity)),
    numTd(l.cost, "p"),
    numTd(l.proceeds, "p"),
    numTd(l.profit, "p", signClass(l.profit)),
  ));
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Name"), el("th", null, "Rank"), el("th", null, "Qty"),
      el("th", null, "Cost"), el("th", null, "Proceeds"), el("th", null, "Profit"))),
    el("tbody", null, ...trs),
  );
  box.append(el("div", { class: "table-scroll" }, table));
}

// --- Signals tab + live feed -------------------------------------------------
// Two payload shapes reach the feed: GET /signals rows carry `id`/`alerted_at`,
// WS /ws/signals pushes carry `expires_at` and NO id. Rows are keyed by a
// composite of fields present in both, never by id.
const signalsFeed = {
  rows: [],                 // newest first
  keys: new Set(),
  socket: null,
  reconnectTimer: null,
  reconnectDelay: 1000,
  connected: false,
  unloading: false,
  loadError: false,       // true when the last GET /signals threw (vs an empty feed)
};

function sigKey(s) {
  return `${s.slug}|${s.rank}|${s.analyzer}|${s.ts}`;
}

function renderSignals() {
  document.getElementById("list-pane").append(
    el("h1", null,
      "Signals",
      el("span", { id: "sig-dot", class: "sig-dot daemon-dot warn" }),
      el("span", { id: "sig-status", class: "sig-status" }),
    ),
    el("div", { id: "signals-body" }),
  );
  detailEmpty();
  updateSignalsIndicator();
  loadSignals();
  startSignalsSocket();
}

function updateSignalsIndicator() {
  const dot = document.getElementById("sig-dot");
  const status = document.getElementById("sig-status");
  if (!dot || !status) return;
  if (signalsFeed.connected) {
    dot.className = "sig-dot daemon-dot up";
    status.textContent = "";
  } else {
    dot.className = "sig-dot daemon-dot warn";
    status.textContent = "feed disconnected, showing history only";
  }
}

async function loadSignals() {
  const body = document.getElementById("signals-body");
  if (!body) return;
  body.replaceChildren(el("p", { class: "muted" }, "loading..."));
  let data;
  try {
    data = await api("/signals?limit=50");
  } catch (e) {
    signalsFeed.rows = [];
    signalsFeed.keys = new Set();
    signalsFeed.loadError = true;
    paintSignals();
    showError(e.message);
    return;
  }
  signalsFeed.loadError = false;
  signalsFeed.rows = [];
  signalsFeed.keys = new Set();
  for (const s of Array.isArray(data) ? data : []) {
    const key = sigKey(s);
    if (signalsFeed.keys.has(key)) continue;
    signalsFeed.keys.add(key);
    signalsFeed.rows.push(s); // GET is ts DESC, so push keeps newest first
  }
  paintSignals();
}

function paintSignals() {
  const body = document.getElementById("signals-body");
  if (!body) return;
  if (signalsFeed.rows.length === 0) {
    body.replaceChildren(el("p", { class: "muted" },
      signalsFeed.loadError ? "couldn't load signal history" : "no signals yet"));
    return;
  }
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Time"), el("th", null, "Item"), el("th", null, "Analyzer"),
      el("th", null, "Direction"), el("th", null, "Magnitude"), el("th", null, "Confidence"))),
    el("tbody", { id: "signals-tbody" }, ...signalsFeed.rows.map((s) => sigRow(s, false))),
  );
  body.replaceChildren(el("div", { class: "table-scroll" }, table));
}

function sigRow(s, isNew) {
  const dirClass = s.direction === "buy" ? "val-up"
    : s.direction === "sell" ? "val-down" : null;
  const tr = el("tr", {
    class: isNew ? "row sig-new" : "row",
    dataset: { slug: s.slug },
    onclick: () => {
      const b = document.getElementById("signals-body");
      if (b) for (const x of b.querySelectorAll("tr.row.selected")) x.classList.remove("selected");
      tr.classList.add("selected");
      selectItem(s.slug, s.rank, s);
    },
  },
    el("td", { class: "mono" }, String(s.ts || "").slice(0, 16).replace("T", " ")),
    el("td", null, s.slug),
    el("td", null, s.analyzer),
    el("td", { class: dirClass }, s.direction || ""),
    numTd(s.magnitude),
    numTd(s.confidence),
  );
  return tr;
}

// Add one pushed signal. Dedup by composite key; prepend if new. When the table
// is live, insert a single highlighted row rather than repainting.
function addSignal(s) {
  const key = sigKey(s);
  if (signalsFeed.keys.has(key)) return;
  signalsFeed.keys.add(key);
  signalsFeed.rows.unshift(s);
  const tbody = document.getElementById("signals-tbody");
  if (tbody) {
    const tr = sigRow(s, true);
    tbody.prepend(tr);
    setTimeout(() => tr.classList.remove("sig-new"), 2000);
  } else {
    paintSignals();
  }
}

// Open the feed socket. No-ops when one is already open/connecting or a reconnect
// is already pending, so a tab re-render never spawns a second socket.
function startSignalsSocket() {
  if (signalsFeed.unloading) return;
  if (signalsFeed.reconnectTimer) return;
  const sock = signalsFeed.socket;
  if (sock && (sock.readyState === WebSocket.OPEN || sock.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const url = (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/ws/signals";
  let socket;
  try {
    socket = new WebSocket(url);
  } catch (_) {
    scheduleSignalsReconnect();
    return;
  }
  signalsFeed.socket = socket;

  socket.addEventListener("open", () => {
    signalsFeed.connected = true;
    signalsFeed.reconnectDelay = 1000;
    updateSignalsIndicator();
  });
  socket.addEventListener("message", (ev) => {
    let s;
    try { s = JSON.parse(ev.data); } catch (_) { return; }
    if (s && s.slug) addSignal(s);
  });
  socket.addEventListener("error", () => {
    try { socket.close(); } catch (_) { /* close handler drives reconnect */ }
  });
  socket.addEventListener("close", () => {
    if (signalsFeed.socket === socket) signalsFeed.socket = null;
    signalsFeed.connected = false;
    updateSignalsIndicator();
    if (!signalsFeed.unloading) scheduleSignalsReconnect();
  });
}

function scheduleSignalsReconnect() {
  if (signalsFeed.unloading || signalsFeed.reconnectTimer) return;
  const delay = signalsFeed.reconnectDelay;
  signalsFeed.reconnectTimer = setTimeout(() => {
    signalsFeed.reconnectTimer = null;
    signalsFeed.reconnectDelay = Math.min(signalsFeed.reconnectDelay * 2, 30000);
    startSignalsSocket();
  }, delay);
}

// Page unload: close the socket, null the ref, cancel any pending reconnect.
// A pagehide with event.persisted === true is a bfcache freeze, not an unload: the
// page can be restored live, so latching `unloading` here would kill the feed
// forever (start/reconnect both short-circuit on it). Leave the socket alone; the
// browser suspends it for the freeze and the close handler reconnects on restore.
function stopSignalsSocket(e) {
  if (e && e.persisted) return;
  signalsFeed.unloading = true;
  if (signalsFeed.reconnectTimer) {
    clearTimeout(signalsFeed.reconnectTimer);
    signalsFeed.reconnectTimer = null;
  }
  if (signalsFeed.socket) {
    try { signalsFeed.socket.close(); } catch (_) { /* already gone */ }
    signalsFeed.socket = null;
  }
}

// --- Groups tab --------------------------------------------------------------
// list-pane: group list + the selected group's members. detail-pane: the
// selected group's analysis (set-arbitrage roll-up). groupSeq drops stale
// renders when the selection changes mid-fetch.
let groupSeq = 0;
const groupsState = { selected: null, members: [] };

function renderGroups() {
  // Fresh entry to the tab: drop any prior selection so paintGroups does not mark
  // a stale row .selected while the members box and detail pane are empty. No
  // auto-reselect.
  groupsState.selected = null;
  groupsState.members = [];
  disposePriceChart();
  const pane = document.getElementById("list-pane");
  const newInput = el("input", { type: "text", placeholder: "new group name" });
  const newBtn = el("button", {
    class: "btn", type: "button",
    onclick: () => { const n = newInput.value; newInput.value = ""; createGroup(n); },
  }, "create");
  pane.append(
    el("h1", null, "Groups"),
    el("div", { class: "catalog-search" }, newInput, el("span", null, " "), newBtn),
    el("div", { id: "groups-list" }),
    el("div", { id: "group-members", class: "ledger-section" }),
  );
  document.getElementById("detail-pane").replaceChildren(
    el("p", { class: "muted" }, "select a group")
  );
  loadGroups();
}

async function loadGroups() {
  const box = document.getElementById("groups-list");
  if (!box) return;
  const seq = claimList();
  box.replaceChildren(el("p", { class: "muted" }, "loading..."));
  let groups;
  try {
    groups = await api("/groups");
  } catch (e) {
    if (!isCurrentList(seq)) return;
    box.replaceChildren(el("p", { class: "muted" }, "could not load groups."));
    showError(e.message);
    return;
  }
  if (!isCurrentList(seq)) return;
  paintGroups(box, Array.isArray(groups) ? groups : []);
}

function paintGroups(box, groups) {
  box.replaceChildren();
  if (groups.length === 0) {
    box.append(el("p", { class: "muted" }, "no groups yet. create one above."));
    return;
  }
  const trs = groups.map((g) => {
    const del = el("button", {
      class: "btn", type: "button",
      onclick: (e) => { e.stopPropagation(); deleteGroup(g.name); },
    }, "delete");
    return el("tr", {
      class: groupsState.selected === g.name ? "row selected" : "row",
      dataset: { group: g.name },
      onclick: () => selectGroup(g.name),
    },
      el("td", null, g.name),
      el("td", { class: "mono" }, String(g.members)),
      el("td", null, del),
    );
  });
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Group"), el("th", null, "Members"), el("th", null, ""))),
    el("tbody", null, ...trs),
  );
  box.append(el("div", { class: "table-scroll" }, table));
}

async function createGroup(name) {
  name = (name || "").trim();
  if (!name) return;
  try {
    await api("/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (e) { showError(e.message); return; }
  loadGroups();
}

async function deleteGroup(name) {
  if (!window.confirm(`Delete group "${name}" and all its membership?`)) return;
  try {
    await api(`/groups/${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (e) { showError(e.message); return; }
  if (groupsState.selected === name) {
    groupSeq++;
    groupsState.selected = null;
    groupsState.members = [];
    const mb = document.getElementById("group-members");
    if (mb) mb.replaceChildren();
    disposePriceChart();
    document.getElementById("detail-pane").replaceChildren(
      el("p", { class: "muted" }, "select a group")
    );
  }
  loadGroups();
}

async function selectGroup(name) {
  const seq = ++groupSeq;
  groupsState.selected = name;
  const list = document.getElementById("groups-list");
  if (list) {
    for (const r of list.querySelectorAll("tr.row")) {
      r.classList.toggle("selected", r.dataset.group === name);
    }
  }

  const membersBox = document.getElementById("group-members");
  membersBox.replaceChildren(el("p", { class: "muted" }, "loading members..."));
  const pane = document.getElementById("detail-pane");
  disposePriceChart();
  pane.replaceChildren(el("p", { class: "muted" }, "loading analysis..."));

  let detail;
  try {
    detail = await api(`/groups/${encodeURIComponent(name)}`);
  } catch (e) {
    if (seq !== groupSeq) return;
    membersBox.replaceChildren(el("p", { class: "muted" }, "could not load members."));
    showError(e.message);
    return;
  }
  if (seq !== groupSeq) return;
  groupsState.members = detail.members || [];
  paintMembers(membersBox, name, groupsState.members);

  let analysis;
  try {
    analysis = await api(`/groups/${encodeURIComponent(name)}/analysis`);
  } catch (e) {
    if (seq !== groupSeq) return;
    pane.replaceChildren(el("p", { class: "muted" }, "could not load analysis."));
    showError(e.message);
    return;
  }
  if (seq !== groupSeq) return;
  paintAnalysis(pane, analysis, groupsState.members);
}

function paintMembers(box, groupName, members) {
  box.replaceChildren(el("h2", null, "Members"));
  const addInput = el("input", { type: "text", placeholder: "add member (slug or name)" });
  const addBtn = el("button", {
    class: "btn", type: "button",
    onclick: () => { const q = addInput.value; addInput.value = ""; addMember(groupName, q); },
  }, "add");
  box.append(el("div", { class: "catalog-search" }, addInput, el("span", null, " "), addBtn));

  if (members.length === 0) {
    box.append(el("p", { class: "muted" }, "no members yet."));
    return;
  }
  const trs = members.map((m) => {
    const rm = el("button", {
      class: "btn", type: "button",
      onclick: (e) => { e.stopPropagation(); removeMember(groupName, m); },
    }, "remove");
    return el("tr", {
      class: "row",
      onclick: () => selectItem(m.slug, m.rank, m),
    },
      el("td", null, m.name || m.slug),
      el("td", { class: "mono" }, m.slug),
      el("td", { class: "mono" }, String(m.rank)),
      el("td", null, rm),
    );
  });
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Name"), el("th", null, "Slug"),
      el("th", null, "Rank"), el("th", null, ""))),
    el("tbody", null, ...trs),
  );
  box.append(el("div", { class: "table-scroll" }, table));
}

async function addMember(groupName, query) {
  query = (query || "").trim();
  if (!query) return;
  try {
    await api(`/groups/${encodeURIComponent(groupName)}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, rank: null }),
    });
  } catch (e) { showError(e.message); return; }
  selectGroup(groupName);
}

async function removeMember(groupName, m) {
  try {
    await api(`/groups/${encodeURIComponent(groupName)}/members`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: m.slug, rank: m.rank }),
    });
  } catch (e) { showError(e.message); return; }
  selectGroup(groupName);
}

// analyzer signal -> "buy"/"sell" colour, matching the Signals tab.
function groupDirClass(direction) {
  return direction === "buy" ? "val-up" : direction === "sell" ? "val-down" : null;
}

// Plain-words meaning for a set_arbitrage direction. The enum alone is ambiguous:
// "buy" = act on the Set, "sell" = act on the parts.
function arbitrageWords(direction) {
  if (direction === "buy") return "buy the Set, part it out (the set is cheap)";
  if (direction === "sell") return "buy the parts, sell as a Set (the set is expensive)";
  return "direction unknown";
}

function paintAnalysis(pane, analysis, members) {
  disposePriceChart();
  pane.replaceChildren(el("h1", null, `Analysis: ${analysis.name}`));

  // --- Set arbitrage (group_signals), rendered first ---
  const arb = el("div", { class: "stat-block" }, el("h2", null, "Set arbitrage"));
  const groupSignals = analysis.group_signals || [];
  if (groupSignals.length > 0) {
    for (const s of groupSignals) arb.append(renderGroupSignal(s));
  } else {
    const setCount = (members || []).filter((m) => /_set$/.test(m.slug || "")).length;
    const shaped = setCount === 1 && members.length > 1;
    arb.append(el("p", { class: "muted" }, shaped
      ? "no current arbitrage spread. the group is shaped right, there is just no tradeable gap now (or live book data is stale)."
      : "not arbitrage-shaped: needs exactly one set item plus its parts."));
  }

  // --- Per-member roll-up ---
  const rollup = el("div", { class: "stat-block" }, el("h2", null, "Member signals"));
  const withSignals = (analysis.items || []).filter((it) => it.signals && it.signals.length > 0);
  if (withSignals.length === 0) {
    rollup.append(el("p", { class: "muted" }, "no member signals."));
  } else {
    for (const it of withSignals) {
      rollup.append(el("div", { class: "kv" }, el("span", { class: "k mono" }, it.slug)));
      rollup.append(el("div", { class: "table-scroll" }, memberSigTable(it.signals)));
    }
  }

  pane.append(arb, rollup);
}

function renderGroupSignal(s) {
  const box = el("div", { class: "stat-block" },
    el("div", { class: "kv" },
      el("span", { class: "k" }, "direction"),
      el("span", { class: "v" },
        el("span", { class: groupDirClass(s.direction) }, s.direction || "?"),
        el("span", { class: "muted" }, `  ${arbitrageWords(s.direction)}`),
      ),
    ),
    statLine("magnitude", s.magnitude),
    statLine("confidence", s.confidence),
  );
  const ev = s.evidence || {};
  const keys = Object.keys(ev);
  if (keys.length > 0) {
    box.append(el("div", { class: "kv" }, el("span", { class: "k muted" }, "legs")));
    for (const k of keys) {
      const v = ev[k];
      const vspan = el("span", { class: "v" });
      if (typeof v === "number") {
        vspan.innerHTML = fmt(v);
      } else if (v == null) {
        vspan.className = "v na";
        vspan.textContent = "unavailable";
      } else {
        vspan.textContent = typeof v === "string" ? v : JSON.stringify(v);
      }
      box.append(el("div", { class: "kv" }, el("span", { class: "k" }, k), vspan));
    }
  }
  return box;
}

function memberSigTable(signals) {
  const trs = signals.map((s) => el("tr", null,
    el("td", null, s.analyzer || ""),
    el("td", { class: groupDirClass(s.direction) }, s.direction || ""),
    numTd(s.magnitude),
    numTd(s.confidence),
  ));
  return el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Analyzer"), el("th", null, "Direction"),
      el("th", null, "Magnitude"), el("th", null, "Confidence"))),
    el("tbody", null, ...trs),
  );
}

const TABS = [
  { id: "catalog",   label: "Catalog",   render: catalogTab.render },
  { id: "watchlist", label: "Watchlist", render() { renderWatchlist(); } },
  { id: "signals",   label: "Signals",   render() { renderSignals(); } },
  { id: "groups",    label: "Groups",    render() { renderGroups(); } },
  { id: "daemon",    label: "Daemon",    render: daemonTab.render },
  { id: "ledger",    label: "Ledger",    render() { renderLedger(); } },
];

let activeTab = null;

function selectTab(id) {
  const tab = TABS.find((t) => t.id === id);
  if (!tab) return;
  activeTab = id;
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === id);
  }
  disposePriceChart();
  // #detail-pane is a persistent node (only its children swap), so an in-flight
  // /items/{slug} or /groups/{name}/analysis that resolves after this switch would
  // paint into whatever tab is now showing. Bump both seqs to invalidate them.
  invalidateDetail();
  groupSeq++;
  document.getElementById("list-pane").replaceChildren();
  document.getElementById("detail-pane").replaceChildren();
  tab.render();
}

function buildRail() {
  const list = document.getElementById("tab-list");
  for (const tab of TABS) {
    list.append(
      el("button", {
        class: "tab-btn", type: "button",
        dataset: { tab: tab.id },
        onclick: () => selectTab(tab.id),
      }, tab.label)
    );
  }
}

// --- daemon status poll --------------------------------------------------
function setDaemon(dotClass, label) {
  document.getElementById("daemon-dot").className = `daemon-dot ${dotClass}`;
  document.getElementById("daemon-label").textContent = label;
}

daemonStatus.subscribe((s) => {
  if (s.status === "unreachable") return setDaemon("down", "unreachable");
  if (s.status === "running") {
    return s.stale ? setDaemon("warn", "stale") : setDaemon("up", "running");
  }
  if (s.status === "halted" || s.status === "stopping") return setDaemon("down", s.status);
  if (s.stale) return setDaemon("warn", "stale");
  if (s.status === "not running") return setDaemon("faint", "not running");
  return setDaemon("faint", s.status || "unknown");
});

// --- boot ----------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  buildRail();
  selectTab("catalog");
  daemonStatus.start();
  document.getElementById("error-dismiss").addEventListener("click", clearError);
  window.addEventListener("beforeunload", stopSignalsSocket);
  window.addEventListener("pagehide", stopSignalsSocket);
});
