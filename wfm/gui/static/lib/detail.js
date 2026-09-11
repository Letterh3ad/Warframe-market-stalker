import { api } from "./api.js";
import { el, showError, humanAge } from "./dom.js";
import { fmt, missingLine, statTable } from "./fmt.js";

// --- item detail pane (Task 7) -----------------------------------------------
// selectItem(slug, rank, meta): meta is the catalog/watchlist row that triggered
// the click. It carries `tags` and `is_set`, which GET /items/{slug} does NOT
// return, so the header needs it. meta is optional; without it tags are dropped
// and `is_set` falls back to a `_set` slug suffix.

function detailEmpty() {
  disposePriceChart();
  document.getElementById("detail-pane").replaceChildren(
    el("p", { class: "muted" }, "select an item")
  );
}



// --- price history chart (Task 8) ------------------------------------------
// The history fetch is fired alongside GET /items/{slug} (not after it) and its
// result cached in historyState keyed by detailSeq. Whichever of the two
// resolves last triggers the draw: renderDetail calls drawPriceChart() once the
// #price-chart container exists, and the history promise calls it again when the
// rows arrive. Stale responses (seq !== detailSeq) are dropped.
let priceChart = null;
let detailSeq = 0;
const historyState = { status: "pending", rows: null };

function disposePriceChart() {
  if (priceChart) {
    try { priceChart.remove(); } catch (_) { /* container already gone */ }
    priceChart = null;
  }
}

// Was a window resize listener in app.js's boot section; moved here because
// priceChart is module-private now, so only this module can reach it to resize.
window.addEventListener("resize", () => {
  const box = document.getElementById("price-chart");
  if (priceChart && box) priceChart.applyOptions({ width: box.clientWidth });
});

// Draws (or redraws) into #price-chart from historyState. No-op when the
// container is absent (detail pane not rendered yet). Always disposes the prior
// chart first so click-through does not leak an instance per selection.
function drawPriceChart() {
  const box = document.getElementById("price-chart");
  if (!box) return;
  disposePriceChart();
  box.replaceChildren();

  if (historyState.status === "pending") {
    box.append(el("p", { class: "chart-note" }, "loading chart…"));
    return;
  }
  if (historyState.status === "error") {
    box.append(el("p", { class: "chart-note" }, "chart unavailable"));
    return;
  }

  const rows = (historyState.rows || []).filter(
    (r) => r && r.open != null && r.high != null && r.low != null && r.close != null
  );
  if (rows.length < 2) {
    box.append(el("p", { class: "chart-note" },
      rows.length === 0
        ? "no history to chart"
        : `not enough history to chart (${rows.length} candle${rows.length === 1 ? "" : "s"})`));
    return;
  }
  if (!window.LightweightCharts) {
    box.append(el("p", { class: "chart-note" }, "chart unavailable"));
    return;
  }

  const css = getComputedStyle(document.documentElement);
  const c = (name) => css.getPropertyValue(name).trim();
  const chart = window.LightweightCharts.createChart(box, {
    width: box.clientWidth,
    height: 260,
    layout: { background: { color: c("--bg-inset") }, textColor: c("--text-dim") },
    grid: {
      vertLines: { color: c("--border") },
      horzLines: { color: c("--border") },
    },
    timeScale: { borderColor: c("--border") },
    rightPriceScale: { borderColor: c("--border") },
  });
  const series = chart.addCandlestickSeries({
    upColor: c("--up"), downColor: c("--down"),
    wickUpColor: c("--up"), wickDownColor: c("--down"),
    borderVisible: false,
  });
  series.setData(rows.map((r) => ({
    time: r.date, open: r.open, high: r.high, low: r.low, close: r.close,
  })));
  chart.timeScale().fitContent();
  priceChart = chart;
}

async function selectItem(slug, rank, meta) {
  const seq = ++detailSeq;
  disposePriceChart();
  historyState.status = "pending";
  historyState.rows = null;

  const pane = document.getElementById("detail-pane");
  pane.replaceChildren(el("p", { class: "muted" }, "loading..."));
  const ref = { slug, rank, meta: meta || {} };

  // Fire both requests now; render each as it resolves so a slow one does not
  // block the other.
  const itemP = api(`/items/${encodeURIComponent(slug)}?rank=${encodeURIComponent(rank)}`);
  const historyP = api(
    `/items/${encodeURIComponent(slug)}/history?rank=${encodeURIComponent(rank)}&days=90`
  );

  historyP.then(
    (rows) => {
      if (seq !== detailSeq) return;
      historyState.status = "ok";
      historyState.rows = Array.isArray(rows) ? rows : [];
      drawPriceChart();
    },
    (e) => {
      if (seq !== detailSeq) return;
      historyState.status = "error";
      historyState.rows = null;
      showError(e.message);
      drawPriceChart();
    },
  );

  let body;
  try {
    body = await itemP;
  } catch (e) {
    if (seq !== detailSeq) return;
    disposePriceChart();
    pane.replaceChildren(el("p", { class: "muted" }, "could not load item."));
    showError(e.message);
    return;
  }
  if (seq !== detailSeq) return;
  renderDetail(body, ref);
}

