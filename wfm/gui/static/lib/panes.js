// One list pane, last writer wins. Watchlist, Ledger and Groups all paint into the
// same node, so the counter is deliberately SHARED: a per-tab counter would let a
// slow /watchlist response resolve after a switch to Ledger and paint into it.
//
// Callers claim before fetching and check after every await:
//     const seq = claimList();
//     const rows = await api("/watchlist");
//     if (!isCurrentList(seq)) return;
let listSeq = 0;

function claimList() {
  return ++listSeq;
}

function isCurrentList(seq) {
  return seq === listSeq;
}

export { claimList, isCurrentList };
