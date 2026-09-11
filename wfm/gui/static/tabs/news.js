import { api } from "../lib/api.js";
import { el, showError, humanAge } from "../lib/dom.js";
import { fmt, signClass } from "../lib/fmt.js";
import { claimList, isCurrentList } from "../lib/panes.js";
import { selectItem, empty as detailEmpty } from "../lib/detail.js";

// Poll only while a job is live. The daemon ticks hourly on its own, so an idle
// tab has nothing to watch and should cost nothing.
const JOB_POLL_MS = 2000;

const state = { eventType: "", offset: 0, limit: 50, total: 0 };
let jobTimer = null;

const EVENT_TYPES = [
  ["", "all"],
  ["vault_in", "vault in"],
  ["vault_out", "vault out"],
  ["prime_access", "prime access"],
  ["buff", "buff"],
  ["nerf", "nerf"],
  ["drop_rate_change", "drop rate"],
  ["rework", "rework"],
  ["new_content", "new content"],
  ["other", "other"],
];

function renderNews() {
  document.getElementById("list-pane").append(
    el("h1", null, "News"),
    el("div", { id: "news-status" }),
    el("div", { id: "news-chips" }),
    el("div", { id: "news-feed" }),
  );
  detailEmpty();
  paintChips();
  loadStatus();
  loadEvents();
}

function paintChips() {
  const box = document.getElementById("news-chips");
  if (!box) return;
  box.replaceChildren(
    ...EVENT_TYPES.map(([value, label]) => {
      const active = value === state.eventType;
      return el("button", {
        class: active ? "btn accent" : "btn",
        type: "button",
        onclick: () => {
          state.eventType = value;
          state.offset = 0;
          paintChips();
          loadEvents();
        },
      }, label);
    }),
  );
}

// --- status strip + job control -------------------------------------------

async function loadStatus() {
  if (!document.getElementById("news-status")) return;
  let status;
  try {
    status = await api("/news/status");
  } catch (e) {
    showError(e.message);
    return;
  }
  if (!document.getElementById("news-status")) return;
  paintStatus(status);
  if (status.job && status.job.running) pollJob();
}

function paintStatus(status) {
  const box = document.getElementById("news-status");
  if (!box) return;
  const job = status.job;
  const busy = !!(job && job.running);

  const refreshBtn = el("button", {
    class: "btn", type: "button",
    onclick: () => startJob("refresh"),
  }, "Refresh");
  const classifyBtn = el("button", {
    class: "btn", type: "button",
    onclick: () => startJob("classify"),
  }, "Classify");
  refreshBtn.disabled = busy;
  classifyBtn.disabled = busy;

  const colonIdx = (status.classifier || "").indexOf(":");
  const backend = colonIdx === -1 ? status.classifier : status.classifier.slice(0, colonIdx);
  const model = colonIdx === -1 ? null : status.classifier.slice(colonIdx + 1);

  box.replaceChildren(
    el("div", { class: "kv" }, el("span", { class: "k" }, "backend"), el("span", { class: "v" }, backend)),
    el("div", { class: "kv" }, el("span", { class: "k" }, "model"), el("span", { class: "v" }, model || "—")),
    el("div", { class: "kv" }, el("span", { class: "k" }, "articles"), fmtSpan("v", "", status.articles)),
    el("div", { class: "kv" }, el("span", { class: "k" }, "pending"), fmtSpan("v", "", status.pending)),
    busy
      ? el("div", { class: "kv" }, el("span", { class: "k" }, "job"),
          el("span", { class: "v" }, `${job.kind} running (${job.done}/${job.total})`))
      : null,
    el("div", { class: "detail-actions" }, refreshBtn, classifyBtn),
  );
}

async function startJob(kind) {
  let body;
  try {
    body = await api(`/news/${kind}`, { method: "POST" });
  } catch (e) {
    showError(e.message);
    return;
  }
  if (body.started === false) {
    showError(body.reason);
    return;
  }
  pollJob();
}

