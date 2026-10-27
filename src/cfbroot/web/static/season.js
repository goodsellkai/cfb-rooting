"use strict";

/* Sim a season: one simulated season, played back stage by stage.
 *
 * Uses app.js's globals: $, esc, num, team, logo, ratingOf, STATE, STATIC, WANTED.
 * The local app simulates a fresh season on request. The hosted site has no
 * server, so it draws from the seasons written out at build time.
 */

let SEASON = null;      // the sample: games, title games, ranking, field, bracket
let POLL = null;        // the ranking after the stage being shown
let POLL_RANK = new Map();
let PRE_RANK = new Map();   // and the one teams carried into that week
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

const UNSEEN = { now: [], fresh: [] };

/** The next saved season, in a fresh random order once they run out. */
function nextSavedSeason() {
  const fresh = $("s-fresh").checked;
  const pool = fresh ? UNSEEN.fresh : UNSEEN.now;
  if (!pool.length) {
    for (let i = 0; i < (STATE.sample_seasons || 1); i++) pool.push(i);
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
  }
  return `data/season/${fresh ? "fresh/" : ""}${pool.pop()}.json`;
}

async function newSeason() {
  stopPlaying();
  $("s-loading").hidden = false;
  $("s-new").disabled = true;
  while (!STATE) await new Promise(r => setTimeout(r, 100));   // app.js still booting
  try {
    const url = STATIC ? BASE + nextSavedSeason()
      : "/api/sample" + ($("s-fresh").checked ? "?from_start=true" : "");
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) throw new Error(await reason(resp));
    SEASON = await resp.json();
    if (!(await sameBuild(SEASON))) {
      SEASON = await (await fetch(url, { cache: "reload" })).json();
    }
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
    ? `Season ${S.seed}, replayed from week 1: all ${S.games.length} games `
      + `simulated on current ${STATE.rating_label}.`
    : `Season ${S.seed}: ${S.games.filter(g => !g.real).length} games `
      + `simulated, ${S.games.filter(g => g.real).length} already played.`;
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

/** The team's FPI, shown next to its name. */
function fpi(idx) {
  const r = team(idx).rating;
  return r == null ? "" : `<span class="fpi" title="${esc(STATE.rating_label)}">${num(r, 1)}</span>`;
}

const ROUND_SHORT = ["CFP 1st rd", "CFP QF", "CFP SF", "CFP final"];

/** A team's games still to come in this season, from the stage shown.
 *
 * Only games that would be known at the time. The regular season is on the
 * schedule from the start, but nobody knows in week eleven who is playing in
 * a title game, or who a team would meet in the semifinal. Those show up one
 * stage ahead, once the teams in them are settled.
 */
function seasonUpcomingFor(idx) {
  if (!SEASON) return null;
  const out = [];
  const firstRound = STAGES.findIndex(x => x.kind === "round");
  const known = (i, s) => {
    if (s.kind === "week") return true;           // on the schedule all along
    if (s.kind === "ccg") return STAGE >= i - 1;  // once the last week is in
    if (i === firstRound) return STAGE >= stageIndex("selection");
    return STAGE >= i - 1;                        // once the round before is in
  };
  for (let i = STAGE + 1; i < STAGES.length; i++) {
    const s = STAGES[i];
    if (!known(i, s)) continue;
    const label = (g) => s.kind === "week" ? `Wk ${g.week}`
      : s.kind === "ccg" ? "Title" : ROUND_SHORT[s.round] || s.label;
    for (const g of s.games) {
      if (g.home === idx || g.away === idx) out.push({ ...g, label: label(g) });
    }
  }
  return out;
}

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

/** The committee ranking at the stage being shown.
 *
 * ``after`` counts that week's games, which is what the top 25 shows once the
 * week is played. ``before`` is what the teams carried into the week, which is
 * the number worth seeing beside a score: 3 beat 12, not what they became
 * afterwards. ``prev`` is the poll before ``after``, for the change in rank.
 */
function currentPoll(before = false) {
  const stage = STAGES[STAGE];
  const polls = SEASON.polls || {};
  const weeks = Object.keys(polls).map(Number).sort((a, b) => a - b);
  const at = (i, label) => ({
    label, teams: polls[weeks[i]],
    prev: i > 0 ? polls[weeks[i - 1]] : null,
  });
  if (stage.kind === "week" && STAGE < stageIndex("selection")) {
    const i = weeks.findLastIndex(x => (before ? x < stage.week : x <= stage.week));
    return i >= 0 ? at(i, `through week ${weeks[i]}`) : null;
  }
  if (stage.kind === "ccg" && weeks.length) {
    // Nothing is ranked between the last week and the title games.
    return at(weeks.length - 1, `through week ${weeks[weeks.length - 1]}`);
  }
  return { label: "final", teams: SEASON.ranking.slice(0, 25).map(r => r.team),
           prev: weeks.length ? polls[weeks[weeks.length - 1]] : null };
}

function rankTag(idx) {
  const r = POLL_RANK.get(idx);
  return r ? `<span class="rk">${r}</span>` : "";
}

/** The rank a team took into the week, for the scoreboard. */
function rankTagPre(idx) {
  const r = PRE_RANK.get(idx);
  return r ? `<span class="rk" title="ranked ${r} going into this week">${r}</span>` : "";
}

// Rendering

function renderSeason() {
  if (!SEASON) return;
  const stage = STAGES[STAGE];
  $("s-label").innerHTML = `${esc(stage.label)}<small>${STAGE + 1} of ${STAGES.length}</small>`;
  $("s-prev").disabled = STAGE === 0;
  $("s-next").disabled = STAGE === STAGES.length - 1;
  const games = resultsSoFar();
  const recs = records(games);
  POLL = currentPoll();
  POLL_RANK = new Map((POLL ? POLL.teams : []).map((t, i) => [t, i + 1]));
  const pre = currentPoll(true);
  PRE_RANK = new Map((pre ? pre.teams : []).map((t, i) => [t, i + 1]));
  renderStats(games, recs);
  renderMine(games, recs);
  renderBoard(stage, recs);
  renderRight(recs);
  $("footmeta").textContent = `Simulated season ${SEASON.seed}`;
}

function statCard(label, value, detail) {
  return `<div><dt>${esc(label)}</dt><dd>${value}</dd>
    <dd class="detail">${detail || "&nbsp;"}</dd></div>`;
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
    return `<span class="res ${won ? "w" : "l"}${g.real ? "" : " sim"}" data-team="${opp}"
      title="${g.real ? "Played" : "Simulated"}"><i>${won ? "W" : "L"}</i>${mp}-${op}
      ${where} ${esc(team(opp).abbr || team(opp).name)}</span>`;
  }).join("");

  let status = "";
  if (STAGE >= stageIndex("selection")) {
    const rank = SEASON.ranking.findIndex(x => x.team === me) + 1;
    const f = SEASON.field.find(x => x.team === me);
    status = f ? `Ranked ${rank}, seed ${f.seed}${f.bye ? " with a bye" : ""}`
               : `Ranked ${rank}, missed the playoff`;
    const po = playoffSoFar().filter(g => g.home === me || g.away === me);
    const out = po.find(g => winner(g) !== me);
    if (out) status += `, out in the ${roundName(out).toLowerCase()}`;
    else if (SEASON.champion === me && STAGE === STAGES.length - 1) status += ", national champion";
  }
  el.innerHTML = `<div class="minehead" data-team="${me}">${logo(me, 30)}
      ${rankTag(me)}<b>${esc(team(me).name)}</b> <span class="muted">${rec(r)}
      (${r ? r.cw : 0}-${r ? r.cl : 0} ${esc(team(me).conference || "")})</span></div>
      <span class="minestatus">${status}</span>
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
      ${logo(t, 18)}<span class="gt-name">${home && !g.neutral ? '<span class="at">@</span>' : ""}${rankTagPre(t)}${esc(team(t).name)}</span>
      ${fpi(t)}<span class="gt-rec">${rec(recs[t])}</span><span class="gt-pts">${pts}</span></div>`;
  const foot = [
    isTitle ? `<span>${esc(g.conference)}</span>` : "",
    g.real ? '<span class="chipmini real">Final</span>' : '<span class="chipmini">Simulated</span>',
    g.neutral && !isTitle ? "<span>neutral</span>" : "",
    upset ? `<span class="chipmini upset">Upset: ${Math.round(100 * p)}% to win</span>` : "",
  ].join("");
  return `<div class="gt${g.home === me || g.away === me ? " mine" : ""}">
    ${row(g.away, g.away_points, false)}${row(g.home, g.home_points, true)}
    <div class="gt-foot">${foot}</div></div>`;
}

