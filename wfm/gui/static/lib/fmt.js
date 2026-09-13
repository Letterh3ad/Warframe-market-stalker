import { el } from "./dom.js";

// --- null-honest formatter -------------------------------------------------
// CONTRACT: fmt ALWAYS returns a STRING (never an Element).
//   null / undefined      -> '<span class="na">unavailable</span>'  (HTML string)
//   non-number (any type)  -> '<span class="na">unavailable</span>'  (HTML string)
//   number                 -> String(value) + (unit || "")          ('0' for 0)
// Output is HTML for innerHTML / template-literal use ONLY, never textContent:
// the unavailable branches return a literal <span> tag. The non-number guard
// keeps a later task's stray server string from becoming an injection vector.
// 0 is a real value and renders as '0' (typeof 0 === "number").
function fmt(value, unit) {
  if (value == null) return '<span class="na">unavailable</span>';
  if (typeof value !== "number") return '<span class="na">unavailable</span>';
  return `${value}${unit || ""}`;
}

// One line per entirely-absent block (from provenance.missing), naming it and the
// relevant sample counts from provenance.samples. NOT a grid of unavailable rows.
const MISSING_BLOCK_LINE = {
  price: (s) => `price history unavailable (${s.price_90d || 0} candles in 90d, ${s.price_7d || 0} in 7d)`,
  book: () => "order book unavailable (no snapshot fetched)",
  seasonality: (s) => `seasonality unavailable (${s.seasonality_bucket || 0} bucket / ${s.seasonality_total || 0} total observations)`,
  market: (s) => `market cohort unavailable (${s.market_cohort || 0} cohort members)`,
};

function missingLine(name, samples) {
  const build = MISSING_BLOCK_LINE[name];
  return el("div", { class: "missing-line" }, build ? build(samples) : `${name} unavailable`);
}

// Bridge fmt()'s HTML-STRING return into the DOM: build a <tbody> via innerHTML
// from [label, fmtOutput] pairs. SAFE because every label here is a hard-coded
// literal and every value is fmt() output (numbers/null only) - no server string
// ever reaches this innerHTML. Server strings (name, slug, tags, market.tag) go
// through el() text nodes instead.
function statTable(pairs) {
  const tbody = el("tbody");
  tbody.innerHTML = pairs
    .map(([label, html]) => `<tr><th>${label}</th><td>${html}</td></tr>`)
    .join("");
  return el("table", { class: "stat-table" }, tbody);
}

// --- fmt() -> DOM bridges (Task 9) ----------------------------------------
// fmt() returns an HTML STRING (numbers / null / non-number only -> never a
// server string). These helpers drop that string into a fresh node via
// innerHTML; SAFE for the same reason statTable's innerHTML is - no server
// string ever reaches it. Server strings (name, slug) use el() text nodes.
function signClass(value) {
  if (!Number.isFinite(value) || value === 0) return null;
  return value > 0 ? "val-up" : "val-down";
}
function numTd(value, unit, colorClass) {
  const td = el("td", { class: colorClass ? `mono ${colorClass}` : "mono" });
  td.innerHTML = fmt(value, unit);
  return td;
}
// colorize is opt-in: only fields with real gain/loss semantics (realized profit,
// unrealized profit, lot profit) get the red/green class. A trade count, a
// confidence, or a magnitude has no sign meaning and stays neutral.
function statLine(label, value, { unit, colorize } = {}) {
  const v = el("span", { class: "v" });
  const cls = colorize ? signClass(value) : null;
  if (cls) v.classList.add(cls);
  v.innerHTML = fmt(value, unit);
  return el("div", { class: "kv" }, el("span", { class: "k" }, label), v);
}

export { fmt, missingLine, statTable, signClass, numTd, statLine };
