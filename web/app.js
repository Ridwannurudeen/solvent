/* SOLVENT — shared client. Builds nav/footer, fetches state, renders whatever
   mount points the current page exposes. Pages set <body data-page="..."> and
   include only the mount elements they need. */

const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
const short = (h) => (!h ? "—" : h.slice(0, 10) + "…" + h.slice(-6));
const fmtUsd = (n) =>
  "$" +
  Number(n).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const isTx = (h) => typeof h === "string" && /^0x[0-9a-fA-F]{64}$/.test(h);
const pct = (v) => {
  if (v == null) return "—";
  const p = +(v * 100).toFixed(1);
  return `${p > 0 ? "+" : ""}${p.toFixed(1)}%`;
};
const trendClass = (v) => {
  if (v == null) return "";
  const p = +(v * 100).toFixed(1);
  return p > 0 ? "up" : p < 0 ? "down" : "";
};
const BNB_LOGO =
  '<svg width="20" height="20" viewBox="0 0 32 32" aria-hidden="true"><path fill="#F0B90B" d="M16 3l4 4-4 4-4-4zM16 21l4 4-4 4-4-4zM7 12l4 4-4 4-4-4zM25 12l4 4-4 4-4-4zM16 12l4 4-4 4-4-4z"/></svg>';

const REGIME = {
  "risk-on": { cls: "rt" },
  neutral: { cls: "rn" },
  "risk-off": { cls: "ro" },
};
function regimeChip(r) {
  const m = REGIME[r] || { cls: "rn" };
  return `<span class="regime ${m.cls}"><span class="rdot"></span>${esc(r || "—")}</span>`;
}
function humanAge(s) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

/* ── chrome ─────────────────────────────────────────────── */
function buildChrome() {
  const page = document.body.dataset.page || "";
  const links = [
    ["how-it-works", "How it works"],
    ["dashboard", "Dashboard"],
    ["decisions", "Decisions"],
    ["proof", "Proof"],
  ];
  const nav = document.getElementById("nav");
  if (nav)
    nav.innerHTML = `
    <div class="nav"><div class="nav-inner">
      <a class="brand" href="/">${BNB_LOGO}<span class="name">SOLVENT</span><span class="chain">BNB CHAIN</span></a>
      <div class="nav-links">${links.map(([h, t]) => `<a href="/${h}" ${page === h ? 'class="active"' : ""}>${t}</a>`).join("")}</div>
      <div class="nav-right">
        <div class="nav-status" id="navStatus"><span class="dot"></span> connecting</div>
      </div>
      <button class="nav-toggle" id="navToggle" aria-label="Toggle menu">☰</button>
    </div></div>`;
  const tog = document.getElementById("navToggle");
  const nl = nav && nav.querySelector(".nav-links");
  if (tog && nl) {
    tog.addEventListener("click", () => nl.classList.toggle("open"));
    nl.querySelectorAll("a").forEach((a) =>
      a.addEventListener("click", () => nl.classList.remove("open")),
    );
  }
  const foot = document.getElementById("footer");
  if (foot)
    foot.innerHTML = `
    <footer><div class="foot-inner">
      <div class="foot-left">${BNB_LOGO.replace('width="20" height="20"', 'width="14" height="14"')}<span>Built on BNB Chain · BNB Hack: AI Trading Agent Edition</span></div>
      <div class="foot-links"><a href="/receipts">Receipts</a><a href="/verify">Verify</a><a href="/state">State</a><a href="/verify_receipts.py">Auditor script</a></div>
      <div class="foot-hash">chain head <span id="headHash">—</span> · <span class="updated" id="updated">refreshes every 60s</span></div>
    </div></footer>`;
}

/* ── toast / copy ───────────────────────────────────────── */
let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
}
function copyHash(h) {
  if (navigator.clipboard)
    navigator.clipboard.writeText(h).then(() => toast("Hash copied"));
}

