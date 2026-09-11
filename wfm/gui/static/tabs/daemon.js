import { api } from "../lib/api.js";
import { el, humanAge, showError } from "../lib/dom.js";
import { fmt } from "../lib/fmt.js";
import { empty as detailEmpty } from "../lib/detail.js";
import { subscribe, refresh, latest } from "../lib/daemon_status.js";

// daemonStopping latches once Stop is confirmed: from then on the tab shows a
// terminal state and ignores poll failures (the daemon serving this page is
// going away, that is expected).
let daemonStopping = false;

// --- Daemon tab ------------------------------------------------------------
// A single status view over GET /daemon/status. It does NOT poll on its own:
// lib/daemon_status.js (the footer's 10s interval) tracks the latest status and
// calls renderDaemonDetail when this tab is showing. render() fires one
// immediate refresh for a fresh value and paints from whatever is already cached.

function daemonKv(label, valueNode) {
  return el("div", { class: "kv" }, el("span", { class: "k" }, label), valueNode);
}
// String fields (status, started_at, heartbeat_at, detail): text node, escaped.
function daemonKvText(label, value) {
  const v = value == null || value === ""
    ? el("span", { class: "v na" }, "unavailable")
    : el("span", { class: "v" }, String(value));
  return daemonKv(label, v);
}
// Numeric fields (pid): fmt() -> "unavailable" span for null/non-number.
function daemonKvNum(label, value, unit) {
  const v = el("span", { class: "v" });
  v.innerHTML = fmt(value, unit);
  return daemonKv(label, v);
}
// Bool fields (pid_alive, stale): yes / no, "unavailable" when the key is absent.
function daemonKvBool(label, value) {
  const v = value == null
    ? el("span", { class: "v na" }, "unavailable")
    : el("span", { class: "v" }, value ? "yes" : "no");
  return daemonKv(label, v);
}
// Heartbeat age reuses humanAge (same seconds unit as book_age_seconds).
function daemonKvAge(label, seconds) {
  const v = seconds == null
    ? el("span", { class: "v na" }, "unavailable")
    : el("span", { class: "v" }, humanAge(seconds));
  return daemonKv(label, v);
}

function daemonStartHint() {
  return el("p", { class: "age-line" },
    "The daemon can't be started from here. It's the process that serves this page. Run ",
    el("code", { class: "mono" }, "wfm daemon start"),
    " from a terminal.");
}

function renderDaemon() {
  document.getElementById("list-pane").append(
    el("h1", null, "Daemon"),
    el("div", { id: "daemon-detail" }),
  );
  detailEmpty();
  refresh();
  renderDaemonDetail(latest());
}

function renderDaemonDetail(s) {
  const box = document.getElementById("daemon-detail");
  if (!box) return;

  if (daemonStopping) {
    box.replaceChildren(
      el("p", { class: "muted" },
        "daemon stopping. this page will stop responding shortly."),
      daemonStartHint(),
    );
    return;
  }

  if (!s) {
    box.replaceChildren(
      el("p", { class: "muted" }, "daemon status unavailable."),
      daemonStartHint(),
    );
    return;
  }

  const alive = s.status === "running" || s.status === "halted" || s.status === "stopping";

  const stopBtn = el("button", { class: "btn", type: "button" }, "Stop daemon");
  stopBtn.disabled = !alive;
  stopBtn.addEventListener("click", async () => {
    const ok = window.confirm(
      "Stop the daemon?\n\n" +
      "This stops the daemon AND disconnects this dashboard, because the daemon is " +
      "the process serving this page. The dashboard will go unresponsive. To bring " +
      "it back, run `wfm daemon start` from a terminal."
    );
    if (!ok) return;
    stopBtn.disabled = true;
    try {
      await api("/daemon/stop", { method: "POST" });
      daemonStopping = true;
    } catch (err) {
      // A fetch rejection is a TypeError: the daemon dropped the connection as it
      // shut down. That is the expected outcome, so latch the terminal state.
      // An Error from api() is a real 4xx/5xx: the daemon is still up and refused,
      // so surface it and leave the tab live.
      if (err instanceof TypeError) {
        daemonStopping = true;
      } else {
        daemonStopping = false;
        showError(err.message);
      }
    }
    renderDaemonDetail(latest());
  });

  box.replaceChildren(
    el("div", { class: "stat-block" },
      el("h2", null, "Status"),
      daemonKvText("Status", s.status),
      daemonKvNum("PID", s.pid),
      daemonKvBool("PID alive", s.pid_alive),
      daemonKvText("Started", s.started_at),
      daemonKvText("Last heartbeat", s.heartbeat_at),
      daemonKvAge("Heartbeat age", s.heartbeat_age_seconds),
      daemonKvBool("Stale", s.stale),
      daemonKvText("Detail", s.detail),
    ),
    el("div", { class: "detail-actions" }, stopBtn),
    daemonStartHint(),
  );
}

subscribe((status) => {
  if (document.getElementById("daemon-detail")) renderDaemonDetail(status);
});

export default { id: "daemon", label: "Daemon", render: renderDaemon };
