import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";
import { numTd } from "../lib/fmt.js";
import { selectItem, empty as detailEmpty } from "../lib/detail.js";

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

export { startSignalsSocket, stopSignalsSocket };
export default { id: "signals", label: "Signals", render: renderSignals };
