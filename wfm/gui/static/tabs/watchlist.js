import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";
import { claimList, isCurrentList } from "../lib/panes.js";
import { selectItem, empty as detailEmpty } from "../lib/detail.js";

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

export default { id: "watchlist", label: "Watchlist", render: renderWatchlist };