function renderSelection(recs) {
  $("s-boardtitle").textContent = "Selection Sunday";
  $("s-boardnote").textContent = "model committee ranking";
  const me = myIdx();
  const seedOf = Object.fromEntries(SEASON.field.map(f => [f.team, f]));
  const top = SEASON.ranking.slice(0, 25).map((r, i) => {
    const f = seedOf[r.team];
    return `<tr class="${r.team === me ? "mine" : ""}"><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${r.team}">${logo(r.team, 18)}<span class="tn">${esc(team(r.team).name)}</span>${fpi(r.team)}</td>
      <td class="num">${rec(recs[r.team])}</td>
      <td class="num">${num(r.rating, 3)}</td>
      <td>${f ? `<span class="seedchip${f.bye ? " bye" : ""}">${f.seed}</span>` : ""}</td></tr>`;
  }).join("");
  const field = SEASON.field.map(f => `<tr class="${f.team === me ? "mine" : ""}">
      <td class="num">${f.seed}</td>
      <td class="teamcell" data-team="${f.team}">${logo(f.team, 18)}<span class="tn">${esc(team(f.team).name)}</span>${fpi(f.team)}</td>
      <td class="muted">${esc(f.how)}${f.bye ? " · bye" : ""}</td></tr>`).join("");
  $("s-board").innerHTML = `<div class="twocol">
    <div><h3 class="subhead">Top 25</h3><div class="tablewrap"><table class="stand">
      <thead><tr><th class="num">#</th><th>Team</th><th class="num">Record</th>
      <th class="num">Rating</th><th>Seed</th></tr></thead><tbody>${top}</tbody></table></div></div>
    <div><h3 class="subhead">The field</h3><div class="tablewrap"><table class="stand">
      <thead><tr><th class="num">Seed</th><th>Team</th><th>How</th></tr></thead>
      <tbody>${field}</tbody></table></div></div></div>`;
}

