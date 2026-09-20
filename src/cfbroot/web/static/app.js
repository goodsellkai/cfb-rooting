"use strict";

const $ = (id) => document.getElementById(id);

/* Percentages always show two decimals. */
const pct = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : (100 * x).toFixed(d) + "%";
const signed = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : (x >= 0 ? "+" : "−") + (100 * Math.abs(x)).toFixed(d);
const num = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : x.toFixed(d);

let STATE = null;      // /api/state
let RESULT = null;     // the shown team: every remaining game x every metric
let WANTED = null;     // the team most recently asked for

// The hosted build is plain files written ahead of time; the local app asks
// its server. Both return the same JSON.
const STATIC = !!window.CFBROOT_STATIC;
const URLS = STATIC
  ? { state: "data/state.json", team: (t) => `data/team/${t.idx}.json` }
  : { state: "/api/state", team: (t) => "/api/team/" + encodeURIComponent(t.name) };

const CONF_TITLE = {
  clear: "Statistically significant.",
  leaning: "Direction is a best guess.",
  thin: "Too few simulations on one side.",
};

// Startup

async function boot() {
  try {
    STATE = await (await fetch(URLS.state)).json();
  } catch (err) {
    banner("Could not load season data: " + err, false);
    return;
  }
  renderSeasonLine();
  fillTeams();
  fillMetrics();
  fillWeeks();
  showNotes();
  let saved = null;
  try { saved = localStorage.getItem("cfbroot.team"); } catch { /* private mode */ }
  if (saved && teamNamed(saved)) {
    $("team").value = saved;
    loadTeam();
  }
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
    banner("No CFBD API key found. Showing a fake demo season. "
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
  const pw = STATE.poll_weeks || {};
  const pollChip = pw.cfp ? `<span class="chip">CFP poll <b>week ${pw.cfp}</b></span>`
    : pw.ap ? `<span class="chip">AP poll <b>week ${pw.ap}</b></span>` : "";
  $("topmeta").innerHTML = pollChip
    + `<span class="chip">${esc(STATE.rating_label)} <b>${esc(ago(STATE.ratings_updated) || "-")}</b></span>`
    + (STATE.loaded_at
      ? `<span class="chip">Simulated <b>${esc(ago(new Date(1000 * STATE.loaded_at).toISOString()))}</b></span>`
      : "");
}

function team(idx) {
  return (STATE.team_index || {})[String(idx)] || {};
}

/** A team's place in the published polls: the committee's once it starts, the
 * AP's before that. These are the real polls, not the model's ranking. */
function pollRank(idx) {
  const p = STATE.polls || {};
  const cfp = (p.cfp || {})[String(idx)];
  const ap = (p.ap || {})[String(idx)];
  return { cfp, ap, best: cfp || ap, kind: cfp ? "CFP" : "AP" };
}

function pollTag(idx) {
  const r = pollRank(idx);
  if (!r.best) return "";
  const wk = (STATE.poll_weeks || {})[r.cfp ? "cfp" : "ap"];
  return `<span class="rk" title="${r.kind} #${r.best}${wk ? `, week ${wk}` : ""}"
      >${r.best}</span>`;
}

// Team picker
//
// A list of our own rather than a <datalist>, which browsers draw
// inconsistently and which only offers the current team once one is picked.

let MENU = [];         // teams in the open menu
let ACTIVE = -1;       // highlighted row

function matchingTeams(query) {
  const q = query.trim().toLowerCase();
  const picked = WANTED && q === WANTED.toLowerCase();
  if (!q || picked) return STATE.teams;
  const hits = STATE.teams.filter(t => t.name.toLowerCase().includes(q)
    || (t.conference || "").toLowerCase().includes(q));
  const starts = (t) => t.name.toLowerCase().startsWith(q) ? 0 : 1;
  return hits.sort((a, b) => starts(a) - starts(b) || a.name.localeCompare(b.name));
}

function openMenu() {
  MENU = matchingTeams($("team").value);
  const cur = MENU.findIndex(t => t.name === WANTED);
  ACTIVE = cur >= 0 ? cur : (MENU.length ? 0 : -1);
  const ul = $("teammenu");
  ul.innerHTML = MENU.length
    ? MENU.map((t, i) => `<li role="option" data-i="${i}" id="teamopt-${i}"
        aria-selected="${t.name === WANTED}">
        ${logo(t.idx, 20)}<span class="name">${esc(t.name)}</span>
        <span class="conf">${esc(t.conference || "Independent")}</span></li>`).join("")
    : `<li class="none">No team matches</li>`;
  ul.hidden = false;
  $("team").setAttribute("aria-expanded", "true");
  showActive();
}

function closeMenu() {
  $("teammenu").hidden = true;
  $("team").setAttribute("aria-expanded", "false");
  $("team").removeAttribute("aria-activedescendant");
  ACTIVE = -1;
}

function showActive() {
  const ul = $("teammenu");
  ul.querySelectorAll("li.active").forEach(li => li.classList.remove("active"));
  const li = ul.querySelector(`li[data-i="${ACTIVE}"]`);
  if (!li) { $("team").removeAttribute("aria-activedescendant"); return; }
  li.classList.add("active");
  li.scrollIntoView({ block: "nearest" });
  $("team").setAttribute("aria-activedescendant", li.id);
}

function chooseTeam(t) {
  $("team").value = t.name;
  closeMenu();
  $("team").blur();
  loadTeam();
}

function fillTeams() {
  const input = $("team");
  const ul = $("teammenu");
  input.addEventListener("focus", () => { input.select(); openMenu(); });
  input.addEventListener("click", () => { if (ul.hidden) openMenu(); });
  input.addEventListener("input", openMenu);
  input.addEventListener("blur", () => {
    closeMenu();
    // Leaving half-typed text behind would look like a different team.
    if (!teamNamed(input.value) && WANTED) input.value = WANTED;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (ul.hidden) { openMenu(); return; }
      if (!MENU.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      ACTIVE = (ACTIVE + step + MENU.length) % MENU.length;
      showActive();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const t = MENU[ACTIVE] || teamNamed(input.value);
      if (t) chooseTeam(t);
    } else if (e.key === "Escape") {
      input.blur();
    }
  });
  // mousedown, so the pick lands before the input loses focus and closes it.
  ul.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const li = e.target.closest("li[data-i]");
    if (li) chooseTeam(MENU[Number(li.dataset.i)]);
  });
  ul.addEventListener("mousemove", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li && Number(li.dataset.i) !== ACTIVE) {
      ACTIVE = Number(li.dataset.i);
      showActive();
    }
  });
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

