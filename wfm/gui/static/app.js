import { el, clearError } from "./lib/dom.js";
import { dispose as disposePriceChart, invalidate as invalidateDetail } from "./lib/detail.js";
import * as daemonStatus from "./lib/daemon_status.js";
import daemonTab from "./tabs/daemon.js";
import catalogTab from "./tabs/catalog.js";
import watchlistTab from "./tabs/watchlist.js";
import ledgerTab from "./tabs/ledger.js";
import signalsTab, { stopSignalsSocket } from "./tabs/signals.js";
import groupsTab, { invalidate as invalidateGroups } from "./tabs/groups.js";

// Order here is the order in the rail. Built from the modules themselves, so a tab
// cannot advertise a label whose render function has drifted away from it.
const TABS = [catalogTab, watchlistTab, signalsTab, groupsTab, daemonTab, ledgerTab];

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
  invalidateGroups();
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