function inferMode(entries) {
  for (let i = entries.length - 1; i >= 0; i--)
    for (const e of entries[i].receipt.executions || []) {
      if (isTx(e.tx_hash)) return "live";
      if (e.tx_hash) return "paper";
    }
  return null;
}

/* ── renderers (each guarded by mount presence) ─────────── */
function renderNavStatus(ver) {
  const el = $("#navStatus");
  if (el) {
    const coverage = ver.anchor_coverage || {};
    const unanchored =
      coverage.unanchored_count != null ? coverage.unanchored_count : null;
    const anchorNote =
      unanchored == null
        ? ""
        : ` · ${unanchored} receipt${unanchored === 1 ? "" : "s"} unanchored`;
    el.innerHTML = ver.ok
      ? `<span class="dot ok pulse"></span> local chain verified · ${ver.count} receipts${anchorNote}`
      : `<span class="dot bad"></span> local chain tampered`;
  }
  const hh = $("#headHash");
  if (hh) hh.textContent = ver.head_hash;
}

function renderHeroMeta(entries, st) {
  const el = $("#heroMeta");
  if (!el) return;
  const parts = [
    `<span class="dot ${st.alive ? "ok pulse" : "bad"}"></span> ${st.alive ? "running" : "stale"}`,
  ];
  if (st.heartbeat_age_s != null)
    parts.push(`last cycle ${humanAge(st.heartbeat_age_s)} ago`);
  const mode = inferMode(entries);
  if (mode) parts.push(`${mode} mode`);
  parts.push("BNB Smart Chain");
  el.innerHTML = parts.join(`<span class="sep">·</span>`);
}

function renderTeaser(entries, ver, st) {
  const el = $("#teaser");
  if (!el) return;
  const last = entries.length ? entries[entries.length - 1].receipt : null;
  const start =
    st.start_equity_usd || (entries.length ? entries[0].receipt.equity_usd : 0);
  const ret = last && start > 0 ? last.equity_usd / start - 1 : null;
  const cards = [
    ["Equity", last ? esc(fmtUsd(last.equity_usd)) : "—", ""],
    ["Return", pct(ret), trendClass(ret)],
    ["Decisions", `${ver.count}`, ""],
    ["Local chain", ver.ok ? "verified" : "tampered", ver.ok ? "up" : "down"],
  ];
  el.innerHTML = cards
    .map(
      ([k, v, c]) =>
        `<div class="t"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`,
    )
    .join("");
}

function renderStats(entries, ver, st) {
  const el = $("#stats");
  if (!el) return;
  const last = entries.length ? entries[entries.length - 1].receipt : null;
  const dataCost = entries.reduce(
    (s, e) =>
      s +
      (e.receipt.data_purchases || []).reduce(
        (a, p) => a + (p.cost_usdc || 0),
        0,
      ),
    0,
  );
  const headroom = last ? last.dq_headroom_pct * 100 : null;
  const start =
    st.start_equity_usd || (entries.length ? entries[0].receipt.equity_usd : 0);
  const ret = last && start > 0 ? last.equity_usd / start - 1 : null;
  const cards = [
    ["Equity", last ? esc(fmtUsd(last.equity_usd)) : "—"],
    ["Return", `<span class="${trendClass(ret)}">${pct(ret)}</span>`],
    ["DQ headroom", headroom == null ? "—" : `${headroom.toFixed(1)}%`],
    ["Decisions", `${ver.count}`],
    ["Data spend", `$${dataCost.toFixed(2)}`],
    [
      "Chain head",
      `<span class="mono" title="${esc(ver.head_hash)}">${short(ver.head_hash)}</span>`,
      true,
    ],
  ];
  el.innerHTML = cards
    .map(
      ([k, v, m]) =>
        `<div class="stat"><div class="k">${k}</div><div class="v${m ? " mono" : ""}">${v}</div></div>`,
    )
    .join("");
}

