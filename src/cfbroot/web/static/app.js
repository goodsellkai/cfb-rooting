"use strict";

const $ = (id) => document.getElementById(id);

/* Percentages are always shown to hundredths: at a million simulations the
   interesting swings live in the second decimal place, and rounding them away
   makes distinct games look identical. */
const pct = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "—" : (100 * x).toFixed(d) + "%";
const signed = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "—" : (x >= 0 ? "+" : "−") + (100 * Math.abs(x)).toFixed(d);
const num = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "—" : x.toFixed(d);

let STATE = null;      // /api/state
let RESULT = null;     // last run: every remaining game x every metric
let POLLING = null;
let RAN = null;        // the inputs RESULT was produced from

const CONF_TITLE = {
  clear: "Survived Benjamini–Hochberg control over this slate — a resolved edge.",
  leaning: "The point estimate favours this side, but the 95% interval includes "
    + "zero, so treat the direction as a best guess.",
  thin: "One result is so unlikely that too few simulations landed there to say "
    + "anything reliable.",
};

// ---------------------------------------------------------------- bootstrap

async function boot() {
  try {
    STATE = await (await fetch("/api/state")).json();
  } catch (err) {
    banner("Could not load season data: " + err, false);
    return;
  }
  renderSeasonLine();
  fillTeams();
  fillMetrics();
  fillWeeks();
  const saved = localStorage.getItem("cfbroot.team");
  if (saved) $("team").value = saved;
  showNotes();
  updateStaleness();
}

function banner(text, ok) {
  const el = $("banner");
  el.textContent = text;
  el.className = "banner" + (ok ? " info" : "");
  el.hidden = !text;
}

function showNotes() {
  const notes = (STATE.notes || []).filter(Boolean);
  if (!STATE.has_api_key) {
    banner("No CFBD API key found — showing a fabricated demo season. "
      + "Add CFBD_API_KEY to .env and restart for real data.", false);
  } else if (notes.length) {
    banner(notes[0], true);
  } else {
    banner("", true);
  }
}