// The 12-team bracket, drawn as a tree: each game sits beside the game its
// winner plays. The simulation lists games in the order it plays them, so
// these are their places top to bottom: the 1 seed's quarter of the bracket,
// then the 4's, the 2's and the 3's.
const BRACKET_ORDER = [[3, 0, 2, 1], [0, 3, 1, 2], [0, 1], [0]];
// Who can still reach each slot not yet decided, by seed, in the same order.
const BRACKET_OPEN = [
  null,
  [["", "8 or 9"], ["", "5 or 12"], ["", "7 or 10"], ["", "6 or 11"]],
  [["1, 8 or 9", "4, 5 or 12"], ["2, 7 or 10", "3, 6 or 11"]],
  [["Top semifinal winner", "Bottom semifinal winner"]],
];

function renderBracket() {
  const me = myIdx();
  const shown = STAGES[STAGE].round;
  // Each column names its round, and the stage control says which one is on.
  $("s-boardtitle").textContent = "The playoff";
  const seedOf = Object.fromEntries(SEASON.field.map(f => [f.team, f.seed]));
  const rounds = SEASON.rounds;

  // One line of a game: a team, or who can still get there.
  const line = (t, pts, g, played, open) => {
    if (t == null) {
      return `<div class="bteam open"><span class="bseed"></span><span class="bname">${esc(open)}</span></div>`;
    }
    const cls = played ? (winner(g) === t ? " win" : " out") : "";
    return `<div class="bteam${cls}${t === me ? " mine" : ""}" data-team="${t}">
      <span class="bseed">${seedOf[t] ?? ""}</span>${logo(t, 18)}
      <span class="bname">${esc(team(t).name)}</span>
      <span class="bpts">${played ? pts : ""}</span></div>`;
  };

  // A round is played up to the stage shown; the next one's teams are known
  // from the results before it; later ones only by who could get there. A
  // quarterfinal's home side is a team with a bye, known all along.
  const box = (i, k, pos) => {
    const g = rounds[i].games[k];
    const played = i <= shown;
    const known = played || i === shown + 1;
    const open = (BRACKET_OPEN[i] || [])[pos] || ["", ""];
    const home = known || i === 1 ? g.home : null;
    const away = known ? g.away : null;
    const cls = `bgame${i === rounds.length - 1 ? " final" : ""}${played ? "" : " ahead"}`;
    return `<div class="${cls}" style="--r:${2 + pos * 2 ** (i === 0 ? 1 : i)};--span:${2 ** (i === 0 ? 1 : i)};--c:${2 * i + 1}">
      ${line(home, g.home_points, g, played, open[0])}${line(away, g.away_points, g, played, open[1])}</div>`;
  };

  let html = "";
  rounds.forEach((r, i) => {
    html += `<h3 class="subhead bhead" style="--c:${2 * i + 1}">${esc(r.name)}</h3>`;
    BRACKET_ORDER[i].forEach((k, pos) => { html += box(i, k, pos); });
    if (i === rounds.length - 1) return;
    // The lines to the next round: one elbow each from the first round into
    // a quarterfinal's second line, and one bracket for each pair after.
    const next = BRACKET_ORDER[i + 1].length;
    for (let pos = 0; pos < next; pos++) {
      const span = 2 ** (i + 1);
      html += `<span class="bline ${i === 0 ? "feed" : "join"}" aria-hidden="true"
        style="--r:${2 + pos * span};--span:${span};--c:${2 * i + 2}"></span>`;
    }
  });

  const champ = shown === rounds.length - 1 && SEASON.champion != null
    ? `<div class="champion${SEASON.champion === me ? " mine" : ""}" data-team="${SEASON.champion}">
        ${logo(SEASON.champion, 34)}
        <div><div class="label">National champion</div>
          <div class="cname">${esc(team(SEASON.champion).name)}</div></div></div>` : "";
  // Anything but the twelve-team field the tree is drawn for goes as a list.
  const tree = rounds.length === 4 && rounds[0].games.length === 4;
  $("s-board").innerHTML = champ + (tree ? `<div class="bracket">${html}</div>`
    : rounds.map((r, i) => `<h3 class="subhead">${esc(r.name)}</h3>`
        + r.games.map(g => `<div class="bgame">${line(g.home, g.home_points, g, i <= shown, "")}`
          + `${line(g.away, g.away_points, g, i <= shown, "")}</div>`).join("")).join(""));
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
  const was = new Map((POLL.prev || []).map((t, i) => [t, i + 1]));
  const rows = POLL.teams.map((t, i) => {
    const f = final ? seedOf[t] : null;
    return `<tr class="${t === me ? "mine" : ""}"><td class="num">${i + 1}</td>
      <td class="num">${moveTag(was, t, i + 1)}</td>
      <td class="teamcell" data-team="${t}">${logo(t, 16)}<span class="tn">${esc(team(t).name)}</span>${fpi(t)}</td>
      <td class="num">${rec(recs[t])}</td>
      <td>${f ? `<span class="seedchip${f.bye ? " bye" : ""}">${f.seed}</span>` : ""}</td></tr>`;
  }).join("");
  // Whoever was ranked last time and is not now, as a line of text under
  // the table rather than rows competing with it.
  const inNow = new Set(POLL.teams);
  const gone = (POLL.prev || []).filter(t => !inNow.has(t));
  const out = gone.length
    ? `<p class="dropped">Dropped out: ${gone.map(t =>
        `<span data-team="${t}">${was.get(t)} ${esc(team(t).name)}</span>`).join(", ")}</p>`
    : "";
  $("s-standings").innerHTML = `<div class="tablewrap"><table class="stand">
    <thead><tr><th class="num">#</th><th class="num">+/-</th><th>Team</th>
    <th class="num">Record</th>
    <th>${final ? "Seed" : ""}</th></tr></thead><tbody>${rows}</tbody></table></div>${out}`;
}

/** How far a team moved since the poll before, as the committee shows it. */
function moveTag(was, team, now) {
  if (!was.size) return "";
  const before = was.get(team);
  if (!before) return '<span class="mv new">new</span>';
  const d = before - now;
  if (d === 0) return '<span class="mv flat">-</span>';
  return `<span class="mv ${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>`;
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
      <td class="teamcell" data-team="${t}">${logo(t, 16)}${rankTag(t)}<span class="tn">${esc(team(t).name)}</span>${fpi(t)} ${tag}</td>
      <td class="num">${c.crowns ? `${r ? r.cw : 0}-${r ? r.cl : 0}` : "-"}</td>
      <td class="num">${rec(r)}</td>
      <td class="num muted">${r ? r.pf : 0}-${r ? r.pa : 0}</td></tr>`;
  }).join("");
  $("s-standings").innerHTML = `<div class="tablewrap"><table class="stand">
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

try {
  if (!window.CFBROOT_INFO && localStorage.getItem("cfbroot.tab") === "season") {
    showTab("season");
  }
} catch { /* private mode */ }