async function fetchBnb(entries) {
  if (entries.length < 2) return null;
  const startTime = Date.parse(entries[0].receipt.ts) - 3600 * 1000;
  const url = `https://data-api.binance.vision/api/v3/klines?symbol=BNBUSDT&interval=1h&startTime=${startTime}&limit=1000`;
  try {
    const kl = await fetch(url).then((r) =>
      r.ok ? r.json() : Promise.reject(),
    );
    return kl.map((k) => [k[0], parseFloat(k[4])]);
  } catch {
    return null;
  }
}
function bnbCloseAt(bnb, tsMs) {
  let c = null;
  for (let i = 0; i < bnb.length; i++) {
    if (bnb[i][0] <= tsMs) c = bnb[i][1];
    else break;
  }
  return c;
}

const CH = { W: 1000, H: 210, padX: 4, padY: 14 };
let chartState = null;
function renderChart(entries, bnb) {
  const svg = $("#curve");
  if (!svg) return;
  const legend = $("#chartLegend"),
    chart = $("#chart");
  chart.querySelectorAll(".lbl,.endpoint").forEach((n) => n.remove());
  if (entries.length < 2) {
    if (legend)
      legend.innerHTML = `<span class="muted">need at least two receipts to plot</span>`;
    svg.innerHTML = "";
    chartState = null;
    return;
  }
  const n = entries.length,
    eq = entries.map((e) => e.receipt.equity_usd),
    eqRet = eq.map((v) => (eq[0] > 0 ? v / eq[0] - 1 : 0));
  let bnbRet = null;
  if (bnb && bnb.length) {
    const p = entries.map((e) => bnbCloseAt(bnb, Date.parse(e.receipt.ts)));
    if (p[0]) bnbRet = p.map((v) => (v ? v / p[0] - 1 : null));
  }
  const all = eqRet.concat(bnbRet ? bnbRet.filter((v) => v != null) : []);
  let lo = Math.min(0, ...all),
    hi = Math.max(0, ...all);
  if (hi === lo) {
    hi += 0.01;
    lo -= 0.01;
  }
  const padv = (hi - lo) * 0.12;
  hi += padv;
  lo -= padv;
  const { W, H, padX, padY } = CH;
  const x = (i) => padX + ((W - 2 * padX) * i) / (n - 1),
    y = (v) => H - padY - ((H - 2 * padY) * (v - lo)) / (hi - lo);
  const line = (arr) =>
    arr
      .map((v, i) =>
        v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`,
      )
      .filter(Boolean)
      .join(" ");
  const grid = [0.25, 0.5, 0.75]
    .map((f) => {
      const g = lo + (hi - lo) * f;
      return `<line x1="${padX}" y1="${y(g).toFixed(1)}" x2="${W - padX}" y2="${y(g).toFixed(1)}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
    })
    .join("");
  const eqPts = line(eqRet);
  const areaPath = `M${x(0).toFixed(1)},${(H - padY).toFixed(1)} L${eqPts.split(" ").join(" L")} L${x(n - 1).toFixed(1)},${(H - padY).toFixed(1)} Z`;
  const last = eqRet[n - 1],
    bnbLast = bnbRet ? bnbRet[n - 1] : null;
  svg.innerHTML = `
    <defs><linearGradient id="ga" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f0b90b" stop-opacity=".16"/><stop offset="1" stop-color="#f0b90b" stop-opacity="0"/></linearGradient></defs>
    ${grid}
    <line x1="${padX}" y1="${y(0).toFixed(1)}" x2="${W - padX}" y2="${y(0).toFixed(1)}" stroke="var(--line-2)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
    <path d="${areaPath}" fill="url(#ga)"/>
    ${bnbRet ? `<polyline points="${line(bnbRet)}" fill="none" stroke="var(--text-2)" stroke-width="1.5" stroke-dasharray="6 5" vector-effect="non-scaling-stroke" opacity=".9"/>` : ""}
    <polyline points="${eqPts}" fill="none" stroke="var(--gold)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
  const mk = (cls, txt, style) => {
    const d = document.createElement("div");
    d.className = cls;
    if (txt != null) d.textContent = txt;
    d.style.cssText = style;
    chart.appendChild(d);
  };
  mk("lbl", pct(hi), "top:2px;left:2px;");
  mk("lbl", pct(lo), "bottom:2px;left:2px;");
  mk(
    "endpoint",
    null,
    `left:${((x(n - 1) / W) * 100).toFixed(2)}%;top:${y(last).toFixed(1)}px;`,
  );
  chartState = {
    n,
    pts: entries.map((e, i) => ({
      xf: x(i) / W,
      eqY: y(eqRet[i]),
      bnbY: bnbRet && bnbRet[i] != null ? y(bnbRet[i]) : null,
      eqR: eqRet[i],
      bnbR: bnbRet ? bnbRet[i] : null,
      ts: (e.receipt.ts || "").replace("T", " ").slice(5, 16),
    })),
  };
  const cx = $("#chartX");
  if (cx)
    cx.innerHTML = `<span>${esc(chartState.pts[0].ts)} UTC</span><span>${esc(chartState.pts[n - 1].ts)} UTC</span>`;
  if (legend)
    legend.innerHTML = `<span><span class="key"></span>SOLVENT <b class="${trendClass(last)}">${pct(last)}</b></span><span><span class="key bnb"></span>BNB hold <b class="dim">${pct(bnbLast)}</b></span>`;
}
function initChartHover() {
  const chart = $("#chart");
  if (!chart) return;
  const cross = document.createElement("div");
  cross.className = "cross";
  const dotEq = document.createElement("div");
  dotEq.className = "cdot eq";
  const dotBnb = document.createElement("div");
  dotBnb.className = "cdot bnb";
  const tip = document.createElement("div");
  tip.className = "tip";
  chart.append(cross, dotEq, dotBnb, tip);
  chart.addEventListener("mousemove", (ev) => {
    if (!chartState) return;
    const r = chart.getBoundingClientRect();
    let idx = Math.round(
      ((ev.clientX - r.left) / r.width) * (chartState.n - 1),
    );
    idx = Math.max(0, Math.min(chartState.n - 1, idx));
    const p = chartState.pts[idx],
      lx = p.xf * r.width;
    cross.style.display = "block";
    cross.style.left = lx + "px";
    dotEq.style.display = "block";
    dotEq.style.left = lx + "px";
    dotEq.style.top = p.eqY + "px";
    if (p.bnbY != null) {
      dotBnb.style.display = "block";
      dotBnb.style.left = lx + "px";
      dotBnb.style.top = p.bnbY + "px";
    } else dotBnb.style.display = "none";
    tip.style.display = "block";
    tip.innerHTML =
      `<div class="tip-d">${p.ts} UTC</div><div><span class="tip-k gold">SOLVENT</span> ${pct(p.eqR)}</div>` +
      (p.bnbR != null
        ? `<div><span class="tip-k">BNB hold</span> ${pct(p.bnbR)}</div>`
        : "");
    const tw = tip.offsetWidth || 140;
    tip.style.left = Math.min(Math.max(lx + 14, 4), r.width - tw - 4) + "px";
    tip.style.top = "6px";
  });
  chart.addEventListener("mouseleave", () =>
    [cross, dotEq, dotBnb, tip].forEach((n) => (n.style.display = "none")),
  );
}

function renderAlloc(entries, st) {
  const el = $("#alloc");
  if (!el) return;
  const last = entries.length ? entries[entries.length - 1].receipt : null;
  const eq = last ? last.equity_usd : st.start_equity_usd || 0;
  const sleeve =
    st.position && st.position.notional_usd ? st.position.notional_usd : 0;
  const floor = Math.max(0, eq - sleeve),
    fp = eq > 0 ? (floor / eq) * 100 : 100,
    sp = eq > 0 ? (sleeve / eq) * 100 : 0;
  const sym = st.position && st.position.symbol ? st.position.symbol : null;
  const holdings = Object.entries(st.holdings || {});
  el.innerHTML = `
    <div class="alloc-head">Allocation</div>
    <div class="alloc-bar"><div class="floor" style="width:${fp.toFixed(1)}%"></div>${sp > 0 ? `<div class="sleeve" style="width:${sp.toFixed(1)}%"></div>` : ""}</div>
    <div class="alloc-rows">
      <div class="alloc-row"><span class="l"><span class="sw" style="background:var(--line-2)"></span>Floor (stables)</span><span class="v">${esc(fmtUsd(floor))} · ${fp.toFixed(0)}%</span></div>
      ${sp > 0 ? `<div class="alloc-row"><span class="l"><span class="sw" style="background:var(--gold)"></span>Sleeve · ${esc(sym)}</span><span class="v">${esc(fmtUsd(sleeve))} · ${sp.toFixed(0)}%</span></div>` : `<div class="alloc-row"><span class="l"><span class="sw" style="background:var(--gold)"></span>Sleeve</span><span class="v muted">flat</span></div>`}
    </div>
    ${holdings.length ? `<div class="holdings-title">Holdings</div><div class="alloc-rows">${holdings.map(([s, u]) => `<div class="alloc-row"><span class="l mono">${esc(s)}</span><span class="v">${Number(u).toLocaleString("en-US", { maximumFractionDigits: 4 })}</span></div>`).join("")}</div>` : ""}`;
}

function renderLatest(entries) {
  const el = $("#latest");
  if (!el) return;
  if (!entries.length) {
    el.innerHTML = "";
    return;
  }
  const r = entries[entries.length - 1].receipt,
    adv = r.signals && r.signals.advisor;
  let html = `<div class="latest" ${adv && adv.thesis ? 'style="border-radius:8px 8px 0 0;margin-bottom:0"' : ""}>${regimeChip(r.regime)}<span class="thesis">“${esc(r.thesis || "—")}”</span><span class="mono muted" style="font-size:12px">F&amp;G ${r.signals?.fear_greed ?? "—"}</span><span class="ts">${esc((r.ts || "").replace("T", " ").slice(0, 16))} UTC</span></div>`;
  if (adv && adv.thesis)
    html += `<div class="advisor"><b>Advisor</b> (can only de-risk) · ${esc(adv.regime || "")}: ${esc(adv.thesis)}</div>`;
  el.innerHTML = html;
}

function renderIntents(intents) {
  if (!intents || !intents.length) return `<span class="muted">—</span>`;
  return intents
    .map(
      (i) =>
        `<div class="mono">${esc(i.kind || "")} ${esc(i.from || "")}<span class="muted"> → </span>${esc(i.to || "")} <span class="dim">· $${Number(i.notional_usd || 0).toFixed(2)}</span></div>`,
    )
    .join("");
}
function renderExecs(execs) {
  if (!execs || !execs.length) return `<span class="muted">—</span>`;
  return execs
    .map((e) => {
      const mark = e.ok
        ? `<span class="up">✓</span>`
        : `<span class="down">✗</span>`;
      const tx = isTx(e.tx_hash)
        ? `<a class="mono" href="https://bscscan.com/tx/${esc(e.tx_hash)}" target="_blank" rel="noopener">${short(e.tx_hash)}</a>`
        : `<span class="paper-mark mono">${esc(e.tx_hash || "—")}</span>`;
      return `<div>${mark} ${tx}</div>`;
    })
    .join("");
}

function renderTable(entries) {
  const t = $("#receipts");
  if (!t) return;
  const tbody = t.querySelector("tbody");
  if (!entries.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">no receipts yet</td></tr>`;
    return;
  }
  tbody.innerHTML = entries
    .slice()
    .reverse()
    .map((e) => {
      const r = e.receipt;
      const data =
        (r.data_purchases || [])
          .map(
            (p) =>
              `<div class="mono dim">${esc(p.tool)} ${p.cost_usdc > 0 ? `<span class="up">$${p.cost_usdc.toFixed(2)}</span>` : `<span class="muted">free</span>`}</div>`,
          )
          .join("") || `<span class="muted">—</span>`;
      return `<tr><td class="mono muted">${r.seq}</td><td class="mono dim" style="white-space:nowrap">${esc((r.ts || "").replace("T", " ").slice(0, 16))}</td><td>${data}</td><td>${regimeChip(r.regime)}</td><td class="dim" style="max-width:220px">${esc(r.thesis || "—")}</td><td>${renderIntents(r.intents)}</td><td>${renderExecs(r.executions)}</td><td class="hash mono" title="${esc(e.hash)} — click to copy" onclick="copyHash('${esc(e.hash)}')">${short(e.hash)}</td></tr>`;
    })
    .join("");
}

