"use strict";

/* Sample season: one simulated season, played back stage by stage.
 *
 * Uses app.js's globals: $, esc, num, team, logo, STATE, STATIC, WANTED.
 * Every season is simulated fresh by the local server when asked for, so the
 * tab only exists in the local app; the hosted site has nothing to run it.
 */

let SEASON = null;      // the sample: games, title games, ranking, field, bracket
let POLL = null;        // the committee ranking at the stage being shown
let POLL_RANK = new Map();
let STAGES = [];        // what the player steps through
let STAGE = 0;
let PLAYER = null;      // playback timer

const PLAY_MS = 1300;
const UPSET = 0.35;     // a win the model gave less than this is an upset

// Tabs

function showTab(name) {
  const season = name === "season";
  $("guideview").hidden = season;
  $("seasonview").hidden = !season;
  document.body.classList.toggle("season", season);
  for (const [id, on] of [["tab-guide", !season], ["tab-season", season]]) {
    $(id).classList.toggle("active", on);
    $(id).setAttribute("aria-selected", String(on));
  }
  try { localStorage.setItem("cfbroot.tab", name); } catch { /* private mode */ }
  if (season && !SEASON) newSeason();
  if (!season) stopPlaying();
}

// Loading

async function newSeason() {
  stopPlaying();
  $("s-loading").hidden = false;
  $("s-new").disabled = true;
  while (!STATE) await new Promise(r => setTimeout(r, 100));   // app.js still booting
  try {
    const fresh = $("s-fresh").checked;
    const resp = await fetch("/api/sample" + (fresh ? "?from_start=true" : ""),
                             { cache: "no-store" });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    SEASON = await resp.json();
  } catch (err) {
    banner("Could not simulate a season: " + err.message, false);
    return;
  } finally {
    $("s-loading").hidden = true;
    $("s-new").disabled = false;
  }
  buildStages();
  fillConferences();
  $("s-body").hidden = false;
  setStage(STAGES.findIndex(s => s.kind === "week" && s.games.some(g => !g.real)));
}

function buildStages() {
  const S = SEASON;
  const cut = S.selection_week ?? Infinity;
  const weeks = [...new Set(S.games.map(g => g.week))].sort((a, b) => a - b);
  const week = (w) => ({ kind: "week", week: w, label: `Week ${w}`,
                         games: S.games.filter(g => g.week === w) });
  STAGES = weeks.filter(w => w <= cut).map(week);
  if (S.title_games.length) {
    STAGES.push({ kind: "ccg", label: "Championship week", games: S.title_games });
  }
  STAGES.push({ kind: "selection", label: "Selection Sunday", games: [] });
  STAGES.push(...weeks.filter(w => w > cut).map(week));
  S.rounds.forEach((r, i) => STAGES.push({ kind: "round", round: i, label: r.name,
                                           games: r.games }));
  $("s-stage").max = String(STAGES.length - 1);
  $("s-meta").textContent = S.from_start
    ? `${S.year} · season #${S.seed} · replayed from week 1, all `
      + `${S.games.length} games simulated on current ${STATE.rating_label}`
    : `${S.year} · season #${S.seed} · `
      + `${S.games.filter(g => !g.real).length} games simulated, `
      + `${S.games.filter(g => g.real).length} already played`;
}

// Playback

function setStage(i) {
  STAGE = Math.max(0, Math.min(STAGES.length - 1, i < 0 ? 0 : i));
  $("s-stage").value = String(STAGE);
  renderSeason();
}

function stopPlaying() {
  clearInterval(PLAYER);
  PLAYER = null;
  $("s-play").textContent = "Play";
}

function togglePlay() {
  if (PLAYER) { stopPlaying(); return; }
  if (STAGE >= STAGES.length - 1) setStage(0);
  $("s-play").textContent = "Pause";
  PLAYER = setInterval(() => {
    if (STAGE >= STAGES.length - 1) { stopPlaying(); return; }
    setStage(STAGE + 1);
  }, PLAY_MS);
}

// What has happened by the current stage