// Loading
//
// The server simulates every team once at start-up, so picking a team is a
// lookup. Until that run finishes the server answers 202 with its progress.
// The hosted build never does: its files were written after the run.

function teamNamed(name) {
  const n = name.trim().toLowerCase();
  return STATE.teams.find(t => t.name.toLowerCase() === n) || null;
}

async function loadTeam() {
  const t = teamNamed($("team").value);
  if (!t || (RESULT && RESULT.team === t.name && WANTED === t.name)) return;
  WANTED = t.name;
  try { localStorage.setItem("cfbroot.team", t.name); } catch { /* private mode */ }
  if (typeof onTeamChanged === "function") onTeamChanged();   // season.js
  for (;;) {
    let resp, body;
    try {
      resp = await fetch(URLS.team(t));
      body = await resp.json();
    } catch (err) {
      showProgress(null);
      banner("Could not load " + t.name + ": " + err, false);
      return;
    }
    if (WANTED !== t.name) return;             // a different team was picked
    if (resp.status === 202) {
      showProgress(body);
      await new Promise(r => setTimeout(r, 400));
      continue;
    }
    showProgress(null);
    if (!resp.ok) {
      banner("Could not load " + t.name + ": " + (body.detail || resp.statusText), false);
      return;
    }
    RESULT = body;
    render();
    return;
  }
}

function showProgress(job) {
  $("progress").hidden = !job;
  if (!job) return;
  $("empty").hidden = true;
  const frac = job.total ? job.done / job.total : 0;
  $("progressfill").style.width = (100 * frac).toFixed(1) + "%";
  $("progresstext").textContent =
    `Simulating the season: ${job.done.toLocaleString()} / `
    + `${job.total.toLocaleString()} · ${num(job.elapsed, 0)}s`;
}

