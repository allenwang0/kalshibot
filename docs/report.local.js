/* kalshibot report: charts drawn from the inlined #kb-data JSON with report-kit v1 helpers. */
(function () {
  "use strict";
  var D = RK.data("kb-data");
  if (!D || !D.n) return;
  var MODEL = { key: "model", name: "Model", color: "var(--series-1)" };
  var MARKET = { key: "market", name: "Market mid", color: "var(--series-2)" };
  var f3 = function (v) { return (v < 0 ? "−" : "") + Math.abs(v).toFixed(3); };
  var f2 = function (v) { return (+v).toFixed(2); };
  /* Axis range on round steps so the first and last ticks sit on the plot edge. */
  var span = function (lo, hi, zero) {
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    var raw = (hi - lo) / 4, e = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / e;
    var st = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
    return { xmin: Math.floor(lo / st) * st, xmax: Math.ceil(hi / st) * st, ticks: { step: st } };
  };
  var MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  var mon = function (t) { var d = new Date(t * 1000); return MONTHS[d.getUTCMonth()] + " " + d.getUTCDate(); };

  /* Hero: model minus market, with bootstrap intervals */
  var dl = D.delta_log_loss, db = D.delta_brier;
  var drows = [{ label: "Log loss", d: dl }, { label: "Brier score", d: db }];
  var lim = Math.max.apply(null, drows.map(function (r) { return Math.max(Math.abs(r.d[1]), Math.abs(r.d[2])); }));
  var ax = span(-lim, lim);
  RKCharts.dotRange("#c-delta", {
    title: "Model minus market score, with 95 % bootstrap intervals",
    series: [{ key: "d", name: "Model − market", color: "var(--series-1)" }],
    rows: drows.map(function (r) { return { label: r.label, series: { d: { lo: r.d[1], mid: r.d[0], hi: r.d[2], tip: f3(r.d[0]) + " [" + f3(r.d[1]) + ", " + f3(r.d[2]) + "]" } } }; }),
    xmin: ax.xmin, xmax: ax.xmax, ticks: ax.ticks, format: f3, xLabel: "Model − market (negative favors the model)",
    refs: [{ x: 0, label: "No difference" }]
  });
  RKCharts.table("#t-delta", { caption: "Model minus market score over " + D.n + " contracts; lower is better for both scores",
    columns: [{ key: "m", label: "Score" }, { key: "d", label: "Model − market" }, { key: "lo", label: "95 % CI low" }, { key: "hi", label: "95 % CI high" }],
    rows: drows.map(function (r) { return { m: r.label, d: f3(r.d[0]), lo: f3(r.d[1]), hi: f3(r.d[2]) }; }) });

  /* Calibration: observed frequency per forecast bin, Wilson 95 % CI, mean forecast as a ring */
  var bins = {};
  [["model", D.model_reliability], ["market", D.market_reliability]].forEach(function (p) {
    p[1].forEach(function (b) { var k = b.lo.toFixed(1); (bins[k] = bins[k] || { lo: b.lo, hi: b.hi })[p[0]] = b; });
  });
  var keys = Object.keys(bins).sort();
  var crow = keys.map(function (k) {
    var b = bins[k], s = {};
    ["model", "market"].forEach(function (src) { var x = b[src]; if (x) s[src] = { lo: x.ci_lo, mid: x.freq, hi: x.ci_hi, mark: x.mean_p,
      tip: x.yes + " of " + x.count + " settled YES (" + f2(x.freq) + ", 95 % CI " + f2(x.ci_lo) + "–" + f2(x.ci_hi) + "); mean forecast " + f2(x.mean_p) }; });
    var n = function (src) { return b[src] ? b[src].count : 0; };
    return { label: b.lo.toFixed(1) + "–" + b.hi.toFixed(1) + " · n " + n("model") + " / " + n("market"), series: s };
  });
  RKCharts.dotRange("#c-cal", { title: "Observed YES rate per forecast bin, model and market", series: [MODEL, MARKET], rows: crow,
    xmin: 0, xmax: 1, format: f2, xLabel: "Share of contracts that settled YES" });
  var trows = [];
  keys.forEach(function (k) { ["model", "market"].forEach(function (src) { var x = bins[k][src]; if (x) trows.push({ bin: bins[k].lo.toFixed(1) + "–" + bins[k].hi.toFixed(1), src: src === "model" ? "Model" : "Market mid", n: x.count, yes: x.yes, p: f2(x.mean_p), f: f2(x.freq), ci: f2(x.ci_lo) + "–" + f2(x.ci_hi) }); }); });
  RKCharts.table("#t-cal", { caption: "Calibration bins, 95 % Wilson intervals",
    columns: [{ key: "bin", label: "Forecast bin" }, { key: "src", label: "Forecaster", align: "left" }, { key: "n", label: "Contracts" }, { key: "yes", label: "Settled YES" }, { key: "p", label: "Mean forecast" }, { key: "f", label: "Observed" }, { key: "ci", label: "95 % CI" }],
    rows: trows });

  /* P&L by settlement */
  if (D.pnl) {
    var P = D.pnl, t0 = Date.parse(P.start) / 1000;
    var px = [t0].concat(P.steps.map(function (s) { return Date.parse(s[0]) / 1000; }));
    var after = [0].concat(P.steps.map(function (s) { return s[2]; })), before = [0].concat(P.steps.map(function (s) { return s[1]; }));
    var usd = function (v) { return (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 }); };
    RKCharts.line("#c-pnl", { title: "Cumulative paper P&L after and before fees", x: px, xFormat: function (t) { return MONTHS[new Date(t * 1000).getUTCMonth()] + " " + new Date(t * 1000).getUTCFullYear(); },
      series: [{ key: "after", name: "After fees", color: "var(--series-1)", y: after }, { key: "before", name: "Before fees", color: "var(--series-neutral)", y: before, dash: true }],
      format: usd, yUnit: "", yLabel: "Cumulative P&L (USD)", xLabel: "Settlement date (UTC)", refs: [{ y: 0, label: "" }] });
    RKCharts.table("#t-pnl", { caption: "Cumulative paper P&L at each settlement (USD)",
      columns: [{ key: "d", label: "Settlement (UTC)" }, { key: "a", label: "After fees" }, { key: "b", label: "Before fees" }],
      rows: P.steps.map(function (s) { return { d: s[0].slice(0, 10), a: f2(s[2]), b: f2(s[1]) }; }) });
  }

  /* Edge vs realized */
  if (D.edges) {
    var ex = span(Math.min.apply(null, D.edges.bins.map(function (b) { return Math.min(b.ci_lo, b.edge); })),
                  Math.max.apply(null, D.edges.bins.map(function (b) { return Math.max(b.ci_hi, b.edge); })), true);
    var c1 = function (v) { return (v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + " ¢"; };
    RKCharts.dotRange("#c-edge", { title: "Mean realized P&L per contract by model edge bin", series: [{ key: "r", name: "Realized", color: "var(--series-1)" }],
      rows: D.edges.bins.map(function (b) { return { label: b.lo + " to " + b.hi + " ¢ · n " + b.n, series: { r: { lo: b.ci_lo, mid: b.mean, hi: b.ci_hi, mark: b.edge,
        tip: "mean realized " + c1(b.mean) + " (95 % CI " + c1(b.ci_lo) + " to " + c1(b.ci_hi) + "); mean edge " + c1(b.edge) + "; " + b.n + " fills" } } }; }),
      xmin: ex.xmin, xmax: ex.xmax, ticks: ex.ticks, format: function (v) { return (v < 0 ? "−" : "") + Math.abs(Math.round(v)); }, xLabel: "Realized P&L per contract (¢)", refs: [{ x: 0, label: "" }] });
    RKCharts.table("#t-edge", { caption: "Fills grouped by model edge after fees (cents per contract)",
      columns: [{ key: "b", label: "Edge bin (¢)" }, { key: "n", label: "Fills" }, { key: "e", label: "Mean edge" }, { key: "m", label: "Mean realized" }, { key: "ci", label: "95 % CI" }],
      rows: D.edges.bins.map(function (b) { return { b: b.lo + " to " + b.hi, n: b.n, e: c1(b.edge), m: c1(b.mean), ci: c1(b.ci_lo) + " to " + c1(b.ci_hi) }; }) });
  }

  /* Contract explorer */
  var C = D.contracts, sel = document.getElementById("kb-contract");
  if (!C || !sel) return;
  function draw(i) {
    var c = C[i], el = document.getElementById("c-ts");
    el.innerHTML = ""; el.classList.remove("plot");
    var fresh = el.cloneNode(false); el.parentNode.replaceChild(fresh, el);
    RKCharts.line(fresh, { title: "Model and market mid for " + c.ticker, x: c.t, xFormat: mon, zero: false, format: f2,
      series: [{ key: "model", name: "Model", color: "var(--series-1)", y: c.model },
               { key: "mid", name: "Market mid", color: "var(--series-2)", y: c.mid, dash: true,
                 lo: c.bid.map(function (v, j) { return v == null ? c.mid[j] : v / 100; }), hi: c.ask.map(function (v, j) { return v == null ? c.mid[j] : v / 100; }) }],
      yLabel: "Probability of YES (band: bid to ask)", xLabel: "Time (UTC)" });
    document.getElementById("kb-status").textContent = c.result ? "Settled " + c.result.toUpperCase() + " · FOMC decision " + c.meeting : "Not settled · FOMC decision " + c.meeting;
    RKCharts.table("#t-ts", { caption: c.ticker + ": one row per logged cycle",
      columns: [{ key: "t", label: "Time (UTC)" }, { key: "m", label: "Model" }, { key: "x", label: "Market mid" }, { key: "b", label: "Bid (¢)" }, { key: "a", label: "Ask (¢)" }],
      rows: c.t.map(function (t, j) { return { t: new Date(t * 1000).toISOString().slice(0, 16).replace("T", " "), m: c.model[j] == null ? null : f2(c.model[j]), x: f2(c.mid[j]), b: c.bid[j], a: c.ask[j] }; }) });
    var fig = fresh.closest(".figure"), seg = fig && fig.querySelector("[data-view-toggle] [aria-checked='true']");
    if (seg && seg.dataset.value === "table") fresh.hidden = true;
  }
  sel.addEventListener("change", function () { draw(+sel.value); });
  draw(+sel.value);
})();