function stageIndex(kind) { return STAGES.findIndex(s => s.kind === kind); }

/** Every result through the current stage: regular season and title games. */
function resultsSoFar() {
  const out = [];
  for (let i = 0; i <= STAGE; i++) {
    const s = STAGES[i];
    if (s.kind === "week" || s.kind === "ccg") out.push(...s.games);
  }
  return out;
}

function playoffSoFar() {
  const out = [];
  for (let i = 0; i <= STAGE; i++) {
    if (STAGES[i].kind === "round") out.push(...STAGES[i].games);
  }
  return out;
}

function records(games) {
  const r = {};
  const row = (t) => (r[t] ??= { w: 0, l: 0, cw: 0, cl: 0, pf: 0, pa: 0 });
  for (const g of games) {
    const hw = g.home_points > g.away_points;
    const h = row(g.home), a = row(g.away);
    h.pf += g.home_points; h.pa += g.away_points;
    a.pf += g.away_points; a.pa += g.home_points;
    h[hw ? "w" : "l"]++; a[hw ? "l" : "w"]++;
    if (g.conference === true) { h[hw ? "cw" : "cl"]++; a[hw ? "cl" : "cw"]++; }
  }
  return r;
}

const rec = (r) => r ? `${r.w}-${r.l}` : "0-0";
const winner = (g) => (g.home_points > g.away_points ? g.home : g.away);
const loser = (g) => (g.home_points > g.away_points ? g.away : g.home);
const winP = (g) => (g.p_home == null ? null
  : (g.home_points > g.away_points ? g.p_home : 1 - g.p_home));

function myIdx() {
  const t = WANTED && STATE.teams.find(x => x.name === WANTED);
  return t ? t.idx : null;
}

function ratingOf(idx) {
  const r = team(idx).rating;
  return r == null ? -40 : r;
}

/** The team's FPI, shown next to its name. */
function fpi(idx) {
  const r = team(idx).rating;
  return r == null ? "" : `<span class="fpi" title="${esc(STATE.rating_label)}">${num(r, 1)}</span>`;
}

const ROUND_SHORT = ["CFP 1st rd", "CFP QF", "CFP SF", "CFP final"];

/** A team's games in this season through the stage shown, for the hover card. */
function seasonGamesFor(idx) {
  if (!SEASON) return null;
  const out = [];
  for (let i = 0; i <= STAGE; i++) {
    const s = STAGES[i];
    const label = (g) => s.kind === "week" ? `Wk ${g.week}`
      : s.kind === "ccg" ? "Title" : ROUND_SHORT[s.round] || s.label;
    for (const g of s.games) {
      if (g.home === idx || g.away === idx) out.push({ ...g, label: label(g) });
    }
  }
  return out;
}

/** The committee ranking as it stood at the stage being shown: the latest
 * weekly poll during the season, the final one from Selection Sunday on. */
function currentPoll() {
  const stage = STAGES[STAGE];
  const polls = SEASON.polls || {};
  const weeks = Object.keys(polls).map(Number).sort((a, b) => a - b);
  if (stage.kind === "week" && STAGE < stageIndex("selection")) {
    const w = weeks.filter(x => x <= stage.week).pop();
    return w ? { label: `week ${w}`, teams: polls[w] } : null;
  }
  if (stage.kind === "ccg" && weeks.length) {
    const w = weeks[weeks.length - 1];
    return { label: `week ${w}`, teams: polls[w] };
  }
  return { label: "final", teams: SEASON.ranking.slice(0, 25).map(r => r.team) };
}

function rankTag(idx) {
  const r = POLL_RANK.get(idx);
  return r ? `<span class="rk">${r}</span>` : "";
}

// Rendering