// Selection

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

/** Confidence bound closest to zero. */
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

// Rendering

function render() {
  if (!RESULT) return;
  $("empty").hidden = true;
  $("results").hidden = false;
  renderHeadline();
  renderDist("winsdist", RESULT.wins_distribution, (i) => i);
  renderDist("seeddist", RESULT.seed_distribution, (i) => (i === 0 ? "out" : String(i)));
  renderOwnGames();
  renderRootList();
  renderLeague();
  $("weeklabel").textContent = selectedWeek() === null
    ? "(all remaining games)" : "(week " + selectedWeek() + ")";
  $("footmeta").textContent =
    `${RESULT.n_sims.toLocaleString()} seasons · `
    + `${STATE.rating_label} ${ago(STATE.ratings_updated)}`;
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
      <div class="value">${pct(h.p)}</div>`;
    wrap.appendChild(div);
  }
  const div = document.createElement("div");
  div.className = "card";
  div.innerHTML = `<div class="label">Expected wins</div>
    <div class="value">${num(RESULT.expected_wins)}</div>`;
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
      <th>Matchup</th>
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

    // Bold the team to root for.
    const awayCls = "side" + (rootHome ? "" : " root") + (conf === "clear" ? " strong" : "");
    const homeCls = "side" + (rootHome ? " root" : "") + (conf === "clear" ? " strong" : "");

    html += `<tr>
      <td class="matchup">
        <span class="${awayCls}" data-team="${g.away_idx}">${logo(g.away_idx, 18)}${pollTag(g.away_idx)}${esc(g.away)}</span>
        <span class="at">${g.neutral ? "vs" : "@"}</span>
        <span class="${homeCls}" data-team="${g.home_idx}">${logo(g.home_idx, 18)}${pollTag(g.home_idx)}${esc(g.home)}</span>
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
        <div class="swingnum">${signed(s.delta)}pp</div>
      </td>
      <td class="num"><span class="sig ${conf}" title="${esc(CONF_TITLE[conf])}">${conf}</span></td>
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
    : `<p class="foot">Nothing significant on this slate.</p>`;

  $("rootcount").textContent = `${shown.length} of ${slate.length}`;
}