function ago(iso) {
  if (!iso) return "";
  const t = new Date(iso);
  if (isNaN(t)) return "";
  const h = (Date.now() - t.getTime()) / 3.6e6;
  if (h < 1) return "just now";
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function renderSeasonLine() {
  $("seasonline").textContent =
    `${STATE.year} season · week ${STATE.current_week} · `
    + `${STATE.games_played} played, ${STATE.games_remaining} to simulate`;
  $("topmeta").innerHTML =
    `<span class="chip">${esc(STATE.rating_label)} <b>${esc(ago(STATE.ratings_updated) || "—")}</b></span>`;
}

function team(idx) {
  return (STATE.team_index || {})[String(idx)] || {};
}

function fillTeams() {
  const dl = $("teamlist");
  dl.innerHTML = "";
  for (const t of STATE.teams) {
    const o = document.createElement("option");
    o.value = t.name;
    o.label = `${t.conference || "Independent"} · ${STATE.rating_label} ${num(t.rating, 1)}`;
    dl.appendChild(o);
  }
}

function fillMetrics() {
  for (const sel of [$("primary"), $("leaguemetric")]) {
    sel.innerHTML = "";
    for (const m of STATE.metrics) {
      const o = document.createElement("option");
      o.value = m.key; o.textContent = m.label;
      sel.appendChild(o);
    }
    sel.value = "make_playoff";
  }
}

function fillWeeks() {
  const sel = $("week");
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = "all"; all.textContent = "All remaining";
  sel.appendChild(all);
  for (const w of STATE.weeks) {
    const o = document.createElement("option");
    o.value = String(w); o.textContent = "Week " + w;
    sel.appendChild(o);
  }
  sel.value = String(STATE.default_week ?? STATE.current_week);
}

// ------------------------------------------------------------------ running
//
// Only three things change the simulation itself: which team is the focus
// (the kernel records metrics for that team alone), how many seasons to draw,
// and the underlying data. Week, metric, significance filter and sort order
// are all views over one completed run.

function currentInputs() {
  return {
    team: $("team").value.trim().toLowerCase(),
    n_sims: parseInt($("nsims").value, 10),
    data: STATE ? STATE.loaded_at : 0,
  };
}

function needsRerun() {
  if (!RESULT || !RAN) return true;
  const c = currentInputs();
  return c.team !== RAN.team || c.n_sims !== RAN.n_sims || c.data !== RAN.data;
}

function updateStaleness() {
  const el = $("staleness");
  if (!RESULT) { el.hidden = true; return; }
  const c = currentInputs();
  const why = [];
  if (c.team !== RAN.team) why.push("team");
  if (c.n_sims !== RAN.n_sims) why.push("simulation count");
  if (c.data !== RAN.data) why.push("underlying data");
  el.hidden = why.length === 0;
  if (why.length) {
    el.innerHTML = `Showing results for <b>${esc(RESULT.team)}</b> at `
      + `${RESULT.n_sims.toLocaleString()} seasons. The ${why.join(" and ")} `
      + `changed — press <b>Run</b> to re-simulate.`;
  }
}

async function runSim() {
  const name = $("team").value.trim();
  if (!name) { banner("Pick a team first.", false); return; }
  localStorage.setItem("cfbroot.team", name);

  const inputs = currentInputs();
  setBusy(true);
  let job;
  try {
    const resp = await fetch("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team: name, n_sims: inputs.n_sims,
                             primary: $("primary").value, all_weeks: true }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    job = await resp.json();
  } catch (err) {
    banner("Could not start the run: " + err.message, false);
    setBusy(false);
    return;
  }
  poll(job.job_id, inputs);
}

function setBusy(busy) {
  $("run").disabled = busy;
  $("refresh").disabled = busy;
  $("progress").hidden = !busy;
  if (busy) {
    $("progressfill").style.width = "0%";
    $("progresstext").textContent = "compiling and simulating…";
  }
}

function poll(jobId, inputs) {
  clearInterval(POLLING);
  POLLING = setInterval(async () => {
    let j;
    try {
      j = await (await fetch("/api/job/" + jobId)).json();
    } catch { return; }
    if (j.status === "running") {
      const frac = j.total ? j.done / j.total : 0;
      $("progressfill").style.width = (100 * frac).toFixed(1) + "%";
      $("progresstext").textContent =
        `${j.done.toLocaleString()} / ${j.total.toLocaleString()} seasons simulated`
        + ` · ${num(j.elapsed, 1)}s`;
      return;
    }
    clearInterval(POLLING);
    setBusy(false);
    if (j.status === "error") {
      banner("Simulation failed: " + String(j.error).split("\n")[0], false);
      return;
    }
    RESULT = j.result;
    RAN = inputs;
    render();
  }, 250);
}

async function refreshData() {
  setBusy(true);
  $("progresstext").textContent = "re-pulling scores and ratings…";
  try {
    STATE = await (await fetch("/api/refresh", { method: "POST" })).json();
    renderSeasonLine(); fillTeams(); fillWeeks(); showNotes();
  } catch (err) {
    banner("Refresh failed: " + err, false);
    setBusy(false);
    return;
  }
  setBusy(false);
  if ($("team").value.trim()) runSim();
}

// ---------------------------------------------------------------- selection

function metricLabel(key) {
  const m = STATE.metrics.find(x => x.key === key);
  return m ? m.label : key;
}

function selectedWeek() {
  const v = $("week").value;
  return v === "all" ? null : parseInt(v, 10);
}

/** Which false-discovery-rate scope applies to the current slate. */
function scope() { return selectedWeek() === null ? "all" : "week"; }

function sigOf(s) { return scope() === "all" ? s.sig_all : s.sig_week; }
function qOf(s) { return scope() === "all" ? s.q_all : s.q_week; }

function confidenceOf(s) {
  if (!s.reliable) return "thin";
  return sigOf(s) ? "clear" : "leaning";
}

/** CI bound nearest zero — the swing that can actually be defended. */
function conservative(s) {
  if (!isFinite(s.lo) || !isFinite(s.hi)) return 0;
  if (s.lo <= 0 && 0 <= s.hi) return 0;
  return Math.min(Math.abs(s.lo), Math.abs(s.hi));
}

function visibleGames() {
  const key = $("primary").value;
  const wk = selectedWeek();
  let games = RESULT.games.filter(g => wk === null || g.week === wk);
  if ($("sigfilter").value === "sig") {
    games = games.filter(g => sigOf(g.swings[key]));
  }
  return games.sort((a, b) => {
    const sa = a.swings[key], sb = b.swings[key];
    return (sigOf(sb) - sigOf(sa))
      || (sb.reliable - sa.reliable)
      || (conservative(sb) - conservative(sa))
      || (Math.abs(sb.delta) - Math.abs(sa.delta));
  });
}

function ownGames() {
  const wk = selectedWeek();
  return RESULT.own_games.filter(g => wk === null || g.week === wk);
}

// ---------------------------------------------------------------- rendering

function render() {
  if (!RESULT) return;
  $("empty").hidden = true;
  $("results").hidden = false;
  renderHeadline();
  renderDist("winsdist", RESULT.wins_distribution, (i) => i);
  renderDist("seeddist", RESULT.seed_distribution, (i) => (i === 0 ? "out" : String(i)));
  $("winsnote").textContent = `— ${num(RESULT.expected_wins)} expected`;
  renderOwnGames();
  renderRootList();
  renderLeague();
  renderMethod();
  $("weeklabel").textContent = selectedWeek() === null
    ? "— all remaining games" : "— week " + selectedWeek();
  $("footmeta").textContent =
    `${RESULT.n_sims.toLocaleString()} seasons · ${num(RESULT.elapsed)}s · `
    + `${STATE.rating_label} ${ago(STATE.ratings_updated)}`;
  updateStaleness();
}

function renderHeadline() {
  const key = $("primary").value;
  const wrap = $("headline");
  wrap.innerHTML = "";
  const keys = [key, "win_conference", "top4_seed", "win_national_title"]
    .filter((v, i, a) => a.indexOf(v) === i);
  for (const k of keys) {
    const h = RESULT.headline[k];
    if (!h) continue;
    const div = document.createElement("div");
    div.className = "card" + (k === key ? " is-primary" : "");
    div.innerHTML = `<div class="label">${esc(h.label)}</div>
      <div class="value">${pct(h.p)}</div>
      <div class="ci">95% CI ${pct(h.lo)} – ${pct(h.hi)}</div>`;
    wrap.appendChild(div);
  }
  const div = document.createElement("div");
  div.className = "card";
  div.innerHTML = `<div class="label">Expected wins</div>
    <div class="value">${num(RESULT.expected_wins)}</div>
    <div class="ci">regular season</div>`;
  wrap.appendChild(div);
}

function renderDist(elId, dist, labelFn) {
  const el = $(elId);
  el.innerHTML = "";
  let lo = 0, hi = dist.length - 1;
  while (lo < hi && dist[lo] < 0.002) lo++;
  while (hi > lo && dist[hi] < 0.002) hi--;
  const max = Math.max(...dist.slice(lo, hi + 1), 1e-9);
  for (let i = lo; i <= hi; i++) {
    const b = document.createElement("div");
    b.className = "b" + (dist[i] === max ? " hi" : "");
    b.title = `${labelFn(i)}: ${pct(dist[i])}`;
    b.innerHTML = `<i style="height:${(100 * dist[i] / max).toFixed(1)}%"></i>`
      + `<span>${labelFn(i)}</span>`;
    el.appendChild(b);
  }
}

function logo(idx, size) {
  const t = team(idx);
  if (!t.logo) return `<span class="logo ph" style="width:${size}px;height:${size}px"></span>`;
  return `<img class="logo" src="${esc(t.logo)}" alt="" loading="lazy"
            width="${size}" height="${size}">`;
}

/** One row per game: the side to root for, and what each result does. */
function gameRowsHTML(games) {
  const key = $("primary").value;
  const base = RESULT.headline[key].p;
  const label = metricLabel(key).toLowerCase();

  const maxAbs = Math.max(1e-6, ...games.map(g => {
    const s = g.swings[key];
    return Math.max(Math.abs(s.lo || 0), Math.abs(s.hi || 0), Math.abs(s.delta || 0));
  }));

  let html = `<div class="tablewrap"><table class="games"><thead><tr>
      <th>Matchup <span class="hint">away @ home · bold = root for</span></th>
      <th class="num">If away wins</th>
      <th class="num">If home wins</th>
      <th class="swingcell">Swing in ${esc(label)}</th>
      <th class="num">Confidence</th>
    </tr></thead><tbody>`;

  for (const g of games) {
    const s = g.swings[key];
    const conf = confidenceOf(s);
    const rootHome = s.home;
    const scale = 50 / maxAbs;
    const w = Math.min(50, Math.abs(s.delta) * scale);
    const left = rootHome ? 50 : 50 - w;
    const ciLo = 50 + Math.max(-50, Math.min(50, (s.lo || 0) * scale));
    const ciHi = 50 + Math.max(-50, Math.min(50, (s.hi || 0) * scale));
    const dim = sigOf(s) ? "" : " dim";
    const pHome = g.p_home_win;

    // The team to root for is the one in bold — never simply the home team.
    const awayCls = "side" + (rootHome ? "" : " root") + (conf === "clear" ? " strong" : "");
    const homeCls = "side" + (rootHome ? " root" : "") + (conf === "clear" ? " strong" : "");

    html += `<tr>
      <td class="matchup">
        <span class="${awayCls}">${logo(g.away_idx, 18)}${esc(g.away)}</span>
        <span class="at">${g.neutral ? "vs" : "@"}</span>
        <span class="${homeCls}">${logo(g.home_idx, 18)}${esc(g.home)}</span>
        ${g.neutral ? '<span class="tag">neutral</span>' : ""}
      </td>
      ${outcomeCell(1 - pHome, s.p_if_away, base, !rootHome)}
      ${outcomeCell(pHome, s.p_if_home, base, rootHome)}
      <td class="swingcell"><div class="swing">
          <div class="axis"></div>
          <div class="fill ${rootHome ? "pos" : "neg"}${dim}"
               style="left:${left}%;width:${w}%"></div>
          <div class="ci" style="left:${Math.min(ciLo, ciHi)}%;width:${Math.abs(ciHi - ciLo)}%"></div>
        </div>
        <div class="swingnum">${signed(s.delta)}pp
          <span class="muted">[${signed(s.lo)}, ${signed(s.hi)}]</span></div>
      </td>
      <td class="num"><span class="sig ${conf}" title="${esc(CONF_TITLE[conf])}"
        >${conf}</span><div class="qv">q ${qOf(s) < 0.001 ? "&lt;.001" : num(qOf(s), 3)}</div></td>
    </tr>`;
  }
  return html + "</tbody></table></div>";
}

function outcomeCell(likelihood, p, base, isGood) {
  const d = p - base;
  const dirCls = d > 0 ? "up" : (d < 0 ? "down" : "flat");
  return `<td class="num outcome ${isGood ? "good" : ""}">
      <div class="op">${pct(p)}</div>
      <div class="od ${dirCls}">${signed(d)}pp</div>
      <div class="ol">${pct(likelihood, 0)} likely</div>
    </td>`;
}

function renderOwnGames() {
  const games = ownGames();
  const panel = $("owngames");
  if (!games.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("owntable").innerHTML = gameRowsHTML(games);
}

function renderRootList() {
  const key = $("primary").value;
  const wk = selectedWeek();
  const slate = RESULT.games.filter(g => wk === null || g.week === wk);
  const games = visibleGames();
  const shown = games.slice(0, 60);

  $("rootlist").innerHTML = shown.length
    ? gameRowsHTML(shown)
    : `<p class="foot">No game on this slate clears the significance threshold
       for ${esc(metricLabel(key).toLowerCase())}. Switch <b>Show</b> back to
       <b>All games</b> to see the best guesses, or raise the simulation count.</p>`;

  const sig = slate.filter(g => sigOf(g.swings[key])).length;
  $("rootcount").textContent = `${shown.length} of ${slate.length} shown`;

  const base = RESULT.headline[key].p;
  $("rootfoot").innerHTML =
    `Percentages are ${esc(RESULT.team)}'s chance of <b>${esc(metricLabel(key).toLowerCase())}</b> `
    + `after that result, against a baseline of <b>${pct(base)}</b> today. `
    + `A side is named for every game; the confidence column says how firm the call is. `
    + `<b>${sig}</b> of <b>${slate.length}</b> games on this slate clear `
    + `Benjamini–Hochberg control at q ≤ ${RESULT.fdr_q}.`;
}

function renderLeague() {
  const key = $("leaguemetric").value;
  const rows = RESULT.league.slice()
    .sort((a, b) => b.p[key] - a.p[key]).slice(0, 25);
  const showEspn = key === "make_playoff"
    && rows.some(r => r.espn_playoff_prob !== null && r.espn_playoff_prob !== undefined);
  let html = `<div class="tablewrap"><table><thead><tr>
    <th class="num">#</th><th>Team</th><th>Conference</th>
    <th class="num">${esc(STATE.rating_label)}</th>
    <th class="num">${esc(metricLabel(key))}</th>
    <th class="num">95% CI</th>
    ${showEspn ? '<th class="num">ESPN</th>' : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    html += `<tr><td class="num">${i + 1}</td>
      <td class="teamcell">${logo(r.idx, 18)}${esc(r.team)}</td>
      <td class="muted">${esc(r.conference || "")}</td>
      <td class="num">${num(r.rating, 1)}</td>
      <td class="num"><b>${pct(r.p[key])}</b></td>
      <td class="num muted">${pct(r.lo[key])}–${pct(r.hi[key])}</td>
      ${showEspn ? `<td class="num muted">${r.espn_playoff_prob != null
        ? pct(r.espn_playoff_prob) : "—"}</td>` : ""}</tr>`;
  });
  $("league").innerHTML = html + "</tbody></table></div>";
  $("leaguefoot").innerHTML = showEspn
    ? "The ESPN column is ESPN's own FPI-based playoff probability, shown as an "
    + "outside reference. It is not an input to anything here."
    : "";
}

function renderMethod() {
  const notes = (RESULT.notes || []).map(n => `<div class="note">${esc(n)}</div>`).join("");
  $("method").innerHTML = notes + `
    <p><b>${RESULT.n_sims.toLocaleString()}</b> complete seasons simulated in
       <b>${num(RESULT.elapsed)}s</b>${RESULT.used_numba ? "" : " (without numba)"}.
       Each unplayed game is drawn independently from a normal model on the
       ${esc(STATE.rating_label)} rating gap; then conference standings, title
       games, a committee-ranking proxy, the 12-team playoff field and the full
       bracket are resolved.</p>
    <p><b>One run covers everything on this page.</b> All nine measures of
       success and all ${RESULT.games.length + RESULT.own_games.length} remaining
       games come from the same set of simulated seasons, so changing the
       measure, the slate or the filter is a re-render. Only a different team, a
       different simulation count, or fresh data requires simulating again.</p>
    <p>Rooting interests come from slicing <em>those same</em> simulations by the
       result of each game — the subset where the home team won against the
       subset where it lost. Because both slices share every other game's random
       draws, the difference between them is far more precisely estimated than
       two separate runs would give.</p>
    <p>Each swing is a difference of two binomial proportions, with a Newcombe
       score interval. <code>q</code> is a Benjamini&ndash;Hochberg
       false-discovery-rate adjusted p-value, computed within the selected
       metric over the selected slate. At this sample size, swings below about
       <b>${num(100 * RESULT.resolution)} percentage points</b> cannot be
       separated from Monte Carlo noise.</p>
    <p class="muted">${esc(STATE.calibration)}</p>`;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ------------------------------------------------------------------- events

$("run").addEventListener("click", runSim);
$("refresh").addEventListener("click", refreshData);
for (const id of ["primary", "week", "sigfilter"]) {
  $(id).addEventListener("change", () => { if (RESULT) render(); });
}
$("leaguemetric").addEventListener("change", () => { if (RESULT) renderLeague(); });
for (const id of ["team", "nsims"]) {
  $(id).addEventListener("change", updateStaleness);
  $(id).addEventListener("input", updateStaleness);
}
$("team").addEventListener("keydown", (e) => { if (e.key === "Enter") runSim(); });

boot();