function renderSpend(entries, st) {
  const el = $("#spend");
  if (!el) return;
  const byTool = {};
  let total = 0,
    n = 0;
  for (const e of entries)
    for (const p of e.receipt.data_purchases || []) {
      n++;
      total += p.cost_usdc || 0;
      const t = byTool[p.tool] || (byTool[p.tool] = { count: 0, cost: 0 });
      t.count++;
      t.cost += p.cost_usdc || 0;
    }
  const x = st.x402 || {},
    cap = x.max_per_call_usd,
    budget = x.session_budget_usd;
  const rows = Object.entries(byTool)
    .sort((a, b) => b[1].cost - a[1].cost)
    .map(
      ([tool, v]) =>
        `<tr><td class="mono dim">${esc(tool)}</td><td class="num mono">${v.count}</td><td class="num mono">${v.cost > 0 ? "$" + v.cost.toFixed(4) : '<span class="muted">free</span>'}</td></tr>`,
    )
    .join("");
  el.innerHTML = `
    <div class="spend-strip"><div><div class="k">Total spend</div><div class="v">$${total.toFixed(4)}</div></div><div><div class="k">Purchases</div><div class="v">${n}</div></div><div><div class="k">Per-call cap</div><div class="v">${cap != null ? "$" + cap.toFixed(2) : "—"}</div></div><div><div class="k">Session budget</div><div class="v">${budget != null ? "$" + budget.toFixed(2) : "—"}</div></div></div>
    <table><thead><tr><th>Endpoint</th><th class="num">Calls</th><th class="num">Spend</th></tr></thead><tbody>${rows || `<tr><td colspan="3" class="muted">no data purchases yet</td></tr>`}</tbody></table>
    <div class="footnote">Paper mode uses free public feeds. Live mode pays per CMC x402 call${cap != null ? `, capped at $${cap.toFixed(2)}/call against a $${budget.toFixed(2)} session budget` : ""} — enforced in the signer, not in prose.</div>`;
}

