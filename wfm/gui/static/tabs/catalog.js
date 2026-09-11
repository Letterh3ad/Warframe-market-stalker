import { api } from "../lib/api.js";
import { el, showError } from "../lib/dom.js";
import { selectItem, empty as detailEmpty } from "../lib/detail.js";

// Paging and query state for the Catalog tab. Local to this module: the detail
// pane takes its slug and rank as arguments, so nothing else reads this.
const state = {
  catalog: { q: "", offset: 0, limit: 100, total: 0 },
};

let catalogDebounce = null;
let catalogReqSeq = 0;

function renderCatalog() {
  const pane = document.getElementById("list-pane");
  const input = el("input", {
    type: "search",
    placeholder: "search by name or tag...",
    value: state.catalog.q,
    oninput: (e) => {
      const q = e.target.value;
      clearTimeout(catalogDebounce);
      catalogDebounce = setTimeout(() => {
        state.catalog.q = q;
        state.catalog.offset = 0;
        loadCatalog();
      }, 250);
    },
  });
  pane.append(
    el("h1", null, "Catalog"),
    el("div", { class: "catalog-search" }, input),
    el("div", { id: "catalog-body" }),
  );
  detailEmpty();
  loadCatalog();
}

async function loadCatalog() {
  const body = document.getElementById("catalog-body");
  if (!body) return;
  const seq = ++catalogReqSeq;
  body.replaceChildren(el("p", { class: "muted" }, "loading..."));

  const { q, offset, limit } = state.catalog;
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q) params.set("q", q);

  let data;
  try {
    data = await api(`/catalog?${params}`);
  } catch (e) {
    if (seq !== catalogReqSeq) return;
    body.replaceChildren(el("p", { class: "muted" }, "could not load catalog."));
    showError(e.message);
    return;
  }
  if (seq !== catalogReqSeq) return;
  // No unconditional clearError() here: the detail pane (Task 7) raises errors
  // from Refresh / watchlist actions, and a background catalog refetch or a
  // tab-switch back to Catalog must not silently wipe one the user hasn't seen.
  // The banner clears only on dismiss or when a new showError replaces it.
  state.catalog.total = data.total || 0;
  paintCatalog(body, data.items || []);
}

function paintCatalog(body, items) {
  if (items.length === 0) {
    body.replaceChildren(el("p", { class: "muted" }, "no items match"), buildPager());
    return;
  }
  const rows = items.map((it) => {
    const tr = el("tr", {
      class: "row",
      dataset: { slug: it.slug },
      onclick: () => {
        for (const s of body.querySelectorAll("tr.row.selected")) s.classList.remove("selected");
        tr.classList.add("selected");
        selectItem(it.slug, it.canonical_rank, it);
      },
    },
      el("td", null,
        it.name,
        it.is_set ? el("span", { class: "set-marker" }, "set") : null,
      ),
      el("td", null, ...(it.tags || []).map((t) => el("span", { class: "tag-pill" }, t))),
      el("td", { class: "mono" }, String(it.canonical_rank)),
    );
    return tr;
  });
  const table = el("table", null,
    el("thead", null, el("tr", null,
      el("th", null, "Name"), el("th", null, "Tags"), el("th", null, "Rank"))),
    el("tbody", null, ...rows),
  );
  body.replaceChildren(el("div", { class: "table-scroll" }, table), buildPager());
}

function buildPager() {
  const { offset, limit, total } = state.catalog;
  const first = total === 0 ? 0 : Math.min(offset + 1, total);
  const last = Math.min(offset + limit, total);
  const prev = el("button", {
    class: "btn", type: "button",
    onclick: () => { state.catalog.offset = Math.max(0, offset - limit); loadCatalog(); },
  }, "‹ prev");
  prev.disabled = offset === 0;
  const next = el("button", {
    class: "btn", type: "button",
    onclick: () => { state.catalog.offset = offset + limit; loadCatalog(); },
  }, "next ›");
  next.disabled = offset + limit >= total;
  return el("div", { class: "pager" },
    prev,
    el("span", null, `showing ${first}–${last} of ${total}`),
    next,
  );
}

export default { id: "catalog", label: "Catalog", render: renderCatalog };