function renderSeason() {
  if (!SEASON) return;
  const stage = STAGES[STAGE];
  $("s-label").textContent = `${stage.label}  (${STAGE + 1}/${STAGES.length})`;
  $("s-prev").disabled = STAGE === 0;
  $("s-next").disabled = STAGE === STAGES.length - 1;
  const games = resultsSoFar();
  const recs = records(games);
  POLL = currentPoll();
  POLL_RANK = new Map((POLL ? POLL.teams : []).map((t, i) => [t, i + 1]));
  renderStats(games, recs);
  renderMine(games, recs);
  renderBoard(stage, recs);
  renderRight(recs);
  $("footmeta").textContent = `sample season #${SEASON.seed}`;
}

function statCard(label, value, detail) {
  return `<div class="card"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    <div class="detail">${detail || "&nbsp;"}</div></div>`;
}

function matchText(g) {
  const w = winner(g), l = loser(g);
  const hi = Math.max(g.home_points, g.away_points);
  const lo = Math.min(g.home_points, g.away_points);
  return `${esc(team(w).name)} ${hi}, ${esc(team(l).name)} ${lo}`;
}

function renderStats(games, recs) {
  const fbsGame = (g) => team(g.home).fbs && team(g.away).fbs;
  const scored = games.filter(fbsGame);
  const upsets = scored.filter(g => winP(g) != null && winP(g) < UPSET);
  const biggest = upsets.reduce((a, g) => (!a || winP(g) < winP(a) ? g : a), null);
  const margin = (g) => Math.abs(g.home_points - g.away_points);
  const total = (g) => g.home_points + g.away_points;
  const blow = scored.reduce((a, g) => (!a || margin(g) > margin(a) ? g : a), null);
  const high = scored.reduce((a, g) => (!a || total(g) > total(a) ? g : a), null);
  const unbeaten = STATE.teams.filter(t => recs[t.idx] && recs[t.idx].l === 0
                                           && recs[t.idx].w > 0);

  let html = "";
  const final = STAGES[STAGE].kind === "round" && STAGE === STAGES.length - 1;
  if (final && SEASON.champion != null) {
    html += statCard("National champion", `${logo(SEASON.champion, 26)} ${esc(team(SEASON.champion).name)}`,
                     rec(recs[SEASON.champion]) + " before the playoff");
  }
  html += statCard("Upsets", String(upsets.length),
    biggest ? `Biggest: ${esc(team(winner(biggest)).name)}, given `
              + `${Math.round(100 * winP(biggest))}% to win` : "");
  html += statCard("Biggest blowout", blow ? `${margin(blow)}` : "-", blow ? matchText(blow) : "");
  html += statCard("Highest scoring", high ? `${total(high)}` : "-", high ? matchText(high) : "");
  html += statCard("Unbeaten", String(unbeaten.length),
    unbeaten.slice(0, 4).map(t => esc(t.name)).join(", ")
    + (unbeaten.length > 4 ? ", …" : ""));
  $("s-stats").innerHTML = html;
}

function renderMine(games, recs) {
  const me = myIdx();
  const el = $("s-mine");
  if (me == null) { el.hidden = true; return; }
  el.hidden = false;
  const r = recs[me];
  const mine = games.filter(g => g.home === me || g.away === me);
  const chips = mine.map(g => {
    const won = winner(g) === me;
    const opp = g.home === me ? g.away : g.home;
    const where = g.neutral ? "vs" : (g.home === me ? "vs" : "@");
    const mp = g.home === me ? g.home_points : g.away_points;
    const op = g.home === me ? g.away_points : g.home_points;
    return `<span class="res ${won ? "w" : "l"}${g.real ? "" : " sim"}" data-team="${opp}">${won ? "W" : "L"} ${mp}-${op}
      ${where} ${esc(team(opp).abbr || team(opp).name)}</span>`;
  }).join("");

  let status = "";
  if (STAGE >= stageIndex("selection")) {
    const rank = SEASON.ranking.findIndex(x => x.team === me) + 1;
    const f = SEASON.field.find(x => x.team === me);
    status = f ? `Ranked ${rank} · seed ${f.seed}${f.bye ? " with a bye" : ""}`
               : `Ranked ${rank} · missed the playoff`;
    const po = playoffSoFar().filter(g => g.home === me || g.away === me);
    const out = po.find(g => winner(g) !== me);
    if (out) status += ` · out in the ${roundName(out).toLowerCase()}`;
    else if (SEASON.champion === me && STAGE === STAGES.length - 1) status += " · national champion";
  }
  el.innerHTML = `<div class="minehead"><span class="teamcell" data-team="${me}">${logo(me, 22)}
      ${rankTag(me)}<b>${esc(team(me).name)}</b>${fpi(me)}</span> <span class="muted">${rec(r)}
      (${r ? r.cw : 0}-${r ? r.cl : 0} ${esc(team(me).conference || "")})</span>
      <span class="minestatus">${status}</span></div>
    <div class="reslist">${chips || '<span class="muted">No games yet.</span>'}</div>`;
}