function explorerBase(net) {
  return net && net.includes("testnet")
    ? "https://testnet.bscscan.com"
    : "https://bscscan.com";
}
function renderAnchors(st) {
  const el = $("#anchorsBody");
  if (!el) return;
  const net = st.anchor_network || "bsc-testnet";
  if (!st.anchors || !st.anchors.length) {
    el.innerHTML = `<div class="card-pad muted" style="font-size:13px">No anchors yet. The receipt chain head is committed on-chain daily at 23:00 UTC under the agent's ERC-8004 identity (${esc(net)}).</div>`;
    return;
  }
  const base = explorerBase(net);
  const coverage = st.anchor_coverage || {};
  const coverageHtml =
    coverage.local_count != null
      ? `<div class="card-pad" style="border-bottom:1px solid var(--line);display:grid;gap:6px">
          <div><b>Local chain verified through</b> ${Number(coverage.local_count || 0)} receipt${coverage.local_count === 1 ? "" : "s"}.</div>
          <div><b>On-chain anchored through</b> ${coverage.anchored_seq == null ? "no matching local receipt yet" : "receipt #" + esc(coverage.anchored_seq)}.</div>
          <div><b>Unanchored receipts</b> ${Number(coverage.unanchored_count || 0)}.</div>
        </div>`
      : "";
  el.innerHTML =
    coverageHtml +
    `<table><thead><tr><th>Day (UTC)</th><th>Anchored head</th><th>Transaction (${esc(net)})</th></tr></thead><tbody>` +
    st.anchors
      .map(
        (a) =>
          `<tr><td class="mono dim" style="white-space:nowrap">${esc(a.day)}</td><td class="hash mono" title="${esc(a.head_hash || "")}" onclick="copyHash('${esc(a.head_hash || "")}')">${short(a.head_hash)}</td><td>${isTx(a.tx_hash) ? `<a class="mono" href="${base}/tx/${esc(a.tx_hash)}" target="_blank" rel="noopener">${short(a.tx_hash)}</a>` : `<span class="paper-mark mono">${esc(a.tx_hash || "—")}</span>`}</td></tr>`,
      )
      .join("") +
    `</tbody></table>`;
}

