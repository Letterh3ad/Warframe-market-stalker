import { api } from "./api.js";

// One poll, two consumers: the footer rail and the Daemon tab. Before this, the
// poll wrote a global, painted the rail, and read `activeTab` to decide whether to
// repaint the tab -- three jobs and a dependency on tab state. Subscribers remove
// the last one: the tab paints when it hears, and does not care when it is hidden.
const POLL_INTERVAL_MS = 10000;

let last = null;
const subscribers = [];

function subscribe(fn) {
  subscribers.push(fn);
  if (last !== null) fn(last);
  return () => {
    const at = subscribers.indexOf(fn);
    if (at !== -1) subscribers.splice(at, 1);
  };
}

function latest() {
  return last;
}

async function refresh() {
  let status;
  try {
    status = await api("/daemon/status");
  } catch (err) {
    status = { status: "unreachable", error: String(err) };
  }
  last = status;
  for (const fn of subscribers) fn(status);
  return status;
}

function start() {
  refresh();
  setInterval(refresh, POLL_INTERVAL_MS);
}

export { subscribe, refresh, latest, start };
