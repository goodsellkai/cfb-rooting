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
// A team page sits two levels down, so the hosted build says how to get back
// to the root for data and assets.
const BASE = window.CFBROOT_BASE || "";
const URLS = STATIC
  ? { state: BASE + "data/state.json",
      team: (t) => `${BASE}data/team/${t.slug || slugOf(t.name)}.json` }
  : { state: "/api/state", team: (t) => "/api/team/" + encodeURIComponent(t.name) };

/** A team's piece of the address, the same rule the build uses. */
function slugOf(name) {
  return String(name || "").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase();
}

/** Why a request failed, whether or not the answer was JSON. */
async function reason(resp) {
  try {
    return (await resp.json()).detail || resp.statusText;
  } catch {
    return resp.statusText || ("HTTP " + resp.status);
  }
}

/* A page kept open long enough asks for files from a build that has been
 * replaced. The answer is the 404 page, which is not JSON, and every browser
 * says so in its own words. One reload puts the page back in step; the flag
 * means it happens once, so a site that really is broken says so instead. */
function staleReload() {
  try {
    if (sessionStorage.getItem("cfbroot.reloaded")) return false;
    sessionStorage.setItem("cfbroot.reloaded", "1");
  } catch {
    return false;                      // private mode: say something instead
  }
  location.reload();
  return true;
}

/** Back after a while: if the site has been rebuilt, start again on the new one. */
async function checkBuild() {
  if (!STATIC || !STATE || !STATE.build) return;
  try {
    const fresh = await (await fetch(URLS.state, { cache: "reload" })).json();
    if (!fresh.build || fresh.build === STATE.build) return;
    // Once per build, so a held copy of the season data cannot start a loop.
    try {
      if (sessionStorage.getItem("cfbroot.build") === fresh.build) return;
      sessionStorage.setItem("cfbroot.build", fresh.build);
    } catch {
      return;
    }
    location.reload();
  } catch { /* offline; what is on screen still works */ }
}

/* Every data file says which build wrote it, so a page holding one build's
 * team numbering can tell when the files have moved on. Pick up the new
 * season data and let the caller ask again. */
async function refreshState() {
  if (!STATIC) return false;
  try {
    STATE = await (await fetch(URLS.state, { cache: "reload" })).json();
  } catch {
    return false;
  }
  renderTopMeta();
  return true;
}

async function sameBuild(body) {
  if (!STATIC || !body || !body.build || body.build === STATE.build) return true;
  await refreshState();
  return body.build === STATE.build;
}

const CONF_TITLE = {
  clear: "The simulations separate the two results",
  leaning: "The direction is a lean, not a clear result",
  thin: "Too few simulations on one side to say",
};

// Startup