const ERC8004_REGISTRY = {
  "bsc-testnet": "0x8004A818BFB912233c491871b3d84c89A494BD9e",
  "bsc-mainnet": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
};
const COMPETITION_REGISTRY = "0x212c61b9b72c95d95bf29cf032f5e5635629aed5"; // BNB Hack registry, BSC mainnet
function renderContracts(st) {
  const el = $("#contractsBody");
  if (!el) return;
  const net = st.anchor_network || "bsc-testnet";
  const base = explorerBase(net);
  const reg = ERC8004_REGISTRY[net] || ERC8004_REGISTRY["bsc-testnet"];
  const idSet = st.agent_id != null;
  el.innerHTML = `<div class="contracts">
    <div class="contract"><div class="k">ERC-8004 registry (${esc(net)})</div><a class="addr" href="${base}/address/${reg}" target="_blank" rel="noopener">${short(reg)}</a><div class="st">where the agent's identity and daily anchors are written</div></div>
    <div class="contract"><div class="k">BNB Hack registry</div><a class="addr" href="https://bscscan.com/address/${COMPETITION_REGISTRY}" target="_blank" rel="noopener">${short(COMPETITION_REGISTRY)}</a><div class="st">competition entry registry on BSC mainnet</div></div>
    <div class="contract"><div class="k">Agent identity</div><span class="addr ${idSet ? "" : "muted"}">${idSet ? "#" + esc(st.agent_id) : "not yet registered"}</span><div class="st">${idSet ? "SOLVENT's on-chain ERC-8004 id" : "appears once the identity is registered"}</div></div>
  </div>`;
}