// Rank selector for mods, arcanes and anything else with a rank range.
//
// Built from body.ranks (the ranks that actually have candles), NOT from
// range(0, max_rank+1): in practice only rank 0 and each item's max see trade volume, so
// offering the intermediates would open panels with no data in them. The current rank is
// unioned in so the selector always shows where you are, even for a rank the catalog
// knows about but that has never traded.
//
// Falls back to the plain text it replaced when there is nothing to choose between, so
// single-rank items look exactly as before.
//
// Caveat worth knowing: switching rank re-renders price history and stats correctly, but
// the ORDER BOOK will read unavailable on any rank that is not polled. Book polling is
// watchlist-driven at canonical_rank only, so non-canonical ranks have no snapshot.
function rankPicker(body, ref) {
  const current = body.rank;
  const ranks = (Array.isArray(body.ranks) ? body.ranks : []).filter(
    (r) => typeof r === "number",
  );
  if (!ranks.includes(current)) ranks.push(current);
  ranks.sort((a, b) => a - b);

  if (ranks.length < 2) return el("span", null, `rank ${current}`);

  const top = ranks[ranks.length - 1];
  const label = (r) => (r === 0 ? "unranked" : r === top ? `rank ${r} (max)` : `rank ${r}`);

  const select = el("select", {
    class: "rank-select",
    "aria-label": "rank",
    // ref.meta carries tags and is_set, which GET /items/{slug} does not return, so it
    // must be passed through or the header loses them on every rank change.
    onchange: (e) => selectItem(ref.slug, Number(e.target.value), ref.meta),
  });
  for (const r of ranks) {
    const option = el("option", { value: String(r) }, label(r));
    if (r === current) option.selected = true;
    select.append(option);
  }
  return select;
}