function roundName(g) {
  const r = SEASON.rounds.find(r => r.games.includes(g));
  return r ? r.name : "playoff";
}

function renderBoard(stage, recs) {
  $("s-boardnote").textContent = "";
  if (stage.kind === "selection") return renderSelection(recs);
  if (stage.kind === "round") return renderBracket();
  $("s-boardtitle").textContent = stage.kind === "ccg" ? "Conference championships" : stage.label;
  const me = myIdx();
  const games = stage.games.slice().sort((a, b) =>
    ((b.home === me || b.away === me) - (a.home === me || a.away === me))
    || (ratingOf(b.home) + ratingOf(b.away) - ratingOf(a.home) - ratingOf(a.away)));
  const sims = games.filter(g => !g.real).length;
  $("s-boardnote").textContent = sims === games.length ? "all simulated"
    : sims === 0 ? "all already played" : `${sims} of ${games.length} simulated`;
  $("s-board").innerHTML = `<div class="gamegrid">${games.map(g => tile(g, recs, stage.kind === "ccg")).join("")}</div>`;
}

function tile(g, recs, isTitle) {
  const me = myIdx();
  const w = winner(g);
  const p = winP(g);
  const upset = p != null && p < UPSET && team(g.home).fbs && team(g.away).fbs;
  const row = (t, pts, home) => `<div class="gt-row${t === w ? " win" : ""}" data-team="${t}">
      ${logo(t, 18)}<span class="gt-name">${home && !g.neutral ? '<span class="at">@</span>' : ""}${rankTag(t)}${esc(team(t).name)}</span>
      ${fpi(t)}<span class="gt-rec">${rec(recs[t])}</span><span class="gt-pts">${pts}</span></div>`;
  const foot = [
    isTitle ? `<span>${esc(g.conference)}</span>` : "",
    g.real ? '<span class="chipmini real">Final</span>' : '<span class="chipmini">Simulated</span>',
    g.neutral && !isTitle ? "<span>neutral</span>" : "",
    upset ? `<span class="chipmini upset">Upset · ${Math.round(100 * p)}%</span>` : "",
  ].join("");
  return `<div class="gt${g.home === me || g.away === me ? " mine" : ""}">
    ${row(g.away, g.away_points, false)}${row(g.home, g.home_points, true)}
    <div class="gt-foot">${foot}</div></div>`;
}

function renderSelection(recs) {
  $("s-boardtitle").textContent = "Selection Sunday";
  $("s-boardnote").textContent = "the model's committee ranking, with its noise";
  const me = myIdx();
  const seedOf = Object.fromEntries(SEASON.field.map(f => [f.team, f]));
  const top = SEASON.ranking.slice(0, 25).map((r, i) => {
    const f = seedOf[r.team];
    return `<tr class="${r.team === me ? "mine" : ""}"><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${r.team}">${logo(r.team, 18)}${esc(team(r.team).name)}${fpi(r.team)}</td>
      <td class="num">${rec(recs[r.team])}</td>
      <td class="num">${num(r.rating, 3)}</td>
      <td>${f ? `<span class="seedchip${f.bye ? " bye" : ""}">${f.seed}</span>` : ""}</td></tr>`;
  }).join("");
  const field = SEASON.field.map(f => `<tr class="${f.team === me ? "mine" : ""}">
      <td class="num">${f.seed}</td>
      <td class="teamcell" data-team="${f.team}">${logo(f.team, 18)}${esc(team(f.team).name)}${fpi(f.team)}</td>
      <td class="muted">${esc(f.how)}${f.bye ? " · bye" : ""}</td></tr>`).join("");
  $("s-board").innerHTML = `<div class="twocol">
    <div><h3 class="subhead">Top 25</h3><div class="tablewrap"><table>
      <thead><tr><th class="num">#</th><th>Team</th><th class="num">Record</th>
      <th class="num">Rating</th><th>Seed</th></tr></thead><tbody>${top}</tbody></table></div></div>
    <div><h3 class="subhead">The field</h3><div class="tablewrap"><table>
      <thead><tr><th class="num">Seed</th><th>Team</th><th>How</th></tr></thead>
      <tbody>${field}</tbody></table></div></div></div>`;
}

