import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";
import { claimList, isCurrentList } from "../lib/panes.js";
import { signClass, numTd, statLine } from "../lib/fmt.js";
import { empty as detailEmpty } from "../lib/detail.js";

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

export default { id: "ledger", label: "Ledger", render: renderLedger };