async function load() {
  try {
    const [entries, ver, st] = await Promise.all([
      fetch("/receipts", { cache: "no-store" }).then((r) => r.json()),
      fetch("/verify", { cache: "no-store" }).then((r) => r.json()),
      fetch("/state", { cache: "no-store" }).then((r) => r.json()),
    ]);
    $("#errBanner")?.classList.remove("show");
    renderNavStatus(ver);
    renderHeroMeta(entries, st);
    renderTeaser(entries, ver, st);
    renderStats(entries, ver, st);
    renderAlloc(entries, st);
    if ($("#chart")) renderChart(entries, await fetchBnb(entries));
    renderLatest(entries);
    renderSpend(entries, st);
    renderTable(entries);
    renderAnchors(st);
    renderContracts(st);
    const note = $("#chartNote");
    if (note) {
      const m = inferMode(entries);
      note.textContent =
        m === "paper"
          ? "Paper-mode rehearsal — the flat line reflects the daily qualification trade's fee drag; the agent holds 100% floor until a confirmed risk-on signal."
          : "";
    }
    const up = $("#updated");
    if (up)
      up.textContent =
        "updated " + new Date().toISOString().slice(11, 19) + " UTC";
  } catch (err) {
    const ns = $("#navStatus");
    if (ns) ns.innerHTML = `<span class="dot bad"></span> offline`;
    const b = $("#errBanner");
    if (b) {
      b.textContent = `Could not reach the receipt log: ${err.message}`;
      b.classList.add("show");
    }
  }
}

buildChrome();
initChartHover();
load();
setInterval(load, 60000);