function renderBracket() {
  const me = myIdx();
  const shown = STAGES[STAGE].round;
  $("s-boardtitle").textContent = STAGES[STAGE].label;
  const seedOf = Object.fromEntries(SEASON.field.map(f => [f.team, f.seed]));
  const line = (t, pts, g, done) => `<div class="bteam${done && winner(g) === t ? " win" : ""}${t === me ? " mine" : ""}" data-team="${t}">
      <span class="bseed">${seedOf[t] ?? ""}</span>${logo(t, 16)}
      <span class="bname">${esc(team(t).name)}</span>${fpi(t)}<span class="bpts">${done ? pts : ""}</span></div>`;
  const cols = SEASON.rounds.map((r, i) => {
    const done = i <= shown;
    const body = done
      ? r.games.map(g => `<div class="bgame">${line(g.home, g.home_points, g, true)}${line(g.away, g.away_points, g, true)}</div>`).join("")
      : r.games.map(() => '<div class="bgame tbd"><div class="bteam">&nbsp;</div><div class="bteam">&nbsp;</div></div>').join("");
    return `<div class="bcol"><h3 class="subhead">${esc(r.name)}</h3>${body}</div>`;
  }).join("");
  const champ = shown === SEASON.rounds.length - 1 && SEASON.champion != null
    ? `<div class="champion" data-team="${SEASON.champion}">${logo(SEASON.champion, 40)}<div><div class="label">National champion</div>
        <div class="cname">${esc(team(SEASON.champion).name)}</div></div></div>` : "";
  $("s-board").innerHTML = champ + `<div class="bracket">${cols}</div>`;
}

function fillConferences() {
  const sel = $("s-conf");
  const keep = sel.value;
  sel.innerHTML = SEASON.conferences.map((c, i) =>
    `<option value="${i}">${esc(c.name)}</option>`).join("");
  const me = myIdx();
  const mineAt = SEASON.conferences.findIndex(c => c.order.includes(me));
  const sec = SEASON.conferences.findIndex(c => c.name === "SEC");
  sel.value = keep && sel.querySelector(`option[value="${keep}"]`) ? keep
    : String(mineAt >= 0 ? mineAt : Math.max(sec, 0));
}

function renderRight(recs) {
  const top25 = $("s-right").value === "top25";
  $("s-conf").hidden = top25;
  $("s-righttitle").textContent = top25
    ? `Committee top 25 ${POLL ? "(" + POLL.label + ")" : ""}` : "Standings";
  return top25 ? renderTop25(recs) : renderStandings(recs);
}