function renderDetail(body, ref) {
  const pane = document.getElementById("detail-pane");
  // Refresh / watchlist re-renders replace the whole pane; drop the old chart
  // before its container node is discarded, then redraw from cached history.
  disposePriceChart();
  const price = body.price || {};
  const market = body.market || {};
  const book = body.book || {};
  const season = body.seasonality || {};
  const prov = body.provenance || { available: [], missing: [], samples: {} };
  const available = prov.available || [];
  const missing = prov.missing || [];
  const samples = prov.samples || {};

  const tags = ref.meta.tags || [];
  const isSet = ref.meta.is_set != null ? ref.meta.is_set : /_set$/.test(body.slug || "");

  const header = el("div", { class: "detail-header" },
    el("h1", null,
      body.name || body.slug,
      isSet ? el("span", { class: "set-marker" }, "set") : null,
    ),
    el("div", { class: "slug-line mono" }, `${body.slug}  ·  `, rankPicker(body, ref)),
    tags.length
      ? el("div", { class: "tags" }, ...tags.map((t) => el("span", { class: "tag-pill" }, t)))
      : null,
    el("div", { class: body.watched ? "watch-state on" : "watch-state" },
      body.watched ? "on watchlist" : "not watched"),
  );

  // --- Refresh button: the ONLY control that contacts warframe.market ---
  const refreshBtn = el("button", { class: "btn", type: "button" }, "Refresh live book (API call)");
  refreshBtn.addEventListener("click", async () => {
    // Re-rendering the SAME item: capture the current detailSeq without bumping it
    // (a bump would wrongly invalidate a concurrent legitimate selectItem). Bail if
    // a tab switch or a new item selection moved detailSeq during the await.
    const seq = detailSeq;
    const original = refreshBtn.textContent;
    refreshBtn.disabled = true;
    refreshBtn.textContent = "contacting warframe.market…";
    try {
      const fresh = await api(
        `/items/${encodeURIComponent(ref.slug)}?rank=${encodeURIComponent(ref.rank)}&refresh=true`
      );
      if (seq !== detailSeq) return;
      renderDetail(fresh, ref);
    } catch (e) {
      if (seq !== detailSeq) return;
      showError(e.message);
      refreshBtn.disabled = false;
      refreshBtn.textContent = original;
    }
  });

  // --- Watchlist toggle: re-fetches after so `watched` is authoritative ---
  const watchBtn = el("button", { class: "btn", type: "button" },
    body.watched ? "− Remove from watchlist" : "+ Add to watchlist");
  watchBtn.addEventListener("click", async () => {
    // Same-item re-render: capture detailSeq without bumping, bail if it moved.
    const seq = detailSeq;
    watchBtn.disabled = true;
    try {
      if (body.watched) {
        await api(
          `/watchlist/${encodeURIComponent(ref.slug)}/${encodeURIComponent(ref.rank)}`,
          { method: "DELETE" }
        );
      } else {
        await api("/watchlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: ref.slug, rank: ref.rank }),
        });
      }
      const fresh = await api(
        `/items/${encodeURIComponent(ref.slug)}?rank=${encodeURIComponent(ref.rank)}`
      );
      if (seq !== detailSeq) return;
      renderDetail(fresh, ref);
    } catch (e) {
      if (seq !== detailSeq) return;
      showError(e.message);
      watchBtn.disabled = false;
    }
  });

  const actions = el("div", { class: "detail-actions" }, refreshBtn, watchBtn);

  // --- Price history chart (filled by drawPriceChart once history resolves) ---
  const chartBlock = el("div", { class: "stat-block" },
    el("h2", null, "Price history"),
    el("div", { id: "price-chart", class: "price-chart" }),
  );

  // --- Statistics (price) ---
  const statsBlock = el("div", { class: "stat-block" }, el("h2", null, "Statistics"));
  if (available.includes("price")) {
    statsBlock.append(statTable([
      ["last close", fmt(price.last_close, "p")],
      ["median 7d", fmt(price.median_7d, "p")],
      ["median 30d", fmt(price.median_30d, "p")],
      ["median 90d", fmt(price.median_90d, "p")],
      ["robust z", fmt(price.robust_z)],
      ["percentile 90d", fmt(price.percentile_90d)],
      ["ATR 14d", fmt(price.atr_14d)],
      ["ATR %", fmt(price.atr_pct)],
      ["volume trend", fmt(price.volume_trend)],
      ["median volume 30d", fmt(price.median_volume_30d)],
      ["donchian position", fmt(price.donchian_position)],
    ]));
  } else {
    statsBlock.append(missingLine("price", samples));
  }

  // --- Market ---
  const marketBlock = el("div", { class: "stat-block" }, el("h2", null, "Market"));
  if (available.includes("market")) {
    marketBlock.append(statTable([
      ["excess return 7d", fmt(market.excess_return_7d)],
      ["cohort size", fmt(market.cohort_size)],
    ]));
    // market.tag is a server STRING; fmt would call it "unavailable". Render it as
    // an escaped text node, with a null guard of its own.
    marketBlock.append(
      el("div", { class: "kv" },
        el("span", { class: "k" }, "tag"),
        market.tag == null
          ? el("span", { class: "v na" }, "unavailable")
          : el("span", { class: "v" }, market.tag),
      )
    );
  } else {
    marketBlock.append(missingLine("market", samples));
  }

  // --- Seasonality (rows when the block is present, else one missing line) ---
  const seasonBlock = el("div", { class: "stat-block" }, el("h2", null, "Seasonality"));
  if (available.includes("seasonality")) {
    seasonBlock.append(statTable([
      ["bucket", fmt(season.bucket)],
      ["observed bucket", fmt(season.observed_bucket)],
      ["observed age (h)", fmt(season.observed_age_hours)],
      ["expected price", fmt(season.expected_price)],
      ["expected volume", fmt(season.expected_volume)],
      ["price deviation", fmt(season.price_deviation)],
      ["volume deviation", fmt(season.volume_deviation)],
      ["confidence", fmt(season.confidence)],
      ["best bucket next 48h", fmt(season.best_bucket_next_48h)],
    ]));
  } else {
    seasonBlock.append(missingLine("seasonality", samples));
  }

  // --- Book ---
  const bookBlock = el("div", { class: "stat-block" }, el("h2", null, "Order book"));
  if (available.includes("book")) {
    bookBlock.append(statTable([
      ["online best bid", fmt(book.online_best_bid, "p")],
      ["online best ask", fmt(book.online_best_ask, "p")],
      ["online spread", fmt(book.online_spread, "p")],
      ["online spread %", fmt(book.online_spread_pct)],
      ["stale share", fmt(book.stale_share)],
      ["online bid count", fmt(book.online_bid_count)],
      ["online ask count", fmt(book.online_ask_count)],
    ]));
  } else {
    bookBlock.append(missingLine("book", samples));
  }
  bookBlock.append(
    el("div", { class: "age-line" }, `book age: ${humanAge(body.book_age_seconds)}`)
  );

  pane.replaceChildren(header, actions, chartBlock, statsBlock, marketBlock, seasonBlock, bookBlock);
  drawPriceChart();
}

// `invalidate` is what tab switching needs: it bumps the sequence so an in-flight
// /items/{slug} or /groups/{name}/analysis that resolves after the switch cannot
// paint into whatever tab is now showing. Exported instead of the counter itself,
// because a mutable binding read across modules is exactly the bug this prevents.
function invalidate() {
  detailSeq++;
}

export { selectItem, detailEmpty as empty, disposePriceChart as dispose, invalidate };
