import { api } from "./lib/api.js";
import { el, showError, clearError } from "./lib/dom.js";
import { fmt, missingLine, statTable, numTd, statLine } from "./lib/fmt.js";
import { claimList, isCurrentList } from "./lib/panes.js";
import { selectItem, empty as detailEmpty, dispose as disposePriceChart, invalidate as invalidateDetail } from "./lib/detail.js";
import * as daemonStatus from "./lib/daemon_status.js";
import daemonTab from "./tabs/daemon.js";
import catalogTab from "./tabs/catalog.js";
import watchlistTab from "./tabs/watchlist.js";
import ledgerTab from "./tabs/ledger.js";
import signalsTab, { startSignalsSocket, stopSignalsSocket } from "./tabs/signals.js";

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
  { id: "watchlist", label: "Watchlist", render: watchlistTab.render },
  { id: "signals",   label: "Signals",   render: signalsTab.render },
  { id: "groups",    label: "Groups",    render() { renderGroups(); } },
  { id: "daemon",    label: "Daemon",    render: daemonTab.render },
  { id: "ledger",    label: "Ledger",    render: ledgerTab.render },
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