function renderTop25(recs) {
  if (!POLL || !POLL.teams.length) {
    $("s-standings").innerHTML =
      '<p class="foot">The committee has not published a ranking yet. Its first'
      + ' poll comes in week ' + (SEASON.polls ? Object.keys(SEASON.polls)[0] : 10)
      + '.</p>';
    return;
  }
  const me = myIdx();
  const seedOf = Object.fromEntries((SEASON.field || []).map(f => [f.team, f]));
  const final = POLL.label === "final";
  const rows = POLL.teams.map((t, i) => {
    const f = final ? seedOf[t] : null;
    return `<tr class="${t === me ? "mine" : ""}"><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${t}">${logo(t, 16)}${esc(team(t).name)}${fpi(t)}</td>
      <td class="num">${rec(recs[t])}</td>
      <td>${f ? `<span class="seedchip${f.bye ? " bye" : ""}">${f.seed}</span>` : ""}</td></tr>`;
  }).join("");
  $("s-standings").innerHTML = `<div class="tablewrap"><table class="standings">
    <thead><tr><th class="num">#</th><th>Team</th><th class="num">Record</th>
    <th>${final ? "Seed" : ""}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderStandings(recs) {
  const c = SEASON.conferences[Number($("s-conf").value) || 0];
  if (!c) { $("s-standings").innerHTML = ""; return; }
  const settled = STAGE >= stageIndex("ccg") && stageIndex("ccg") >= 0
    || STAGE >= stageIndex("selection");
  const pct = (w, l) => (w + l ? w / (w + l) : 0);
  const order = settled ? c.order.slice() : c.order.slice().sort((a, b) => {
    const ra = recs[a] || {}, rb = recs[b] || {};
    return (pct(rb.cw || 0, rb.cl || 0) - pct(ra.cw || 0, ra.cl || 0))
      || ((rb.cw || 0) - (ra.cw || 0))
      || (pct(rb.w || 0, rb.l || 0) - pct(ra.w || 0, ra.l || 0))
      || team(a).name.localeCompare(team(b).name);
  });
  const me = myIdx();
  const tg = settled ? c.title_game : null;
  const rows = order.map((t, i) => {
    const r = recs[t];
    const tag = settled && c.champion === t ? '<span class="seedchip bye">Champion</span>'
      : tg && (tg.home === t || tg.away === t) ? '<span class="seedchip">Title game</span>' : "";
    return `<tr class="${t === me ? "mine" : ""}"><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${t}">${logo(t, 16)}${rankTag(t)}${esc(team(t).name)}${fpi(t)} ${tag}</td>
      <td class="num">${c.crowns ? `${r ? r.cw : 0}-${r ? r.cl : 0}` : "-"}</td>
      <td class="num">${rec(r)}</td>
      <td class="num muted">${r ? r.pf : 0}-${r ? r.pa : 0}</td></tr>`;
  }).join("");
  $("s-standings").innerHTML = `<div class="tablewrap"><table class="standings">
    <thead><tr><th class="num">#</th><th>Team</th><th class="num">Conf</th>
    <th class="num">All</th><th class="num">PF-PA</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${settled ? "" : '<p class="foot">Order by conference record so far; tiebreakers apply at the end.</p>'}`;
}

// Events

$("tab-guide").addEventListener("click", () => showTab("guide"));
$("tab-season").addEventListener("click", () => showTab("season"));
$("s-new").addEventListener("click", newSeason);
$("s-fresh").addEventListener("change", newSeason);
$("s-play").addEventListener("click", togglePlay);
$("s-prev").addEventListener("click", () => { stopPlaying(); setStage(STAGE - 1); });
$("s-next").addEventListener("click", () => { stopPlaying(); setStage(STAGE + 1); });
$("s-stage").addEventListener("input", (e) => { stopPlaying(); setStage(Number(e.target.value)); });
$("s-conf").addEventListener("change", () => renderSeason());
$("s-right").addEventListener("change", () => renderSeason());
document.addEventListener("keydown", (e) => {
  if ($("seasonview").hidden || !SEASON || e.target.matches("input, select, textarea")) return;
  if (e.key === "ArrowRight") { stopPlaying(); setStage(STAGE + 1); }
  else if (e.key === "ArrowLeft") { stopPlaying(); setStage(STAGE - 1); }
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
});

/** app.js calls this when a different team is picked. */
function onTeamChanged() {
  if (!SEASON) return;
  fillConferences();
  renderSeason();
}

if (STATIC) {
  $("tab-season").hidden = true;
} else {
  try {
    if (localStorage.getItem("cfbroot.tab") === "season") showTab("season");
  } catch { /* private mode */ }
}