function pollJob() {
  clearTimeout(jobTimer);
  jobTimer = setTimeout(async () => {
    if (!document.getElementById("news-status")) return; // tab switched away
    let status;
    try {
      status = await api("/news/status");
    } catch (e) {
      showError(e.message);
      return;
    }
    if (!document.getElementById("news-status")) return;
    paintStatus(status);
    if (status.job && status.job.running) pollJob();
    else loadEvents();
  }, JOB_POLL_MS);
}

// --- feed -------------------------------------------------------------------

async function loadEvents() {
  const box = document.getElementById("news-feed");
  if (!box) return;
  const seq = claimList();
  box.replaceChildren(el("p", { class: "muted" }, "loading..."));

  const { eventType, offset, limit } = state;
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (eventType) params.set("event_type", eventType);

  let data;
  try {
    data = await api(`/news/events?${params}`);
  } catch (e) {
    if (!isCurrentList(seq)) return;
    box.replaceChildren(el("p", { class: "muted" }, "could not load news."));
    showError(e.message);
    return;
  }
  if (!isCurrentList(seq)) return;
  state.total = data.total || 0;
  paintEvents(box, data.events || []);
}

function directionArrow(direction) {
  if (direction === "up") return "▲";
  if (direction === "down") return "▼";
  return "•";
}
function directionSign(direction) {
  if (direction === "up") return 1;
  if (direction === "down") return -1;
  return 0;
}
// effective_at / published_at are ISO timestamps, not the age-in-seconds humanAge
// expects; convert before handing it off.
function ageSeconds(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : (Date.now() - t) / 1000;
}

// fmt() returns an HTML STRING (see lib/fmt.js contract), safe for innerHTML only
// because its non-number branch is a hard-coded literal span. Never hand it to
// el() as a text child, which would show that markup as literal text instead of
// rendering it.
function fmtSpan(cls, prefix, value, unit) {
  const span = el("span", { class: cls });
  span.innerHTML = `${prefix}${fmt(value, unit)}`;
  return span;
}

function paintEvent(ev) {
  const header = el("div", null,
    el("span", { class: "tag-pill" }, ev.event_type),
    el("span", null, ` ${ev.subject_raw} `),
    el("span", { class: signClass(directionSign(ev.direction)) }, directionArrow(ev.direction)),
    fmtSpan("mono", "  strength ", ev.strength),
    fmtSpan("mono", "  confidence ", ev.confidence),
  );
  const excerpt = el("div", { class: "muted" }, ev.excerpt || "(no excerpt)");

  const slugLink = el("a", {
    href: "#",
    onclick: (e) => {
      e.preventDefault();
      selectItem(ev.slug, ev.rank, null);
    },
  }, ev.slug);
  const linkLine = el("div", null,
    slugLink,
    el("span", { class: "tag-pill" }, ev.link_method),
    fmtSpan("mono", " weight ", ev.weight),
  );

  const articleLink = el("a", { href: ev.article_url, target: "_blank", rel: "noopener" },
    ev.article_title || ev.article_url);
  const footer = el("div", { class: "muted" },
    articleLink,
    el("span", { class: "tag-pill" }, ev.source),
    el("span", null, humanAge(ageSeconds(ev.effective_at ?? ev.published_at))),
  );

  return el("div", { class: "stat-block" }, header, excerpt, linkLine, footer);
}

function buildPager() {
  const { offset, limit, total } = state;
  const first = total === 0 ? 0 : Math.min(offset + 1, total);
  const last = Math.min(offset + limit, total);
  const prev = el("button", {
    class: "btn", type: "button",
    onclick: () => { state.offset = Math.max(0, offset - limit); loadEvents(); },
  }, "‹ prev");
  prev.disabled = offset === 0;
  const next = el("button", {
    class: "btn", type: "button",
    onclick: () => { state.offset = offset + limit; loadEvents(); },
  }, "next ›");
  next.disabled = offset + limit >= total;
  return el("div", { class: "pager" },
    prev,
    el("span", null, `showing ${first}–${last} of ${total}`),
    next,
  );
}

function paintEvents(box, events) {
  if (events.length === 0) {
    box.replaceChildren(el("p", { class: "muted" }, "no events match"), buildPager());
    return;
  }
  box.replaceChildren(...events.map(paintEvent), buildPager());
}

export default { id: "news", label: "News", render: renderNews };