async function boot() {
  try {
    // Everything else is keyed off this, so it is checked rather than taken
    // from the browser's copy.
    STATE = await (await fetch(URLS.state, { cache: "no-cache" })).json();
  } catch {
    if (staleReload()) return;
    banner("Could not load the season. Reload the page to try again.", false);
    return;
  }
  setHeaderImage();
  renderTopMeta();
  if (window.CFBROOT_INFO) {          // a page that is only words
    document.body.classList.add("info");
    document.querySelector(".controlbar").hidden = true;
    document.querySelector(".tabs").hidden = true;
    return;
  }
  watchLive();
  fillTeams();
  fillMetrics();
  fillWeeks();
  showNotes();
  let saved = window.CFBROOT_TEAM || null;
  if (!saved) {
    try { saved = localStorage.getItem("cfbroot.team"); } catch { /* private mode */ }
  }
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
    banner("Could not load the real season, so this is a fake demo one. "
      + "Check the connection and restart.", false);
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

const LINKS = {
  poll: "https://www.espn.com/college-football/rankings",
  fpi: "https://www.espn.com/college-football/fpi",
};

function chip(text, value, href, title) {
  const inner = `${esc(text)} <b>${esc(value)}</b>`;
  const tip = title ? ` title="${esc(title)}"` : "";
  return href
    ? `<a class="chip" href="${href}" target="_blank" rel="noopener"${tip}>${inner}</a>`
    : `<span class="chip"${tip}>${inner}</span>`;
}

const ASSETS = STATIC ? BASE + "static/" : "/static/";

function setHeaderImage() {
  const n = 1 + Math.floor(Math.random() * 4);
  document.querySelector(".topbar").style.backgroundImage =
    `url("${ASSETS}header/${n}.jpg")`;
}

function renderTopMeta() {
  const pw = STATE.poll_weeks || {};
  const poll = pw.cfp ? chip("CFP poll", "week " + pw.cfp, LINKS.poll)
    : pw.ap ? chip("AP poll", "week " + pw.ap, LINKS.poll) : "";
  const carried = STATE.carried_games
    ? `Published ${ago(STATE.ratings_updated)}, then carried forward through `
      + `${STATE.carried_games} game${STATE.carried_games > 1 ? "s" : ""} `
      + `finished since.`
    : "";
  const rating = STATE.rating_label === "FPI"
    ? chip("FPI", ago(STATE.ratings_updated) || "-", LINKS.fpi, carried)
    : chip(STATE.rating_label, ago(STATE.ratings_updated) || "-", null, carried);
  const sims = STATE.loaded_at
    ? chip("Simulated", ago(new Date(1000 * STATE.loaded_at).toISOString())) : "";
  $("topmeta").innerHTML = poll + rating + sims;
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


// Live scores
//
// The odds come from the last build, which can be an hour old on a Saturday.
// The scores do not have to be: the page asks ESPN what is happening now and
// marks each game accordingly, so a game that has already been decided says
// so rather than looking like it is still to come.

const LIVE = new Map();
const SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/"
  + "college-football/scoreboard?groups=80&limit=400";

/** Two teams, in a fixed order, as one string. */
function gameKey(g) {
  const name = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return [name(g.home ?? g.home_name), name(g.away ?? g.away_name)].sort().join("|");
}

function liveState(ev) {
  const c = ev.competitions && ev.competitions[0];
  if (!c) return null;
  const st = c.status && c.status.type;
  const side = {};
  for (const x of c.competitors || []) side[x.homeAway] = x;
  const home = side.home, away = side.away;
  if (!home || !away || !st) return null;
  const score = `${away.team.abbreviation} ${away.score}, `
    + `${home.team.abbreviation} ${home.score}`;
  return {
    state: st.state,                       // pre, in or post
    detail: st.shortDetail || st.detail,   // "Q3 4:12" or a kickoff time
    score,
    hp: Number(home.score), ap: Number(away.score),
    home: (home.team.location || ""), away: (away.team.location || ""),
  };
}

async function refreshLive() {
  try {
    const resp = await fetch(SCOREBOARD, { cache: "no-store" });
    if (!resp.ok) return;
    const data = await resp.json();
    LIVE.clear();
    for (const ev of data.events || []) {
      const s = liveState(ev);
      if (!s) continue;
      if (s.state === "in") s.detail = `${s.detail} · ${s.score}`;
      LIVE.set(gameKey(s), s);
    }
  } catch { /* offline, or ESPN is having a day; the page still works */ }
}

/** Every few minutes while a tab is open, and again when it comes back. */
function watchLive() {
  const tick = async () => {
    await refreshLive();
    if (RESULT) { renderOwnGames(); renderRootList(); }
  };
  tick();
  setInterval(() => { if (!document.hidden) tick(); }, 180000);
  let away = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { away = Date.now(); return; }
    tick();
    // Half an hour away is long enough for the site to have been rebuilt.
    if (away && Date.now() - away > 1800000) checkBuild();
    away = 0;
  });
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
  let t = teamNamed($("team").value);
  if (!t || (RESULT && RESULT.team === t.name && WANTED === t.name)) return;
  WANTED = t.name;
  try { localStorage.setItem("cfbroot.team", t.name); } catch { /* private mode */ }
  if (typeof onTeamChanged === "function") onTeamChanged();   // season.js
  let rebuilt = false;
  for (;;) {
    let resp, body;
    try {
      resp = await fetch(URLS.team(t), rebuilt ? { cache: "reload" } : undefined);
      body = await resp.json();
    } catch (err) {
      // A rebuild landing between the page loading and this request leaves
      // the old address pointing at nothing, and the 404 page is not JSON.
      if (!rebuilt && await refreshState()) {
        rebuilt = true;
        t = teamNamed(WANTED) || t;
        continue;
      }
      showProgress(null);
      if (staleReload()) return;
      banner("The site was rebuilt while this page was open. Reload it for "
             + "the new numbers.", false);
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
    if (!rebuilt && !(await sameBuild(body))) {
      rebuilt = true;
      t = teamNamed(WANTED) || t;    // a rebuild can renumber the teams
      continue;
    }
    RESULT = body;
    render();
    // Each team has its own address on the hosted site, so a link to what
    // is on screen goes to the same place.
    if (STATIC && t.slug && !location.pathname.endsWith(`/team/${t.slug}/`)) {
      history.pushState({ team: t.name }, "", `${BASE}team/${t.slug}/`);
    }
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

/** Every game on the chosen slate, before the filters narrow it down. */
function slateGames() {
  const wk = selectedWeek();
  const played = $("showplayed").checked;
  const replay = (RESULT.replay || {}).games || [];
  const games = RESULT.games.concat(played ? replay : [])
    .filter(g => wk === null || g.week === wk);
  return played ? games : games.filter(g => !finalOf(g));
}

function visibleGames() {
  const key = $("primary").value;
  let games = slateGames();
  if ($("sigfilter").value === "sig") {
    games = games.filter(g => sigOf(g.swings[key]));
  }
  const q = ($("rootsearch").value || "").trim().toLowerCase();
  if (q) {
    games = games.filter(g => (g.home + " " + g.away).toLowerCase().includes(q)
      || [g.home_idx, g.away_idx].some(
        i => (team(i).conference || "").toLowerCase().includes(q)));
  }
  const byTime = $("rootsort").value === "time";
  return games.sort((a, b) => {
    const sa = a.swings[key], sb = b.swings[key];
    return (byTime ? kickoffOrder(a) - kickoffOrder(b) : 0)
      || (sigOf(sb) - sigOf(sa))
      || (sb.reliable - sa.reliable)
      || (conservative(sb) - conservative(sa))
      || (Math.abs(sb.delta) - Math.abs(sa.delta));
  });
}

function ownGames() {
  const wk = selectedWeek();
  const replay = (RESULT.replay || {}).own_games || [];
  const games = $("showplayed").checked
    ? RESULT.own_games.concat(replay) : RESULT.own_games;
  return games.filter(g => wk === null || g.week === wk)
    .filter(g => $("showplayed").checked || !finalOf(g))
    .sort((a, b) => a.week - b.week);
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
    `${STATE.year} week ${STATE.current_week} · `
    + `${RESULT.n_sims.toLocaleString()} simulated seasons`;
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
    b.innerHTML = `<span class="col"><i style="height:${(100 * dist[i] / max).toFixed(2)}%"></i></span>`
      + `<span>${labelFn(i)}</span>`;
    el.appendChild(b);
  }
}

/** "Sat 3:30 PM · ABC", in the reader's own time zone. */
/** When a game starts, in the reader's own zone.
 *
 * A game whose time is not set yet is dated midnight Eastern, which is the
 * evening before in half the country, so those say the day and nothing more.
 */
function whenText(g) {
  if (!g.start_date) return "";
  const t = new Date(g.start_date);
  if (isNaN(t)) return "";
  if (g.time_set === false) {
    const day = t.toLocaleDateString([], { weekday: "short",
                                           timeZone: "America/New_York" });
    return `${day}, time TBA`;
  }
  return t.toLocaleString([], { weekday: "short", hour: "numeric",
                                minute: "2-digit", timeZoneName: "short" });
}

/** The final score of a game that is over, from the build or the scoreboard. */
function finalOf(g) {
  if (g.home_points != null && g.away_points != null) {
    return { home: g.home_points, away: g.away_points, fresh: false };
  }
  const live = LIVE.get(gameKey(g));
  if (live && live.state === "post" && Number.isFinite(live.hp)) {
    return { home: live.hp, away: live.ap, fresh: true };
  }
  return null;
}

/** The odds this game was weighed against: before its week, or right now. */
function baseFor(g, key) {
  const replay = RESULT.replay || {};
  return g.home_points != null && replay.before
    ? replay.before[key] : RESULT.headline[key].p;
}

/** Where a game falls when the list is in kickoff order. */
function kickoffOrder(g) {
  const t = Date.parse(g.start_date || "");
  if (isNaN(t)) return Infinity;
  return g.time_set === false ? t + 23 * 3600e3 : t;   // TBA ends its day
}

function kickoff(g) {
  const bits = [];
  const when = whenText(g);
  if (when) bits.push(esc(when));
  if (g.broadcast) bits.push(esc(g.broadcast));
  const live = LIVE.get(gameKey(g));
  const tv = g.broadcast ? ` · ${esc(g.broadcast)}` : "";
  if (live && live.state === "in") {
    return `<span class="when live">${esc(live.detail)}${tv}</span>`;
  }
  if (live && live.state === "post") {
    return `<span class="when done">Final ${esc(live.score)}</span>`;
  }
  return bits.length ? `<span class="when">${bits.join(" · ")}</span>` : "";
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

  const maxAbs = Math.max(1e-6, ...games.map(g => {
    const s = g.swings[key];
    return Math.max(Math.abs(s.lo || 0), Math.abs(s.hi || 0), Math.abs(s.delta || 0));
  }));

  let html = `<div class="tablewrap"><table class="games"><thead><tr>
      <th>Matchup</th>
      <th class="num">If away wins</th>
      <th class="num">If home wins</th>
      <th class="swingcell">Impact</th>
    </tr></thead><tbody>`;

  for (const g of games) {
    const s = g.swings[key];
    const done = finalOf(g);
    const base = baseFor(g, key);
    const conf = confidenceOf(s);
    const rootHome = s.home;
    const pHome = g.p_home_win;
    const homeWon = done ? done.home > done.away : null;

    // Before the game, the team to root for is in bold; after it, the winner.
    const mark = (isHome) => "side"
      + ((done ? homeWon === isHome : rootHome === isHome) ? " root" : "")
      + (!done && conf === "clear" && rootHome === isHome ? " strong" : "");
    const side = (isHome) => {
      const idx = isHome ? g.home_idx : g.away_idx;
      return `<span class="${mark(isHome)}" data-team="${idx}">`
        + `${logo(idx, 18)}${pollTag(idx)}${esc(isHome ? g.home : g.away)}</span>`;
    };

    let note, swing;
    if (done) {
      const actual = homeWon ? s.p_if_home : s.p_if_away;
      const move = actual - base;
      const dir = move > 0 ? "up" : (move < 0 ? "down" : "flat");
      // The impact column is gone on a narrow screen, so the move rides
      // along with the score there.
      note = `<span class="when done">Final ${done.away}-${done.home}`
        + ` <b class="movesmall ${dir}">${signed(move)}pp</b></span>`;
      swing = `<td class="swingcell final">
          <div class="impact"><span class="amt ${dir}">${signed(move)}pp</span></div>
        </td>`;
    } else {
      const w = Math.min(100, 100 * Math.abs(s.delta) / maxAbs);
      const gap = Math.abs(s.delta);
      const dim = sigOf(s) ? "on" : "dim";
      note = kickoff(g);
      swing = `<td class="swingcell ${conf}" title="${esc(CONF_TITLE[conf])}">
          <div class="impact">
            <span class="amt">${(100 * gap).toFixed(gap >= 0.01 ? 1 : 2)}%</span>
            <span class="bar"><i class="${dim}" style="width:${w}%"></i></span>
          </div>
        </td>`;
    }

    html += `<tr${done ? ' class="played"' : ""}>
      <td class="matchup">
        ${side(false)}
        <span class="at">${g.neutral ? "vs" : "@"}</span>
        ${side(true)}
        ${g.neutral ? '<span class="tag">neutral</span>' : ""}
        ${note}
      </td>
      ${outcomeCell(1 - pHome, s.p_if_away, base, !done && !rootHome,
                    done ? !homeWon : null)}
      ${outcomeCell(pHome, s.p_if_home, base, !done && rootHome,
                    done ? homeWon : null)}
      ${swing}
    </tr>`;
  }
  return html + "</tbody></table></div>";
}

/** One side of a game: the odds it leaves behind, and how likely it was. */
function outcomeCell(likelihood, p, base, isGood, happened) {
  const d = p - base;
  const dirCls = d > 0 ? "up" : (d < 0 ? "down" : "flat");
  const state = happened === null || happened === undefined ? ""
    : (happened ? " happened" : " missed");
  return `<td class="num outcome ${isGood ? "good" : ""}${state}">
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
  const games = visibleGames();
  const shown = games.slice(0, 80);
  const slate = slateGames().length;
  const over = shown.filter(g => finalOf(g)).length;

  // Only the latest week's results get a value put on them, so say so
  // when an older slate is on screen.
  const replay = RESULT.replay || {};
  const wk = selectedWeek();
  const old = $("showplayed").checked && replay.week && wk !== null
    && wk < replay.week
    ? `<p class="foot">Week ${wk} is in the books. What each result was `
      + `worth is worked out for week ${replay.week}.</p>` : "";

  let body;
  if (shown.length) {
    body = gameRowsHTML(shown) + old;
  } else if (!slate) {
    body = old || `<p class="foot">No games left on this slate.</p>`;
  } else {
    body = `<p class="foot">No games here move your odds enough to call. `
      + `Switch Show to all games to see the rest.</p>` + old;
  }
  $("rootlist").innerHTML = body;

  $("rootcount").textContent = `${shown.length} of ${slate} games`
    + (over ? `, ${over} played` : "");
}

function renderLeague() {
  const key = $("leaguemetric").value;
  const rows = RESULT.league.slice().sort((a, b) => b.p[key] - a.p[key]);
  // ESPN publishes its own odds on some of these, and not on others.
  const showEspn = rows.some(r => r.espn && r.espn[key] != null);
  const polls = STATE.polls || {};
  const weeks = STATE.poll_weeks || {};
  const showAp = !!polls.ap, showCfp = !!polls.cfp;
  const head = (k, label) => `<th class="num" title="${label} poll, week ${weeks[k]}">${label}</th>`;
  let html = `<div class="tablewrap tall"><table><thead><tr>
    <th class="num">#</th><th>Team</th><th class="conf">Conference</th>
    <th class="num conf">${esc(STATE.rating_label)}</th>
    ${showCfp ? head("cfp", "CFP") : ""}${showAp ? head("ap", "AP") : ""}
    <th class="num">${esc(metricLabel(key))}</th>
    ${showEspn ? '<th class="num" title="What ESPN gives for the same thing, '
      + 'from its FPI page">ESPN</th>' : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const pr = pollRank(r.idx);
    html += `<tr><td class="num">${i + 1}</td>
      <td class="teamcell" data-team="${r.idx}">${logo(r.idx, 18)}${esc(r.team)}</td>
      <td class="muted conf">${esc(r.conference || "")}</td>
      <td class="num conf">${num(r.rating, 1)}</td>
      ${showCfp ? `<td class="num muted">${pr.cfp || ""}</td>` : ""}
      ${showAp ? `<td class="num muted">${pr.ap || ""}</td>` : ""}
      <td class="num"><b>${pct(r.p[key])}</b></td>
      ${showEspn ? `<td class="num muted">${r.espn && r.espn[key] != null
        ? pct(r.espn[key]) : "-"}</td>` : ""}</tr>`;
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

/** The games a team has left: the rest of the real season, or the rest of a
 * simulated one from the week being shown. */
/** Where a team is ranked, for the card: the published poll in the guide,
 * this week's ranking in a sample season. */
function cardRank(idx, inSeason) {
  if (inSeason) {
    return (typeof POLL_RANK !== "undefined" && POLL_RANK.get(idx)) || null;
  }
  return pollRank(idx).best || null;
}

function rankCell(idx, inSeason) {
  const r = cardRank(idx, inSeason);
  return r ? `<span class="rk">${r}</span>` : "";
}

function teamUpcoming(idx, inSeason) {
  if (inSeason && typeof seasonUpcomingFor === "function") {
    return seasonUpcomingFor(idx) || [];
  }
  return (STATE.upcoming || [])
    .filter(g => g.home === idx || g.away === idx)
    .map(g => ({ ...g, label: `Wk ${g.week}` }));
}

function teamResults(idx, inSeason) {
  if (inSeason && typeof seasonGamesFor === "function") {
    const g = seasonGamesFor(idx);
    if (g) return g;
  }
  return (STATE.played || [])
    .filter(g => g.home === idx || g.away === idx)
    .map(g => ({ ...g, label: g.title_game ? "Title" : `Wk ${g.week}` }));
}

function tipHTML(idx, games, inSeason) {
  const t = team(idx);
  let w = 0, l = 0;
  const next = teamUpcoming(idx, inSeason).slice(0, 6).map(g => {
    const home = g.home === idx;
    const opp = home ? g.away : g.home;
    const where = g.neutral ? "vs" : (home ? "vs" : "@");
    // No odds inside a sample season: they would be the real season's,
    // not this simulated one's.
    const p = (inSeason || g.p_home == null) ? null
      : (home ? g.p_home : 1 - g.p_home);
    return `<tr><td class="muted">${esc(g.label)}</td><td class="muted">${where}</td>
      <td class="teamcell">${logo(opp, 14)}${rankCell(opp, inSeason)}${esc(team(opp).name)}</td>
      <td class="num muted">${p == null ? "" : pct(p, 0)}</td></tr>`;
  }).join("");
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
      <td class="teamcell">${logo(opp, 14)}${rankCell(opp, inSeason)}${esc(team(opp).name)}</td></tr>`;
  }).join("");
  const rating = t.rating != null ? ` · ${esc(STATE.rating_label)} ${num(t.rating, 1)}` : "";
  // In the guide, where a team sits in the published polls. In a sample
  // season, where this week's ranking puts it.
  const r = cardRank(idx, inSeason);
  const ranked = r ? `#${r}` : "";
  const pick = STATE.teams.some(x => x.idx === idx) ? "Click to make this your team" : "";
  return `<div class="tiphead">${logo(idx, 24)}<div>
      <div class="tipname">${esc(t.name)} <span class="muted">${w}-${l}</span>
        ${ranked ? `<span class="tiprank">${ranked}</span>` : ""}</div>
      <div class="tipsub">${esc(t.conference || "Independent")}${rating}</div></div></div>
    ${rows ? `<table class="tiptable"><tbody>${rows}</tbody></table>`
           : '<p class="tipnone">No games played yet.</p>'}
    ${next ? `<p class="tiphead2">Still to play</p>
      <table class="tiptable next"><tbody>${next}</tbody></table>` : ""}
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
  const inSeason = !!el.closest("#seasonview");
  tip.innerHTML = tipHTML(idx, teamResults(idx, inSeason), inSeason);
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

window.addEventListener("popstate", () => location.reload());
$("rootsearch").addEventListener("input", () => { if (RESULT) renderRootList(); });
for (const id of ["primary", "week", "sigfilter", "rootsort", "showplayed"]) {
  $(id).addEventListener("change", () => { if (RESULT) render(); });
}
$("leaguemetric").addEventListener("change", () => { if (RESULT) renderLeague(); });

boot();