function renderLeague() {
  const key = $("leaguemetric").value;
  const rows = RESULT.league.slice().sort((a, b) => b.p[key] - a.p[key]);
  const showEspn = key === "make_playoff"
    && rows.some(r => r.espn_playoff_prob !== null && r.espn_playoff_prob !== undefined);
  const polls = STATE.polls || {};
  const weeks = STATE.poll_weeks || {};
  const showAp = !!polls.ap, showCfp = !!polls.cfp;
  const head = (k, label) => `<th class="num" title="${label} poll, week ${weeks[k]}">${label}</th>`;
  let html = `<div class="tablewrap tall"><table><thead><tr>
    <th class="num">#</th><th>Team</th><th>Conference</th>
    <th class="num">${esc(STATE.rating_label)}</th>
    ${showCfp ? head("cfp", "CFP") : ""}${showAp ? head("ap", "AP") : ""}
    <th class="num">${esc(metricLabel(key))}</th>
    ${showEspn ? '<th class="num">ESPN</th>' : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const pr = pollRank(r.idx);
    html += `<tr><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${r.idx}">${logo(r.idx, 18)}${esc(r.team)}</td>
      <td class="muted">${esc(r.conference || "")}</td>
      <td class="num">${num(r.rating, 1)}</td>
      ${showCfp ? `<td class="num muted">${pr.cfp || ""}</td>` : ""}
      ${showAp ? `<td class="num muted">${pr.ap || ""}</td>` : ""}
      <td class="num"><b>${pct(r.p[key])}</b></td>
      ${showEspn ? `<td class="num muted">${r.espn_playoff_prob != null
        ? pct(r.espn_playoff_prob) : "-"}</td>` : ""}</tr>`;
  });
  $("league").innerHTML = html + "</tbody></table></div>";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Any team name on the page: hover for its games so far, click to pick it.
//
// Elements carry data-team="<team index>". On the Sample season tab the
// games are that simulated season's, through the stage being shown; anywhere
// else they are the real results.

let TIP_TEAM = null;

function teamResults(idx, inSeason) {
  if (inSeason && typeof seasonGamesFor === "function") {
    const g = seasonGamesFor(idx);
    if (g) return g;
  }
  return (STATE.played || [])
    .filter(g => g.home === idx || g.away === idx)
    .map(g => ({ ...g, label: g.title_game ? "Title" : `Wk ${g.week}` }));
}

function tipHTML(idx, games) {
  const t = team(idx);
  let w = 0, l = 0;
  const rows = games.map(g => {
    const home = g.home === idx;
    const us = home ? g.home_points : g.away_points;
    const them = home ? g.away_points : g.home_points;
    const won = us > them;
    won ? w++ : l++;
    const opp = home ? g.away : g.home;
    const where = g.neutral ? "vs" : (home ? "vs" : "@");
    return `<tr><td class="muted">${esc(g.label)}</td>
      <td class="${won ? "wl w" : "wl l"}">${won ? "W" : "L"}</td>
      <td class="num">${us}-${them}</td>
      <td class="muted">${where}</td>
      <td class="teamcell">${logo(opp, 14)}${esc(team(opp).name)}</td></tr>`;
  }).join("");
  const rating = t.rating != null ? ` · ${esc(STATE.rating_label)} ${num(t.rating, 1)}` : "";
  const pr = pollRank(idx);
  const ranked = [pr.cfp ? `CFP #${pr.cfp}` : "", pr.ap ? `AP #${pr.ap}` : ""]
    .filter(Boolean).join(" · ");
  const pick = STATE.teams.some(x => x.idx === idx) ? "Click to make this your team" : "";
  return `<div class="tiphead">${logo(idx, 24)}<div>
      <div class="tipname">${esc(t.name)} <span class="muted">${w}-${l}</span>
        ${ranked ? `<span class="tiprank">${ranked}</span>` : ""}</div>
      <div class="tipsub">${esc(t.conference || "Independent")}${rating}</div></div></div>
    ${rows ? `<table class="tiptable"><tbody>${rows}</tbody></table>`
           : '<p class="tipnone">No games played yet.</p>'}
    ${pick ? `<p class="tipfoot">${pick}</p>` : ""}`;
}

function placeTip(e) {
  const tip = $("teamtip");
  const pad = 14;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > innerWidth - 8) x = Math.max(8, e.clientX - r.width - pad);
  if (y + r.height > innerHeight - 8) y = Math.max(8, innerHeight - r.height - 8);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function hideTip() {
  $("teamtip").hidden = true;
  TIP_TEAM = null;
}

function pickTeam(idx) {
  const t = STATE.teams.find(x => x.idx === idx);
  if (!t) return;                      // FCS opponents cannot be picked
  $("team").value = t.name;
  loadTeam();
}

document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-team]");
  if (!el || !STATE) return;
  const idx = Number(el.dataset.team);
  if (idx === TIP_TEAM && !$("teamtip").hidden) return;
  TIP_TEAM = idx;
  const tip = $("teamtip");
  tip.innerHTML = tipHTML(idx, teamResults(idx, !!el.closest("#seasonview")));
  tip.hidden = false;
  placeTip(e);
});
document.addEventListener("mousemove", (e) => {
  if (!$("teamtip").hidden && e.target.closest("[data-team]")) placeTip(e);
});
document.addEventListener("mouseout", (e) => {
  const el = e.target.closest("[data-team]");
  if (el && !(e.relatedTarget && el.contains(e.relatedTarget))) hideTip();
});
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-team]");
  if (!el || !STATE) return;
  hideTip();
  pickTeam(Number(el.dataset.team));
});

// Events

for (const id of ["primary", "week", "sigfilter"]) {
  $(id).addEventListener("change", () => { if (RESULT) render(); });
}
$("leaguemetric").addEventListener("change", () => { if (RESULT) renderLeague(); });

boot();
