"use strict";

const $ = (id) => document.getElementById(id);

/* Percentages always show two decimals. A chance that rounds to nothing reads
 * as under the smallest step shown, since a simulation cannot rule a thing
 * out. ``zero`` lets a true zero through, for the conference measures, where
 * no simulated season producing it is worth saying. */
const pct = (x, d = 2, zero = false) => {
  if (x === null || x === undefined || Number.isNaN(x)) return "-";
  const step = 10 ** -d;
  if (Math.abs(100 * x) < step / 2 && !(zero && x === 0)) return `<${step.toFixed(d)}%`;
  return (100 * x).toFixed(d) + "%";
};
const EXACT_ZERO = new Set(["win_conference", "make_conf_title_game"]);
/** A measure's chance, with that measure's rule for zero. */
const pctOf = (x, key, d = 2) => pct(x, d, EXACT_ZERO.has(key));
const signed = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : (x >= 0 ? "+" : "−") + (100 * Math.abs(x)).toFixed(d);
const num = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : x.toFixed(d);
const dirOf = (d) => (d > 0 ? "up" : (d < 0 ? "down" : "flat"));

let STATE = null;      // /api/state
let RESULT = null;     // the shown team: every remaining game x every metric
let WANTED = null;     // the team most recently asked for

// What the guide is measured by, and how the list is cut. Week and the rest
// are filters on numbers already loaded, so changing them never asks again.
let METRIC = "make_playoff";
let SORT = "impact";
let SHOW = "sig";

// The hosted build is plain files written ahead of time; the local app asks
// its server. Both return the same JSON.
const STATIC = !!window.CFBROOT_STATIC;
// A team page sits two levels down, so the hosted build says how to get back
// to the root. It is made absolute here, because picking a team moves the
// address to that team's page and a relative path would move with it.
const BASE = STATIC ? new URL(window.CFBROOT_BASE || "./", location.href).href : "/";
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
  clear: "Significant effect on your odds",
  leaning: "Likely effect on your odds",
  thin: "One result almost never happens",
};

// Startup

/** On the missing-page page, the team a mistyped address was nearest to:
 * the last part of the address against every team's, by edit distance. */
function suggestTeam() {
  const part = location.pathname.split("/").filter(Boolean).pop() || "";
  const want = slugOf(decodeURIComponent(part));
  if (want.length < 3) return;
  const dist = (a, b) => {
    let row = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const next = [i];
      for (let j = 1; j <= b.length; j++) {
        next[j] = Math.min(row[j] + 1, next[j - 1] + 1,
                           row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      }
      row = next;
    }
    return row[b.length];
  };
  let best = null, bestD = Infinity;
  for (const t of STATE.teams) {
    const d = dist(want, t.slug);
    if (d < bestD) { best = t; bestD = d; }
  }
  if (!best || bestD > Math.max(2, Math.floor(best.slug.length / 3))) return;
  const p = $("didyoumean");
  const href = new URL(`team/${best.slug}/`, new URL(BASE, location.href)).href;
  p.innerHTML = `Looking for <a href="${esc(href)}">${esc(best.name)}</a>?`;
  p.hidden = false;
}

async function boot() {
  if (window.CFBROOT_INFO) document.body.classList.add("info");
  try {
    // Everything else is keyed off this, so it is checked rather than taken
    // from the browser's copy.
    STATE = await (await fetch(URLS.state, { cache: "no-cache" })).json();
  } catch {
    if (staleReload()) return;
    banner("Could not load the season. Reload the page to try again.", false);
    return;
  }
  renderTopMeta();
  markSubnav();
  focusableTeams();
  if (window.CFBROOT_INFO) {          // a page that is only words
    if ($("didyoumean")) suggestTeam();
    return;
  }
  try {
    const m = localStorage.getItem("cfbroot.metric");
    if (m && STATE.metrics.some(x => x.key === m)) METRIC = m;
  } catch { /* private mode */ }
  watchLive();
  fillTeams();
  fillWeeks();
  showNotes();
  watchToolbar();
  let saved = window.CFBROOT_TEAM || null;
  if (!saved) {
    try {
      // Arriving from the logo means the front page, not the team last seen.
      if (sessionStorage.getItem("cfbroot.home")) sessionStorage.removeItem("cfbroot.home");
      else saved = localStorage.getItem("cfbroot.team");
    } catch { /* private mode */ }
  }
  if (saved && teamNamed(saved)) {
    $("team").value = saved;
    loadTeam();
  } else {
    setBand(null);
  }
}

/** The strip of links says which page this is. */
function markSubnav() {
  for (const a of document.querySelectorAll("#subnav a")) {
    if (new URL(a.href, location.href).pathname === location.pathname) {
      a.setAttribute("aria-current", "page");
    }
  }
}

/** Team links on the written pages carry their team, so hovering one opens
 * its card as hovering any other team name does. */
function tagTeamLinks() {
  if (!STATE) return;
  if (!tagTeamLinks.slugs) tagTeamLinks.slugs = new Map(STATE.teams.map(t => [t.slug, t.idx]));
  for (const a of document.querySelectorAll('a[href*="team/"]:not([data-team])')) {
    const m = /team\/([^/?#]+)\/?$/.exec(a.getAttribute("href") || "");
    if (m && tagTeamLinks.slugs.has(m[1])) a.dataset.team = tagTeamLinks.slugs.get(m[1]);
  }
}

function banner(text, ok) {
  const el = $("banner");
  el.textContent = text;
  el.className = "banner" + (ok ? " info" : "");
  el.hidden = !text;
}

/** What the build says about its own data, for the local app. The hosted
 * site keeps that in the build log and out of the reader's way. */
function showNotes() {
  const notes = STATIC ? [] : (STATE.notes || []).filter(Boolean);
  if (!STATE.has_api_key) {
    banner("The live season could not be loaded, so this is demo data. "
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

function metaItem(text, value, href, title, cls) {
  const inner = `${esc(text)} <b>${esc(value)}</b>`;
  const tip = title ? ` title="${esc(title)}"` : "";
  const kind = cls ? ` class="${cls}"` : "";
  return href
    ? `<a${kind} href="${href}" target="_blank" rel="noopener"${tip}>${inner}</a>`
    : `<span${kind}${tip}>${inner}</span>`;
}

const ASSETS = STATIC ? BASE + "static/" : "/static/";

/** 2500000 as "2.5 million". */
function simsWord(n) {
  return n >= 1e6 ? `${+(n / 1e6).toFixed(2)} million` : n.toLocaleString();
}

function renderTopMeta() {
  const pw = STATE.poll_weeks || {};
  const poll = pw.cfp ? metaItem("CFP poll", "week " + pw.cfp, LINKS.poll)
    : pw.ap ? metaItem("AP poll", "week " + pw.ap, LINKS.poll) : "";
  const carried = STATE.carried_games
    ? `Published ${ago(STATE.ratings_updated)}, then carried forward through `
      + `${STATE.carried_games} game${STATE.carried_games > 1 ? "s" : ""} `
      + `finished since.`
    : "";
  const rating = STATE.rating_label === "FPI"
    ? metaItem("FPI", ago(STATE.ratings_updated) || "-", LINKS.fpi, carried)
    : metaItem(STATE.rating_label, ago(STATE.ratings_updated) || "-", null, carried);
  // How many seasons stand behind every number, and when they were run.
  const when = STATE.loaded_at ? ago(new Date(1000 * STATE.loaded_at).toISOString()) : "";
  const count = STATE.n_sims ? `<b class="simcount">${esc(simsWord(STATE.n_sims))}</b> seasons simulated` : "Simulated";
  const sims = when ? `<span>${count} <b>${esc(when)}</b></span>` : "";
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

const plainName = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

/** Two teams, in a fixed order, as one string. */
function gameKey(g) {
  return [plainName(g.home ?? g.home_name), plainName(g.away ?? g.away_name)].sort().join("|");
}

function liveState(ev) {
  const c = ev.competitions && ev.competitions[0];
  if (!c) return null;
  const st = c.status && c.status.type;
  const side = {};
  for (const x of c.competitors || []) side[x.homeAway] = x;
  const home = side.home, away = side.away;
  if (!home || !away || !st) return null;
  const one = (x) => ({
    id: String(x.team.id || ""),
    abbr: x.team.abbreviation || "",
    score: Number(x.score),
    lines: (x.linescores || []).map(l => Number(l.value)),
  });
  const sit = c.situation || {};
  const tv = c.broadcast || ((c.broadcasts || [])[0] || {}).names?.[0] || "";
  return {
    state: st.state,                       // pre, in or post
    status: st.name || "",                 // STATUS_IN_PROGRESS, STATUS_HALFTIME...
    detail: st.shortDetail || st.detail,   // ESPN's own wording, for the odd cases
    period: Number(c.status.period) || 0,
    clock: c.status.displayClock || "",
    hp: Number(home.score), ap: Number(away.score),
    home: (home.team.location || ""), away: (away.team.location || ""),
    h: one(home), a: one(away),
    poss: sit.possession ? String(sit.possession) : "",
    down: sit.downDistanceText || sit.shortDownDistanceText || "",
    redzone: !!sit.isRedZone,
    last: (sit.lastPlay && sit.lastPlay.text) || "",
    tv,
  };
}

/** The scoreboard's entry for a game, turned to match our home and away.
 * ESPN and the build can disagree about which side is home at a neutral site. */
function liveOf(g) {
  const l = LIVE.get(gameKey(g));
  if (!l) return null;
  if (plainName(g.home) === plainName(l.home)) return l;
  return { ...l, hp: l.ap, ap: l.hp, h: l.a, a: l.h, home: l.away, away: l.home };
}

/** The clock as the broadcast shows it: the quarter and the time left in it,
 * built from ESPN's own period and clock rather than its summary line. */
function liveClock(l) {
  const q = (p) => (p <= 4 ? `Q${p}` : (p === 5 ? "OT" : `${p - 4}OT`));
  if (l.status === "STATUS_HALFTIME") return "Halftime";
  if (l.status === "STATUS_END_PERIOD") return `End of ${q(l.period)}`;
  if (l.status === "STATUS_IN_PROGRESS" && l.period) {
    return l.period > 4 ? q(l.period) : `${q(l.period)} ${l.clock}`;
  }
  return l.detail || "Live";
}

const isLive = (l) => !!l && l.state === "in";

// Live win chance
//
// How likely each side is to win from where the game stands: the margin now,
// plus the share of the pregame edge still to be played, against the scatter
// left in the game. That scatter shrinks with the square root of the time
// left, the random-walk model of a football score (Stern, 1994). Sigma is the
// one the build measured (python -m cfbroot.spread), so a game at kickoff
// gets back exactly the build's own chance. Possession and field position
// are not in it.

/** The normal distribution's CDF (Abramowitz and Stegun 7.1.26). */
function normCdf(x) {
  const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
  const tail = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t
    - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x / 2);
  return x >= 0 ? 1 - tail / 2 : tail / 2;
}

/** Its inverse (Acklam's rational approximation). */
function normInv(p) {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687,
             138.3577518672690, -30.66479806614716, 2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866,
             66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838,
             -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996,
             3.754408661907416];
  const tail = (q) => (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
    / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  if (p < 0.02425) return tail(Math.sqrt(-2 * Math.log(p)));
  if (p > 0.97575) return -tail(Math.sqrt(-2 * Math.log(1 - p)));
  const q = p - 0.5, r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/** The share of regulation still to play, 0 at the end. */
function timeLeft(l) {
  if (l.state === "post") return 0;
  if (l.status === "STATUS_HALFTIME") return 0.5;
  const p = l.period || 1;
  if (p > 4) return 0.02;            // overtime: a possession or two each
  const [m, s] = String(l.clock || "0:00").split(":").map(Number);
  const inQuarter = l.status === "STATUS_END_PERIOD" ? 0 : ((m || 0) * 60 + (s || 0)) / 900;
  return Math.max(0, Math.min(1, (4 - p + inQuarter) / 4));
}

/** The home side's chance of winning from here, or null without a sigma. */
function liveWin(g, l) {
  const sigma = STATE.params && STATE.params.sigma;
  if (!sigma || g.p_home_win == null) return null;
  const margin = l.hp - l.ap;
  const f = timeLeft(l);
  if (f <= 0) return margin > 0 ? 1 : margin < 0 ? 0 : 0.5;
  const edge = sigma * normInv(Math.min(Math.max(g.p_home_win, 1e-4), 1 - 1e-4));
  return normCdf((margin + edge * f) / (sigma * Math.sqrt(f)));
}

/** Every game the build left to play that has started since, once each. */
function startedGames() {
  const seen = new Set(), out = [];
  for (const [g, own] of RESULT.own_games.map(g => [g, true])
         .concat(RESULT.games.map(g => [g, false]))) {
    const k = gameKey(g);
    const l = liveOf(g);
    if (seen.has(k) || !l || l.state === "pre") continue;
    seen.add(k);
    out.push({ g, own, l });
  }
  return out;
}

/** Your odds with today's results. Only games that are over count: a game
 * still being played moves its own tile, not this number. Each one moves the
 * build's number from the pregame chance to the result, times what it is
 * worth to you. Exact for one game; for several it adds their effects, which
 * holds closely while their results do not lean on each other. */
function liveOdds() {
  const key = METRIC;
  const base = RESULT.headline[key].p;
  let p = base, n = 0;
  for (const { g, l } of startedGames()) {
    if (l.state !== "post") continue;
    const q = liveWin(g, l);
    if (q == null) continue;
    const s = g.swings[key];
    p += (q - g.p_home_win) * (s.p_if_home - s.p_if_away);
    n++;
  }
  return n ? { p: Math.min(1, Math.max(0, p)), base, n } : null;
}

// The teams whose score changed on the last poll, by plain name, so the
// scoreboard can mark that score once.
let SCORED = new Set();

async function refreshLive() {
  try {
    const resp = await fetch(SCOREBOARD,
      { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!resp.ok) return;
    const data = await resp.json();
    const before = new Map(LIVE);
    LIVE.clear();
    SCORED = new Set();
    for (const ev of data.events || []) {
      const s = liveState(ev);
      if (!s) continue;
      const k = gameKey(s);
      LIVE.set(k, s);
      const was = before.get(k);
      if (was && was.hp !== s.hp) SCORED.add(plainName(s.home));
      if (was && was.ap !== s.ap) SCORED.add(plainName(s.away));
    }
  } catch { /* offline, or ESPN is having a day; the page still works */ }
}

/** Every fifteen seconds while a game is on, so the clock on screen is never
 * far behind the one on TV; every few minutes otherwise. ESPN holds the
 * scoreboard for two seconds, so asking more often than this buys nothing
 * a reader would see. Paused while the tab is hidden. */
const LIVE_EVERY = 15000;
const IDLE_EVERY = 180000;

function watchLive() {
  let timer = 0;
  const tick = async () => {
    clearTimeout(timer);
    await refreshLive();
    if (RESULT) renderLive();
    SCORED = new Set();
    const busy = [...LIVE.values()].some(isLive);
    timer = setTimeout(() => { if (!document.hidden) tick(); },
                       busy ? LIVE_EVERY : IDLE_EVERY);
  };
  tick();
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
  // On a phone the list starts at the name, not the screen's edge, and can
  // run off the right; it moves left by however far it would.
  ul.style.left = "";
  const over = ul.getBoundingClientRect().right - (document.documentElement.clientWidth - 8);
  if (over > 0) ul.style.left = `${-over}px`;
  $("band").classList.add("menuopen");
  $("team").setAttribute("aria-expanded", "true");
  showActive();
}

function closeMenu() {
  $("teammenu").hidden = true;
  $("band").classList.remove("menuopen");
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

/** The picker is as wide as the name in it, so its arrow sits beside it. */
function fitTeamInput() {
  const input = $("team");
  const probe = document.createElement("span");
  const cs = getComputedStyle(input);
  // Property by property: the font shorthand cannot carry a stretch of 70%.
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
  for (const k of ["fontFamily", "fontSize", "fontWeight", "fontStretch",
                   "letterSpacing"]) probe.style[k] = cs[k];
  probe.textContent = input.value || input.placeholder;
  document.body.appendChild(probe);
  input.style.width = Math.ceil(probe.getBoundingClientRect().width + 40) + "px";
  probe.remove();
}

function chooseTeam(t) {
  $("team").value = t.name;
  fitTeamInput();
  closeMenu();
  $("team").blur();
  loadTeam();
}

function fillTeams() {
  const input = $("team");
  const ul = $("teammenu");
  input.addEventListener("focus", () => { input.select(); openMenu(); });
  input.addEventListener("click", () => { if (ul.hidden) openMenu(); });
  input.addEventListener("input", () => { fitTeamInput(); openMenu(); });
  input.addEventListener("blur", () => {
    closeMenu();
    // Leaving half-typed text behind would look like a different team.
    if (!teamNamed(input.value) && WANTED) input.value = WANTED;
    fitTeamInput();
  });
  document.fonts.ready.then(fitTeamInput);
  fitTeamInput();
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

/** "All remaining", or "All weeks" once played games are in. */
function nameAllOption() {
  $("week").options[0].textContent = $("showplayed").checked ? "All weeks" : "All remaining";
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

// The band: the team's name, its colours, and its record

/** "#9e1b32" as [r, g, b]. */
function rgbOf(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance([r, g, b]) {
  const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** The hue and strength the page takes from a team's colour, in OKLCH.
 * A tenth of the colour's own chroma, capped, and less again for golds and
 * yellows, which otherwise read as cream or olive. Black and grey teams
 * come out all but neutral. The build does the same sum in export.py. */
function teamTint([r, g, b]) {
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const l = Math.cbrt(0.4122214708 * R + 0.5363325363 * G + 0.0514459929 * B);
  const m = Math.cbrt(0.2119034982 * R + 0.6806995451 * G + 0.1073969566 * B);
  const s = Math.cbrt(0.0883024619 * R + 0.2817188376 * G + 0.6299787005 * B);
  const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
  const Bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
  const h = ((Math.atan2(Bb, A) * 180 / Math.PI) + 360) % 360;
  let c = Math.min(Math.hypot(A, Bb) * 0.11, 0.013);
  if (h >= 65 && h <= 115) c *= 0.55;
  return [h, c];
}

/** The band in the team's colour, with whichever text reads on it. */
function setBand(idx) {
  const band = $("band");
  const t = idx == null ? null : team(idx);
  const rgb = t && rgbOf(t.color);
  if (rgb) {
    const L = luminance(rgb);
    const light = (1.05 / (L + 0.05)) < ((L + 0.05) / 0.0587);   // dark text wins
    band.style.setProperty("--team", `rgb(${rgb.join(",")})`);
    band.style.setProperty("--team-fg", light ? "#0f1712" : "#ffffff");
    // On a yellow or near-white band the marker has to be dark to show.
    band.style.setProperty("--team-mark", light ? "#0f1712" : "var(--mark)");
    band.style.setProperty("--team-mark-ink", light ? "#ffffff" : "#0f1712");
    band.style.setProperty("--photo-blend", light ? "screen" : "multiply");
    const [h, c] = teamTint(rgb);
    const root = document.documentElement;
    root.style.setProperty("--team-h", h.toFixed(1));
    root.style.setProperty("--team-c", c.toFixed(4));
    root.classList.add("tinted");
  } else {
    document.documentElement.classList.remove("tinted");
    for (const v of ["--team", "--team-fg", "--team-mark", "--team-mark-ink", "--photo-blend"]) {
      band.style.removeProperty(v);
    }
  }
  // Each team keeps the same photo, so its page looks the same every visit.
  band.dataset.photo = String(idx == null ? 3 : idx % 4 + 1);
  band.classList.toggle("home", idx == null);
  if (idx == null) $("share").hidden = true;
  const plate = $("teamlogo");
  if (t && t.logo) {
    plate.innerHTML = `<img src="${esc(t.logo)}" alt="" width="48" height="48">`;
    plate.hidden = false;
  } else {
    plate.hidden = true;
  }
  if (t == null) {
    $("teamline").textContent = "Every remaining game, ranked by its effect on your "
      + "team's playoff odds.";
  }
}

/** Wins and losses so far, overall and in conference. Worked out once for
 * every team, since a slate asks for over a hundred of them at a time. */
let RECORDS = null, RECORDS_FROM = null;

function recordOf(idx) {
  if (RECORDS_FROM !== STATE.played) {
    RECORDS = new Map();
    RECORDS_FROM = STATE.played;
    for (const g of STATE.played || []) {
      for (const me of [g.home, g.away]) {
        if (!RECORDS.has(me)) RECORDS.set(me, { w: 0, l: 0, cw: 0, cl: 0 });
        const r = RECORDS.get(me);
        const won = (g.home === me) === (g.home_points > g.away_points);
        won ? r.w++ : r.l++;
        if (g.conference && !g.title_game) won ? r.cw++ : r.cl++;
      }
    }
  }
  return RECORDS.get(idx) || { w: 0, l: 0, cw: 0, cl: 0 };
}

/** A team's record beside its name. Not for FCS sides: the build only holds
 * their games against FBS teams, so the count would be wrong. */
function recTag(idx) {
  if (!team(idx).fbs) return "";
  const r = recordOf(idx);
  return `<span class="rec">${r.w}-${r.l}</span>`;
}

function renderTeamLine(idx) {
  const t = team(idx);
  const r = recordOf(idx);
  const bits = [];
  const inConf = t.conference && !/independent/i.test(t.conference);
  // The conference names its own page, the race the team is in.
  const conf = inConf ? ` (${r.cw}-${r.cl} <a class="conflink" href="${BASE}conference/${slugOf(t.conference)}/">${esc(t.conference)}</a>)` : "";
  bits.push(`<span><b>${r.w}-${r.l}</b>${conf}</span>`);
  const cs = STATE.conf_status || {};
  if (inConf && (cs.clinched || []).includes(idx)) {
    bits.push(`<span class="clchip in">Clinched ${esc(t.conference)} title game</span>`);
  } else if (inConf && (cs.out || []).includes(idx)) {
    bits.push(`<span class="clchip out">Out of the ${esc(t.conference)} title race</span>`);
  }
  const pr = pollRank(idx);
  if (pr.best) bits.push(`<span>${pr.kind} No. ${pr.best}</span>`);
  if (RESULT && RESULT.team === t.name) {
    bits.push(`<span><b>${num(RESULT.expected_wins)}</b> projected wins</span>`);
  }
  $("teamline").innerHTML = bits.join(" &nbsp; ");
  if (RESULT && RESULT.team === t.name) renderSeasonRow();
  else $("season").innerHTML = "";
}

// The road: every measure, in the order a season reaches them

const PATH = [
  ["six_wins", "6 wins", "6 wins"],
  ["finish_ranked", "Finish ranked", "Ranked"],
  ["make_conf_title_game", "{c} title game", "CCG"],
  ["win_conference", "Win {c}", "Conf"],
  ["make_playoff", "Make the playoff", "Playoff"],
  ["top4_seed", "Top-4 seed", "Bye"],
  ["win_national_title", "Win national title", "Title"],
];

/** A measure's short name, as the bars say it on a phone. */
function shortLabel(key) {
  const step = PATH.find(([k]) => k === key);
  return step ? step[2] : metricLabel(key);
}

function pathSteps() {
  const conf = team(teamIdx()).conference || "";
  const indie = !conf || /independent/i.test(conf);
  return PATH.filter(([k]) => !(indie && (k === "win_conference"
                                           || k === "make_conf_title_game")))
    .filter(([k]) => RESULT.headline[k]);
}

function teamIdx() {
  const t = RESULT && STATE.teams.find(x => x.name === RESULT.team);
  return t ? t.idx : null;
}

/** The measures as one bar chart on a shared baseline, stepping down from
 * finishing ranked to winning it all. Each bar is also the switch for the
 * list. */
function renderPath(grow) {
  const conf = (team(teamIdx()).conference || "conference")
    .replace(/ Conference$/, "");
  const steps = pathSteps();
  if (!steps.some(([k]) => k === METRIC)) METRIC = "make_playoff";
  const el = $("path");
  el.classList.toggle("short", steps.length < PATH.length);
  el.innerHTML = steps.map(([k, label, short]) => {
    const said = pctOf(RESULT.headline[k].p, k);
    const p = (100 * RESULT.headline[k].p).toFixed(2);
    const full = label.replace("{c}", conf);
    return `<button type="button" class="step" data-key="${k}"
        aria-pressed="${k === METRIC}" title="Show who to root for to ${esc(GOAL[k].replace("{c}", conf))}: ${said}">
      <span class="col" data-p="${p}" style="--p:${grow ? 0 : p}"><i></i>
        <span class="v">${esc(said).replace("%", "<small>%</small>")}</span></span>
      <span class="k"><span class="full">${esc(full)}</span><span class="short">${esc(short)}</span></span>
    </button>`;
  }).join("");
  if (grow) {
    // The one moment of motion: a new team's season rises into place. A
    // timer rather than the next frame, which a tab not being painted never
    // reaches.
    setTimeout(() => {
      el.querySelectorAll(".col").forEach(c => c.style.setProperty("--p", c.dataset.p));
    }, 30);
  }
}

function setMetric(key) {
  if (key === METRIC) return;
  METRIC = key;
  try { localStorage.setItem("cfbroot.metric", key); } catch { /* private mode */ }
  render();
}

/** On a wide screen the toolbar sticks; once the band is gone it says whose
 * odds the list is moving, and from where. */
function watchToolbar() {
  if (!("IntersectionObserver" in window)) return;
  // The mark sits just above the toolbar, so once it has scrolled off the
  // top the toolbar is the one holding the top of the screen.
  new IntersectionObserver(([e]) => {
    $("toolbar").classList.toggle("stuck", !e.isIntersecting && e.boundingClientRect.top < 0);
  }).observe($("tbmark"));
}

function renderToolbarContext() {
  const idx = teamIdx();
  if (idx == null) { $("tbctx").innerHTML = ""; return; }
  $("tbctx").innerHTML = `${logo(idx, 20)}${esc(RESULT.team)}
    <span class="muted">${esc(metricLabel(METRIC))}</span> ${pctOf(RESULT.headline[METRIC].p, METRIC)}`;
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
  setBand(t.idx);
  renderTeamLine(t.idx);
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
      banner("The site has been updated since this page was opened. Reload for "
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
    render(true);
    // Each team has its own address on the hosted site, so a link to what
    // is on screen goes to the same place. The local app keeps its address
    // but still takes the team's written section.
    if (STATIC && t.slug && !location.pathname.endsWith(`/team/${t.slug}/`)) {
      history.pushState({ team: t.name }, "", `${BASE}team/${t.slug}/`);
      loadWriteup(t);
    } else if (!STATIC && t.slug) {
      loadWriteup(t);
    }
    return;
  }
}

/** The written section under the guide is the team page's own, so a team
 * picked in place takes its page's text and title with it. */
async function loadWriteup(t) {
  let doc;
  try {
    const resp = await fetch(`${BASE}team/${t.slug}/`);
    if (!resp.ok) throw new Error(resp.statusText);
    doc = new DOMParser().parseFromString(await resp.text(), "text/html");
  } catch {
    if (WANTED === t.name) $("writeup").innerHTML = "";   // none beats another team's
    return;
  }
  if (WANTED !== t.name) return;
  const w = doc.getElementById("writeup");
  $("writeup").innerHTML = w ? w.innerHTML : "";
  if (doc.title) document.title = doc.title;
}

function showProgress(job) {
  $("progress").hidden = !job;
  if (!job) return;
  $("empty").hidden = true;
  const frac = job.total ? job.done / job.total : 0;
  $("progressfill").style.width = (100 * frac).toFixed(1) + "%";
  $("progresstext").textContent =
    `Simulating the season: ${job.done.toLocaleString()} / `
    + `${job.total.toLocaleString()}, ${num(job.elapsed, 0)}s`;
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

/** Results from weeks the replay run did not cover. Only the latest week's
 * games get a value put on them, so these carry the score and nothing else. */
function pastResults(ownOnly) {
  const valued = (RESULT.replay || {}).week;
  const me = teamIdx();
  return (STATE.played || [])
    .filter(g => g.week !== valued)
    .filter(g => !ownOnly || g.home === me || g.away === me)
    .filter(g => ownOnly || (g.home !== me && g.away !== me))
    .map(g => ({
      home: team(g.home).name, away: team(g.away).name,
      home_idx: g.home, away_idx: g.away, week: g.week, neutral: g.neutral,
      home_points: g.home_points, away_points: g.away_points,
      title_game: g.title_game, unvalued: true,
    }));
}

/** Every game on the chosen slate, before the filters narrow it down. */
function slateGames() {
  const wk = selectedWeek();
  const played = $("showplayed").checked;
  const replay = (RESULT.replay || {}).games || [];
  const games = RESULT.games.concat(played ? replay.concat(pastResults(false)) : [])
    .filter(g => wk === null || g.week === wk);
  return played ? games : games.filter(g => !finalOf(g));
}

/** How big a game is, for ordering results that have no value on them. */
function stature(g) { return ratingOf(g.home_idx) + ratingOf(g.away_idx); }

function ratingOf(idx) {
  const r = team(idx).rating;
  return r == null ? -40 : r;
}

function visibleGames() {
  const key = METRIC;
  let games = slateGames();
  if (SHOW === "sig") {
    // A bare result is not a call on anything, so the filter leaves it be.
    games = games.filter(g => g.unvalued || sigOf(g.swings[key]));
  }
  const q = ($("rootsearch").value || "").trim().toLowerCase();
  if (q) {
    games = games.filter(g => (g.home + " " + g.away).toLowerCase().includes(q)
      || [g.home_idx, g.away_idx].some(
        i => (team(i).conference || "").toLowerCase().includes(q)));
  }
  const byTime = SORT === "time";
  return games.sort((a, b) => {
    // Results without a value go after the rest, latest and biggest first.
    if (a.unvalued || b.unvalued) {
      if (a.unvalued !== b.unvalued) return byTime ? (a.unvalued ? -1 : 1) : (a.unvalued ? 1 : -1);
      return (byTime ? a.week - b.week : b.week - a.week) || (stature(b) - stature(a));
    }
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
    ? RESULT.own_games.concat(replay, pastResults(true)) : RESULT.own_games;
  return games.filter(g => wk === null || g.week === wk)
    .filter(g => $("showplayed").checked || !finalOf(g))
    .sort((a, b) => a.week - b.week);
}

// Rendering

function render(newTeam) {
  if (!RESULT) return;
  document.body.classList.add("hasteam");
  $("share").hidden = false;
  $("empty").hidden = true;
  $("outlook").hidden = false;
  $("results").hidden = false;
  const idx = teamIdx();
  if (newTeam) fitMetric();
  renderTeamLine(idx);
  renderPath(newTeam);
  renderToolbarContext();
  // Missing out is already on the road above, and as a bar it dwarfs the
  // seeds, so the chart is seeds only and the miss is a number. A team with
  // no real chance of a seed has no chart.
  const seeds = RESULT.seed_distribution;
  const inField = seeds.slice(1).reduce((a, b) => a + b, 0);
  $("seednote").textContent = `% of seasons, out of the field in ${pct(seeds[0])}`;
  $("seedbox").hidden = inField < 0.002;
  if (inField >= 0.002) $("seeddist").innerHTML = seedsHTML(seeds);
  renderScoreboard();
  renderNeeds();
  renderOwnGames();
  renderRootList();
  renderLeague();
  $("weeklabel").textContent = selectedWeek() === null
    ? "all remaining" : "week " + selectedWeek();
  $("footmeta").textContent =
    `${STATE.year} week ${STATE.current_week}, `
    + `${RESULT.n_sims.toLocaleString()} simulated seasons`;
}

/** When a game starts, in the reader's own zone.
 *
 * A game whose time is not set yet is dated midnight Eastern, which is the
 * evening before in half the country, so those say the day and nothing more.
 */
function whenText(g, zone = true) {
  if (!g.start_date) return "";
  const t = new Date(g.start_date);
  if (isNaN(t)) return "";
  if (g.time_set === false) {
    const day = t.toLocaleDateString([], { weekday: "short",
                                           timeZone: "America/New_York" });
    return `${day}, time TBA`;
  }
  return t.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit",
                                ...(zone ? { timeZoneName: "short" } : {}) });
}

/** Kickoffs the build wrote in Eastern time, said again in the reader's
 * own zone. The page carries the exact time on each one. */
function localizeKickoffs() {
  for (const el of document.querySelectorAll("time.kick[datetime]")) {
    const g = { start_date: el.getAttribute("datetime"),
                time_set: el.dataset.tba ? false : true };
    const when = whenText(g, true);
    if (when) el.textContent = el.dataset.tv ? `${when}, ${el.dataset.tv}` : when;
  }
}

/** The reader's time zone as a short name, said once over a list of times. */
const ZONE = (() => {
  try {
    return new Intl.DateTimeFormat([], { timeZoneName: "short" })
      .formatToParts(new Date()).find(p => p.type === "timeZoneName").value;
  } catch { return ""; }
})();

/** The final score of a game that is over, from the build or the scoreboard. */
function finalOf(g) {
  if (g.home_points != null && g.away_points != null) {
    return { home: g.home_points, away: g.away_points, fresh: false };
  }
  const live = liveOf(g);
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

/** Kickoff and network, what the game is doing now, or that it is over.
 * ``opp`` is the "over Utah" a phone shows here, where it has room. */
function whenHTML(g, done, opp = "") {
  const tv = g.broadcast ? esc(g.broadcast) : "";
  const live = liveOf(g);
  const o = opp ? `<span class="opp">${opp}</span>` : "";
  if (done) return `<div class="when">${o}<b>Final</b></div>`;
  if (isLive(live)) {
    return `<div class="when">${o}<b class="live">${esc(liveClock(live))}</b>`
      + `<span class="tv">${tv}</span></div>`;
  }
  return `<div class="when">${o}<b>${esc(whenText(g, false))}</b><span class="tv">${tv}</span></div>`;
}

function logo(idx, size) {
  const t = team(idx);
  if (!t.logo) return `<span class="logo ph" style="width:${size}px;height:${size}px"></span>`;
  return `<img class="logo" src="${esc(t.logo)}" alt="" loading="lazy"
            width="${size}" height="${size}">`;
}

// The slate
//
// Every game is one row, and its first line is the answer: the side to root
// for, with the other side's numbers under it. With 2.5 million seasons most
// games clear the bar, so a slate runs to dozens of rows; the whole list
// folds away rather than each game.

/** A list of games. ``slots`` groups them under their kickoff time.
 *
 * Each side's line ends in a bar from a centre line shared by the whole list,
 * as long as the change in your odds if that side wins: right for better,
 * left for worse. A game is the pair of bars, so the list reads as one chart,
 * the biggest swings at the top. */
function gameRowsHTML(games, slots) {
  const key = METRIC;
  // A list of finished games has nothing to root for: it is results.
  const over = games.length && games.every(g => finalOf(g));
  // At least a fifth of a point, so a slate of tiny changes draws tiny bars.
  const maxAbs = Math.max(0.002, ...games.filter(g => !g.unvalued).map(g => {
    const s = g.swings[key], base = baseFor(g, key);
    return Math.max(Math.abs(s.p_if_home - base), Math.abs(s.p_if_away - base));
  }));
  const head = `<div class="slatehead" aria-hidden="true">
      <span class="h-when">${over ? "Played" : "Kickoff" + (ZONE ? ", " + esc(ZONE) : "")}</span>
      <span>${over ? "Winner" : "Root for"}</span>
      <span class="num" title="${over ? "Final score" : "Win probability"}">${over ? "Score" : "Win"}</span>
      ${games.every(g => g.unvalued) ? `<span class="h-tor"></span>` : `
      <span class="h-tor" title="Change in ${esc(metricLabel(key))}, in percentage points"><span>Worse</span>
        <b>Your odds if they ${over ? "won" : "win"}</b><span>Better</span></span>`}
    </div>`;
  let body = "", slot = null;
  for (const g of games) {
    if (slots) {
      const label = g.unvalued ? `Week ${g.week}`
        : finalOf(g) ? "Final" : (whenText(g) || "Time not set");
      if (label !== slot) {
        slot = label;
        body += `<div class="slot">${esc(label)}</div>`;
      }
    }
    body += g.unvalued ? resultHTML(g) : gameHTML(g, key, maxAbs);
  }
  return `<div class="slate${slots ? " byslot" : ""}">${head}${body}</div>`;
}

/** The other side, as a name that hovers and clicks like any other. */
function vsName(idx, name) {
  return `<span class="vs" data-team="${idx}">${pollTag(idx)}${esc(name)}</span>`;
}

/** A result from a week nobody put a value on: the winner, and the score. */
function resultHTML(g) {
  const homeWon = g.home_points > g.away_points;
  const w = homeWon ? g.home_idx : g.away_idx;
  const l = homeWon ? g.away_idx : g.home_idx;
  const score = homeWon ? `${g.home_points}-${g.away_points}`
                        : `${g.away_points}-${g.home_points}`;
  const at = homeWon ? (g.neutral ? "vs" : "@") : "";
  const opp = `beat ${vsName(l, homeWon ? g.away : g.home)}`;
  const label = g.title_game ? "Title game" : `Week ${g.week}`;
  return `<article class="game played unvalued">
      <div class="when"><span class="opp">${opp}</span><b>${label}</b></div>
      <div class="side root won">
        <span class="nm"><i class="at">${at}</i><span class="who" data-team="${w}">${logo(w, 18)}${pollTag(w)}<b>${esc(homeWon ? g.home : g.away)}</b>${recTag(w)}</span><span class="over">${opp}</span></span>
        <span class="num pr score">${score}</span>
      </div>
    </article>`;
}

/** One game. Before it, the first line is the side to root for; after it,
 * the winner. */
function gameHTML(g, key, maxAbs) {
  const s = g.swings[key];
  const done = finalOf(g);
  const base = baseFor(g, key);
  const conf = confidenceOf(s);
  const pHome = g.p_home_win;
  const homeWon = done ? done.home > done.away : null;
  const firstHome = done ? homeWon : s.home;
  const live = done ? null : liveOf(g);

  const side = (isHome, first) => {
    const idx = isHome ? g.home_idx : g.away_idx;
    const p = isHome ? s.p_if_home : s.p_if_away;
    const d = p - base;
    const cls = !first ? "side other" + (done ? " lost" : "")
      : done ? "side root won" : "side root";
    // The two lines read as one fixture, "Kansas @ Utah" or "Utah vs
    // Kansas", so only the second line says where.
    const at = first ? "" : (isHome && !g.neutral ? "@" : "vs");
    const otherIdx = isHome ? g.away_idx : g.home_idx;
    const over = first ? `<span class="over">${done ? "beat" : "over"} `
      + `${vsName(otherIdx, isHome ? g.away : g.home)}</span>` : "";
    const lead = done
      ? `<span class="num pr" title="Final score">${isHome ? done.home : done.away}</span>`
      : isLive(live)
        ? `<span class="num pr now" title="Score now, and the chance to win from here"><b>${isHome ? live.hp : live.ap}</b>${
            liveWin(g, live) == null ? "" : `<small class="lwp">${pct(isHome ? liveWin(g, live) : 1 - liveWin(g, live), 0)}</small>`}</span>`
        : `<span class="num pr">${pct(isHome ? pHome : 1 - pHome, 0)}</span>`;
    const name = isHome ? g.home : g.away;
    const f = Math.min(1, Math.abs(d) / maxAbs).toFixed(4);
    const tip = done ? `Effect of the result on your odds: ${pctOf(p, key)}, ${signed(d)}`
      : `${metricLabel(key)} if ${name} wins: ${pctOf(p, key)}, ${signed(d)}. ${CONF_TITLE[conf]}`;
    return `<div class="${cls}">
      <span class="nm"><i class="at">${at}</i><span class="who" data-team="${idx}">${logo(idx, 18)}${pollTag(idx)}<b>${esc(name)}</b>${recTag(idx)}</span>${over}</span>
      ${lead}
      <span class="tor ${dirOf(d)}" style="--f:${f}" title="${esc(tip)}"><i></i><b>${signed(d)}</b></span>
    </div>`;
  };

  const opp = `${done ? "beat" : "over"} `
    + vsName(firstHome ? g.away_idx : g.home_idx, firstHome ? g.away : g.home);
  return `<article class="game open ${done ? "played" : conf}">
      ${whenHTML(g, done, opp)}${side(firstHome, true)}${side(!firstHome, false)}</article>`;
}

/** The team's own games as rows of the slate. What it needs covers the games
 * to come, so these are for Include played: the results and what each was
 * worth. */
function renderOwnGames() {
  const games = $("showplayed").checked ? ownGames() : [];
  const panel = $("owngames");
  if (!games.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("ownhead").textContent = `${RESULT.team} game${games.length > 1 ? "s" : ""}`;
  $("owntable").innerHTML = gameRowsHTML(games, false);
}

// The rest of the season
//
// The games a team has left as a dumbbell chart: the chosen measure after a
// loss and after a win on one scale, today's number a line down it. The game
// that decides the most spreads the odds furthest: the gap between its two
// results weighted by how uncertain it is. For a contender that is a hard
// game it has to win, not the one whose upset would hurt most, nor a coin
// flip that barely moves its odds. Beside
// them, the measure at every final record the team reaches, with how often
// it ends there, and where it lands in the bracket.
//
// A team opens on the race it is really in, so one out of the playoff picture
// sees its conference race or its chances of six wins rather than a column of
// zeros.

const ODDS_NAME = {
  six_wins: "Six-win odds",
  make_playoff: "Playoff odds",
  win_conference: "{c} title odds",
  make_conf_title_game: "Title game odds",
  top4_seed: "Bye odds",
  win_national_title: "National title odds",
  finish_ranked: "Ranked odds",
};
const GOAL = {
  six_wins: "win six games",
  make_playoff: "make the playoff",
  win_conference: "win the {c}",
  make_conf_title_game: "reach the {c} title game",
  top4_seed: "earn a first-round bye",
  win_national_title: "win the national title",
  finish_ranked: "finish ranked",
};
// A final record needs this many simulated seasons before the chart gives
// its odds, and a game's result this many before its dumbbell shows it.
const RECORD_MIN = 100;
const RESULT_MIN = 200;

function confOf(idx) {
  const c = team(idx).conference || "";
  return /independent/i.test(c) ? "" : c;
}

/** A share of seasons, to the precision it needs. */
function share(x) {
  return x >= 0.1 || x === 0 ? pct(x, 0) : pct(x, 1);
}

/** The top of a scale that fits ``v`` with a little room. */
function niceTop(v) {
  for (const t of [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.4, 0.5, 0.6, 0.8, 1]) {
    if (t >= v * 1.04) return t;
  }
  return 1;
}

/** Still to be decided, one way or the other. */
function inPlay(key) {
  const h = RESULT.headline[key];
  return !!h && h.p >= 0.005 && h.p <= 0.995;
}

/** The measure a new team opens on: the reader's own choice while it is still
 * in play for this team, otherwise the race the team is in. Only a reader's
 * click is saved, so the next team starts from their choice again. */
function fitMetric() {
  const steps = new Set(pathSteps().map(([k]) => k));
  let saved = null;
  try { saved = localStorage.getItem("cfbroot.metric"); } catch { /* private mode */ }
  METRIC = saved && steps.has(saved) ? saved : "make_playoff";
  if (inPlay(METRIC)) return;
  const sure = RESULT.headline.make_playoff && RESULT.headline.make_playoff.p > 0.995;
  const races = sure ? ["top4_seed", "win_national_title"]
    : ["make_playoff", "win_conference", "make_conf_title_game", "finish_ranked", "six_wins"];
  METRIC = races.find(k => steps.has(k) && inPlay(k)) || METRIC;
}

function needRows(key) {
  const me = teamIdx();
  const n = RESULT.n_sims || 0;
  return RESULT.own_games.map(g => {
    const home = g.home_idx === me;
    const s = g.swings[key] || {};
    const chance = home ? g.p_home_win : 1 - g.p_home_win;
    const win = home ? s.p_if_home : s.p_if_away;
    const lose = home ? s.p_if_away : s.p_if_home;
    // A result that came up in too few seasons to put a number on is left
    // off; one that came up in a few hundred is shown, however unlikely.
    const rare = !Number.isFinite(lose) || n * (1 - chance) < RESULT_MIN ? "lose"
      : !Number.isFinite(win) || n * chance < RESULT_MIN ? "win" : null;
    const sure = s.reliable !== false && !rare;
    return {
      g, home, opp: home ? g.away_idx : g.home_idx, name: home ? g.away : g.home,
      where: home || g.neutral ? "vs" : "at", chance, win, lose, sure, rare,
      // How widely the game spreads the odds: the gap between its results
      // times the spread of a coin that lands a win with its chance. A toss-up
      // counts for more than a likely win, but not so much more that a game
      // that hardly matters outranks one that does.
      move: sure ? Math.sqrt(chance * (1 - chance)) * Math.abs(win - lose) : 0,
    };
  }).filter(r => Number.isFinite(r.win) || Number.isFinite(r.lose))
    .sort((a, b) => a.g.week - b.g.week || kickoffOrder(a.g) - kickoffOrder(b.g));
}

/** The title game, for a measure it can still move. */
function titleGame(key) {
  const tg = RESULT.title_game;
  const conf = confOf(teamIdx());
  if (!tg || !conf || ["win_conference", "make_conf_title_game", "six_wins"].includes(key)) return null;
  const reach = tg.p[0] + tg.p[1];
  const [won, lost, missed] = tg.odds[key] || [];
  if (reach < 0.005 || !Number.isFinite(won) || !Number.isFinite(lost)) return null;
  return { conf, reach, winp: tg.p[0] / reach, won, lost, missed };
}

/** A game's two results on the shared scale: a ring where a loss leaves the
 * odds and a dot where a win does, joined by a bar shaded from one to the
 * other, each labelled on its outer side. ``rare`` names a result too rare
 * to measure, which is left off; ``past`` the result that happened, which
 * stays while the other fades. */
function dumbbellHTML(lose, win, x, key, { rare = null, past = null, label = "" } = {}) {
  const xl = +x(Number.isFinite(lose) ? lose : 0), xw = +x(Number.isFinite(win) ? win : 0);
  // The lower of the two is labelled on its left, the higher on its right;
  // a dot on its own is labelled on whichever side has the room.
  const winRight = rare ? xw <= 50 : xw >= xl;
  const loseRight = rare ? xl <= 50 : !winRight;
  const gone = (kind) => (past && past !== kind ? " gone" : "");
  const end = (kind, xv, v, right) => `<span class="db-dot ${kind}${gone(kind)}" style="left:${xv}%"></span>`
    + `<span class="db-v ${kind}${right ? " r" : " l"}${gone(kind)}" style="left:${xv}%">${pctOf(v, key)}</span>`;
  const line = rare ? "" : `<i class="db-line${winRight ? "" : " flip"}" style="left:${Math.min(xl, xw)}%;width:${Math.abs(xw - xl).toFixed(2)}%"></i>`;
  return `<span class="nd-bar" role="img" aria-label="${esc(label)}"><span class="db">${line}`
    + (rare === "lose" ? "" : end("loss", xl, lose, loseRight))
    + (rare === "win" ? "" : end("win", xw, win, winRight))
    + `</span></span>`;
}

/** One row: when, who, the chance to win, and the measure after each result. */
function needRowHTML(r, x, key, big) {
  const g = r.g;
  const done = finalOf(g);
  const live = done ? null : liveOf(g);
  const wk = selectedWeek();
  let when = `<b>Wk ${g.week}</b><small>${esc(whenText(g, false).replace(/,? time TBA$/, ""))}</small>`;
  let chance = `<span class="num nd-p">${pct(r.chance, 0)}</span>`;
  let state = "", past = null;
  if (done) {
    const us = r.home ? done.home : done.away, them = r.home ? done.away : done.home;
    const won = us > them;
    when = `<b>Wk ${g.week}</b><small>Final</small>`;
    chance = `<span class="num nd-p res ${won ? "w" : "l"}">${won ? "W" : "L"} ${us}-${them}</span>`;
    state = won ? " won" : " lost";
    past = won ? "win" : "loss";
  } else if (isLive(live)) {
    const q = liveWin(g, live);
    const us = r.home ? live.hp : live.ap, them = r.home ? live.ap : live.hp;
    when = `<b>Wk ${g.week}</b><small class="live">${esc(liveClock(live))}, ${us}-${them}</small>`;
    if (q != null) chance = `<span class="num nd-p">${pct(r.home ? q : 1 - q, 0)}</span>`;
  }
  const tag = big ? `<span class="nd-tag">Biggest game</span>` : "";
  const head = `<div class="nd${big ? " big" : ""}${g.week === wk ? " on" : ""}${state}" data-week="${g.week}">
      <span class="nd-when">${when}</span>
      <span class="nd-opp"><i class="at">${r.where}</i><span class="who" data-team="${r.opp}">${logo(r.opp, 18)}${pollTag(r.opp)}<b>${esc(r.name)}</b>${recTag(r.opp)}</span>${tag}</span>
      ${chance}`;
  if (!x) return head + "</div>";
  const label = r.rare ? `Week ${g.week} ${r.where} ${r.name}: ${pctOf(r.rare === "lose" ? r.win : r.lose, key)} after a ${r.rare === "lose" ? "win" : "loss"}`
    : `Week ${g.week} ${r.where} ${r.name}: ${pctOf(r.win, key)} after a win, ${pctOf(r.lose, key)} after a loss`;
  return head + dumbbellHTML(r.lose, r.win, x, key, { rare: r.rare, past, label }) + "</div>";
}

/** The conference title game as one more row: the chance to win it once
 * there, and the measure after winning and after losing it. */
function titleRowHTML(tg, x, key) {
  if (!tg) return "";
  return `<div class="nd tg">
      <span class="nd-when"><b>Title</b><small>game</small></span>
      <span class="nd-opp"><b>${esc(tg.conf)} championship</b><small>Reached in ${share(tg.reach)}</small></span>
      <span class="num nd-p" title="Chance to win it, once there">${pct(tg.winp, 0)}</span>
      ${dumbbellHTML(tg.lost, tg.won, x, key, { label: `${tg.conf} title game: ${pctOf(tg.won, key)} after a win, ${pctOf(tg.lost, key)} after a loss` })}
    </div>`;
}

/** Every final record the team reaches in enough seasons, best first. Above
 * the line, the measure at that record; under it, how often the team ends
 * there. The unlikely ones are in, so even losing out has a number. A team
 * with no race left gets how often alone. */
function recordsHTML(key, flat) {
  const recs = (RESULT.by_record || [])
    .filter(r => r.n >= RECORD_MIN && Number.isFinite(r.odds[key]));
  if (recs.length < 2) return "";
  const most = Math.max(...recs.map(r => r.p));
  const name = ODDS_NAME[key].replace("{c}", confOf(teamIdx()) || "Conference");
  const label = (v) => esc(v).replace("%", "<small>%</small>");
  const cols = recs.map(r => {
    const o = r.odds[key];
    const said = `${r.wins}-${r.losses}: ` + (flat ? "" : `${name.toLowerCase()} ${pctOf(o, key)}, `)
      + `in ${share(r.p)} of seasons`;
    const often = `<span class="${flat ? "rc-up" : "rc-dn"}" style="--h:${(100 * r.p / most).toFixed(2)}"><i></i><span class="rc-v">${label(share(r.p))}</span></span>`;
    const up = flat ? "" : `<span class="rc-up" style="--h:${(100 * o).toFixed(2)}"><i></i><span class="rc-v">${label(pctOf(o, key))}</span></span>`;
    return `<div class="rc-c${r.p === most ? " likely" : ""}" title="${esc(said)}">
        ${flat ? often : up}<span class="rc-x">${r.wins}-${r.losses}</span>${flat ? "" : often}</div>`;
  }).join("");
  return `<figure class="recs${flat ? " flat" : ""}">
      <figcaption><b>${flat ? "Final record" : `${esc(name)} by final record`}</b><span>${flat ? "How often" : "Below, how often"}</span></figcaption>
      <div class="rc">${cols}</div>
    </figure>`;
}

/** The measure's name as a column head. */
function shortOdds(key) {
  return { six_wins: "6 wins", make_playoff: "Playoff", win_conference: "Title",
           make_conf_title_game: "Title game", top4_seed: "Bye", finish_ranked: "Ranked" }[key]
    || shortLabel(key);
}

/** Where the team lands when it makes the field: every seed on one scale,
 * grouped the way the bracket treats them. */
function seedsHTML(seeds) {
  const vals = seeds.slice(1, 13);
  const max = Math.max(...vals, 1e-9);
  const cols = vals.map((v, i) => `<span class="sd-c${v === max ? " hi" : ""}" title="Seed ${i + 1}: ${pct(v)}">
      <span class="sd-plot" style="--h:${(100 * v / max).toFixed(2)}"><i></i>${v >= 0.0005 ? `<span class="sd-v">${num(100 * v)}</span>` : ""}</span>
      <span class="sd-x">${i + 1}</span></span>`).join("");
  return `<div class="seeds">${cols}
      <span class="sd-g" style="grid-column:1 / 5">Bye</span>
      <span class="sd-g" style="grid-column:5 / 9">Home game</span>
      <span class="sd-g" style="grid-column:9 / 13">Road game</span></div>`;
}

function renderNeeds() {
  const key = METRIC;
  const rows = needRows(key);
  const tg = titleGame(key);
  const panel = $("needs");
  if (!rows.length && !tg) { panel.hidden = true; return; }
  panel.hidden = false;
  const conf = confOf(teamIdx()) || "Conference";
  const base = RESULT.headline[key].p;
  const known = (v) => (Number.isFinite(v) ? v : 0);
  // Nothing left to move: the games are listed with the chance to win them.
  const flat = Math.max(base, ...rows.map(r => Math.max(known(r.win), known(r.lose)))) < 0.005;
  // The key to the chart, in the heading's place for a note.
  $("needsnote").innerHTML = flat ? "The games left"
    : `<span>${esc(ODDS_NAME[key].replace("{c}", conf))}</span>
       <span class="lgk"><i class="db-dot loss"></i>after a loss</span>
       <span class="lgk"><i class="db-dot win"></i>after a win</span>
       <span class="lgk"><i class="lg-now"></i>now ${pctOf(base, key)}</span>`;

  let x = null, top = 1, bottom = 0;
  if (!flat) {
    const vals = rows.flatMap(r => [r.rare === "win" ? null : r.win, r.rare === "lose" ? null : r.lose])
      .filter(Number.isFinite).concat(base, tg ? [tg.won, tg.lost] : []);
    top = niceTop(Math.max(...vals));
    // A team whose every result sits high gets the stretch they sit in, in
    // four even steps, rather than a scale from zero with them in one corner.
    const low = Math.min(...vals) - 0.03;
    bottom = Math.max(0, ...[0.05, 0.1, 0.2, 0.25].map(s => top - 4 * s)
      .filter(b => b > 0.001 && b <= low));
    x = (v) => (100 * (Math.max(bottom, Math.min(v, top)) - bottom) / (top - bottom)).toFixed(2);
  }
  const most = Math.max(0, ...rows.map(r => r.move));
  // Each tick to as many places as it needs: 50%, 12.5%, 1.25%.
  const tick = (v) => `${+(Math.round(v * 1e4) / 100).toFixed(2)}%`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => `<span style="left:${100 * f}%">${tick(bottom + (top - bottom) * f)}</span>`).join("");
  $("needslist").innerHTML = (rows.length || tg) ? `<div class="nd-grid${flat ? " flat" : ""}" style="--now:${flat ? 0 : x(base)}%">
      <div class="nd-head" aria-hidden="true"><span>Week</span><span>Opponent</span>
        <span class="num" title="${esc(RESULT.team)}'s chance to win">Win</span>
        ${flat ? "" : `<span class="nd-scale"><span class="db">${ticks}</span></span>`}</div>
      ${rows.map(r => needRowHTML(r, x, key, !flat && rows.length > 1 && r.move > 0 && r.move === most)).join("")}
      ${flat ? "" : titleRowHTML(tg, x, key)}
    </div>` : "";
  $("needsrec").innerHTML = rows.length ? recordsHTML(key, flat) : "";
}

// Live now
//
// While games are on, a scoreboard for this team. The games that move its
// odds get a full tile: the score by quarter, the clock, who has the ball,
// each side's chance of winning from here and where your odds go if it does.
// Your team's game leads, then the rest by how much they matter. Every other
// game today is one line in a ticker underneath, so the board is complete
// without burying the few that count. The head carries the number a fan is
// watching for: your odds with the scores as they stand.

const periodName = (p) => (p <= 4 ? String(p) : (p === 5 ? "OT" : `${p - 4}OT`));

function liveGames() {
  const gap = (x) => Math.abs(x.g.swings[METRIC].delta || 0);
  const order = (a, b) => (b.own - a.own) || (gap(b) - gap(a));
  const started = startedGames();
  const on = started.filter(x => isLive(x.l)).sort(order);
  const big = on.filter(x => x.own || sigOf(x.g.swings[METRIC]));
  // Games over since the build: those that matter first, then the rest.
  const over = started.filter(x => !isLive(x.l))
    .sort((a, b) => (sigOf(b.g.swings[METRIC]) - sigOf(a.g.swings[METRIC])) || order(a, b));
  return { big, rest: on.filter(x => !big.includes(x)).concat(over), live: on.length };
}

function scoreTileHTML({ g, own, l }) {
  const key = METRIC;
  const s = g.swings[key];
  const base = baseFor(g, key);
  const q = liveWin(g, l);
  const periods = Math.max(4, l.period, l.h.lines.length, l.a.lines.length);
  const nums = Array.from({ length: periods }, (_, i) => i);
  const row = (isHome) => {
    const idx = isHome ? g.home_idx : g.away_idx;
    const side = isHome ? l.h : l.a;
    const score = isHome ? l.hp : l.ap;
    const ahead = score > (isHome ? l.ap : l.hp);
    const ball = l.poss && l.poss === side.id
      ? '<i class="ball" title="Has the ball"></i>' : "";
    // A quarter not reached yet stays blank rather than reading as a zero.
    const cells = nums.map(i => `<span>${i < side.lines.length
      && Number.isFinite(side.lines[i]) ? side.lines[i] : ""}</span>`).join("");
    // Your odds if this side wins. The side ahead is in bold: that is where
    // your odds go if the score holds.
    const p = isHome ? s.p_if_home : s.p_if_away;
    const win = q == null ? "" : pct(isHome ? q : 1 - q, 0);
    return `<div class="sb-row${isHome === s.home ? " root" : ""}${ahead ? " ahead" : ""}">
        <span class="sb-team"><span class="who" data-team="${idx}">${logo(idx, 20)}${pollTag(idx)}<b><span class="nm-full">${esc(isHome ? g.home : g.away)}</span><span class="nm-abbr">${esc(team(idx).abbr || (isHome ? g.home : g.away))}</span></b>${recTag(idx)}</span>${ball}</span>
        ${cells}<span class="sb-tot${SCORED.has(plainName(isHome ? g.home : g.away)) ? " new" : ""}">${score}</span>
        <span class="sb-win">${win}</span>
        <span class="sb-odds ${dirOf(p - base)}" title="${esc(metricLabel(key))} if ${esc(isHome ? g.home : g.away)} wins: ${pctOf(p, key)}, ${signed(p - base)}">${pctOf(p, key)}</span></div>`;
  };
  const sit = [l.down, l.redzone ? "red zone" : ""].filter(Boolean).join(", ");
  const tv = g.broadcast || l.tv || "";
  return `<article class="sbt ${confidenceOf(s)}${own ? " own" : ""}" data-game="${esc(gameKey(g))}">
      <div class="sb-top"><b class="live">${esc(liveClock(l))}</b>
        ${tv ? `<span class="muted">${esc(tv)}</span>` : ""}
        ${own ? '<span class="sb-tag">Your game</span>' : ""}
        <button type="button" class="sb-fold" aria-expanded="true"
          aria-label="Fold ${esc(g.away)} at ${esc(g.home)} to one line"></button></div>
      <div class="sb-grid" style="--n:${periods}">
        <div class="sb-row sb-head" aria-hidden="true"><span></span>${nums.map(i =>
          `<span>${periodName(i + 1)}</span>`).join("")}<span>T</span>
          <span class="sb-win" title="Chance to win from here">Win</span>
          <span class="sb-odds" title="${esc(metricLabel(key))}, if that side wins">${esc(shortLabel(key))}</span></div>
        ${row(false)}${row(true)}
      </div>
      ${sit ? `<p class="sb-sit">${esc(sit)}</p>` : ""}
      ${l.last ? `<p class="sb-last" title="${esc(l.last)}">${esc(l.last)}</p>` : ""}
    </article>`;
}

/** The rest of today's games, one line each: the clock and the two scores. */
function tickerHTML(list) {
  return `<div class="tick">${list.map(({ g, l }) => {
    const side = (isHome) => {
      const idx = isHome ? g.home_idx : g.away_idx;
      const t = team(idx);
      const score = isHome ? l.hp : l.ap;
      const ahead = score > (isHome ? l.ap : l.hp);
      return `<span class="tk-side${ahead ? " ahead" : ""}"><span class="who" data-team="${idx}">${logo(idx, 16)}<span class="tk-n"><span class="nm-full">${esc(t.name)}</span><span class="nm-abbr">${esc(t.abbr || t.name)}</span></span></span><b class="tk-s${SCORED.has(plainName(isHome ? g.home : g.away)) ? " new" : ""}">${score}</b></span>`;
    };
    return `<div class="tk${isLive(l) ? "" : " over"}" data-game="${esc(gameKey(g))}">
      <span class="tk-top"><span class="tk-clock">${esc(liveClock(l))}</span>
        <button type="button" class="tk-fold" aria-expanded="false"
          aria-label="Open ${esc(g.away)} at ${esc(g.home)} as a full scoreboard"></button></span>
      ${side(false)}${side(true)}</div>`;
  }).join("")}</div>`;
}

function renderScoreboard() {
  const { big, rest, live } = liveGames();
  const any = big.length + rest.length;
  $("liveblock").hidden = !any;
  if (!any) return;
  $("livetitle").textContent = live ? "Live now" : "Today's results";
  $("liveblock").classList.toggle("on", !!live);
  const odds = liveOdds();
  $("livecount").innerHTML = odds
    ? `<span class="lv-k">${esc(shortLabel(METRIC))} odds with today's results</span>
       <b class="lv-p">${pctOf(odds.p, METRIC)}</b>
       <span class="lv-d ${dirOf(odds.p - odds.base)}">${signed(odds.p - odds.base)}</span>` : "";
  $("livecount").title = `Your odds as the build had them, moved by each game that has `
    + `finished since. Games still on count once they end.`;
  // Each part keeps its order: the games open as tiles, then the rest as lines.
  const part = (list, relevant) => {
    const open = list.filter(x => liveOpen(x, relevant));
    const shut = list.filter(x => !liveOpen(x, relevant));
    return (open.length ? `<div class="sb">${open.map(x => scoreTileHTML(x)).join("")}</div>` : "")
      + (shut.length ? tickerHTML(shut) : "");
  };
  $("livelist").innerHTML = part(big, true)
    + (rest.length ? `<p class="tickhead">${big.length ? "Other games today" : "Today's games"}</p>`
       + part(rest, false) : "");
}

// The games that matter open as tiles and fold to a line; the rest are lines
// that open into tiles. What the reader chose for a game holds through the
// redraws every poll makes.
const LIVE_OPEN = new Map();
const liveOpen = (x, relevant) => {
  const k = gameKey(x.g);
  return LIVE_OPEN.has(k) ? LIVE_OPEN.get(k) : relevant;
};

// The season, as one row of opponent logos under the record: a W or an L
// under each game played, the games to come a little faded, the week on the
// slate ringed. Scores and kickoffs are in the hover text; a game to come
// puts its week on the slate when clicked.

function scheduleRows() {
  const me = teamIdx();
  const rows = (STATE.played || [])
    .filter(g => g.home === me || g.away === me)
    .map(g => ({ week: g.week, home_idx: g.home, away_idx: g.away, neutral: g.neutral,
                 hp: g.home_points, ap: g.away_points, title_game: g.title_game }));
  for (const g of RESULT.own_games) {
    const done = finalOf(g);
    rows.push(done ? { ...g, hp: done.home, ap: done.away } : g);
  }
  return rows.sort((a, b) => a.week - b.week || kickoffOrder(a) - kickoffOrder(b));
}

function seasonHTML(rows) {
  const me = teamIdx();
  const wk = selectedWeek();
  const weeks = new Set([...$("week").options].map(o => o.value));
  let last = null;
  return rows.map(g => {
    // A bye is a wider gap, so the weeks still read in order.
    const bye = !g.title_game && last != null && g.week > last + 1;
    if (!g.title_game) last = g.week;
    const home = g.home_idx === me;
    const opp = home ? g.away_idx : g.home_idx;
    const name = team(opp).name;
    const where = home || g.neutral ? "vs" : "at";
    const label = g.title_game ? "Title game" : `Week ${g.week}`;
    const past = g.hp != null;
    let said, mark = "";
    if (past) {
      const us = home ? g.hp : g.ap, them = home ? g.ap : g.hp;
      const won = us > them;
      said = `${label} ${where} ${name}: ${won ? "won" : "lost"} ${us}-${them}`;
      mark = `<b class="${won ? "w" : "l"}">${won ? "W" : "L"}</b>`;
    } else {
      said = `${label} ${where} ${name}, ${whenText(g, false) || "time not set"}`;
      mark = "<b></b>";
    }
    const pick = !g.title_game && weeks.has(String(g.week));
    const cls = `sz${past ? " past" : ""}${bye ? " afterbye" : ""}${pick && g.week === wk ? " on" : ""}`;
    // The logo carries the team, so hovering it opens that team's card; the
    // chip around it carries the week, so clicking it puts that week on the
    // slate.
    const inner = `<span class="sz-logo" data-team="${opp}">${logo(opp, 16)}</span>${mark}`;
    return pick
      ? `<button type="button" class="${cls}" data-week="${g.week}"${past ? ' data-past="1"' : ""} aria-label="${esc(said)}">${inner}</button>`
      : `<span class="${cls}" aria-label="${esc(said)}">${inner}</span>`;
  }).join("");
}

function renderSeasonRow() {
  const el = $("season");
  const t = team(teamIdx());
  if (!RESULT || !t.name) { el.innerHTML = ""; return; }
  el.innerHTML = seasonHTML(scheduleRows());
}

/** What the scoreboard touches, redrawn on each poll. */
function renderLive() {
  renderScoreboard();
  renderNeeds();
  renderOwnGames();
  renderRootList();
  renderSeasonRow();
}

const KEY_HTML = `<p class="key">
  <span><b class="k-clear">Team</b> significant</span>
  <span><b class="k-lean">Team</b> likely</span>
  <span><b>Team</b> lopsided game</span></p>`;

let LIMIT = 120;                // rows on screen before "Show more"

function renderRootList() {
  const games = visibleGames();
  const shown = games.slice(0, LIMIT);
  const slate = slateGames().length;
  const over = shown.filter(g => finalOf(g)).length;

  // Only the latest week's results get a value put on them, so say so
  // when an older slate is on screen.
  const replay = RESULT.replay || {};
  const wk = selectedWeek();
  const anyPast = shown.some(g => g.unvalued);
  const old = anyPast && replay.week
    ? `<p class="foot">Result values are available for week ${replay.week} `
      + `only. Earlier weeks show final scores.</p>` : "";
  const more = games.length > shown.length
    ? `<button type="button" id="showmore" class="more">Show ${Math.min(120,
        games.length - shown.length)} more of ${games.length - shown.length}</button>` : "";

  let body;
  if (shown.length) {
    body = gameRowsHTML(shown, SORT === "time") + more + KEY_HTML + old;
  } else if (!slate) {
    body = old || (!$("showplayed").checked && wk !== null && wk < STATE.current_week
      ? `<p class="foot">Week ${wk} is complete. Turn on Include played to see results.</p>`
      : `<p class="foot">No games left on this slate.</p>`);
  } else {
    body = `<p class="foot">No game on this slate has a significant effect on `
      + `your odds. Select All games to see every game.</p>` + old;
  }
  $("rootlist").innerHTML = body;

  $("roottitle").textContent = shown.length && shown.every(g => finalOf(g))
    ? "Results" : "Who to root for";
  $("rootcount").textContent = `${shown.length} of ${slate} games`
    + (over ? `, ${over} played` : "");
}

let LEAGUE_SCOPE = "all";     // every team, or just your team's conference

/** Every team's odds on the same measure, with your team brought into view. */
function renderLeague() {
  const key = METRIC;
  const me = teamIdx();
  const conf = team(me).conference;
  const indie = !conf || /independent/i.test(conf);
  if (indie) LEAGUE_SCOPE = "all";
  $("leagueseg").hidden = indie;
  $("leagueseg").querySelector('[data-v="conf"]').textContent = conf || "Conference";
  for (const b of $("leagueseg").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", String(b.dataset.v === LEAGUE_SCOPE));
  }
  const rows = RESULT.league
    .filter(r => LEAGUE_SCOPE === "all" || r.conference === conf)
    .sort((a, b) => b.p[key] - a.p[key]);
  // ESPN publishes its own odds on some of these, and not on others.
  const showEspn = rows.some(r => r.espn && r.espn[key] != null);
  $("leaguehead").textContent = metricLabel(key).replace(/ \(.*\)$/, "");
  let html = `<table class="lg"><thead><tr>
    <th class="num">#</th><th>Team</th><th class="num">Record</th><th class="num">Odds</th>
    ${showEspn ? '<th class="num" title="What ESPN gives for the same thing, '
      + 'from its FPI page">ESPN</th>' : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const rec = recordOf(r.idx);
    html += `<tr${r.idx === me ? ' class="mine"' : ""}><td class="num">${i + 1}</td>
      <td class="t"><span class="teamcell" data-team="${r.idx}">${logo(r.idx, 16)}${pollTag(r.idx)}<span class="tn">${esc(r.team)}</span></span></td>
      <td class="num muted">${rec.w}-${rec.l}</td>
      <td class="num"><b>${pctOf(r.p[key], key)}</b></td>
      ${showEspn ? `<td class="num muted">${r.espn && r.espn[key] != null
        ? pct(r.espn[key]) : "-"}</td>` : ""}</tr>`;
  });
  const wrap = $("league");
  wrap.innerHTML = html + "</tbody></table>";
  const mine = wrap.querySelector("tr.mine");
  if (mine) {
    const off = mine.getBoundingClientRect().top - wrap.getBoundingClientRect().top;
    wrap.scrollTop = Math.max(0, wrap.scrollTop + off - wrap.clientHeight / 3);
  }
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

/** The games a team has left: the rest of the real season, or the rest of a
 * simulated one from the week being shown. */
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

function tipHTML(idx, games, inSeason, tap = false) {
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
      <td><span class="teamcell">${logo(opp, 14)}${rankCell(opp, inSeason)}${esc(team(opp).name)}</span></td>
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
      <td><span class="teamcell">${logo(opp, 14)}${rankCell(opp, inSeason)}${esc(team(opp).name)}</span></td></tr>`;
  }).join("");
  const rating = t.rating != null ? `, ${esc(STATE.rating_label)} ${num(t.rating, 1)}` : "";
  // In the guide, where a team sits in the published polls. In a sample
  // season, where this week's ranking puts it.
  const r = cardRank(idx, inSeason);
  const ranked = r ? `#${r}` : "";
  const pickable = STATE.teams.some(x => x.idx === idx);
  // A mouse clicks the name itself; a tap has already been spent opening
  // this card, so the card carries the way through.
  const foot = !pickable ? ""
    : !tap ? `<p class="tipfoot">Click to view this team</p>`
    : idx === teamIdx() && $("seasonview").hidden ? ""
    : `<button type="button" class="tipgo" data-go="${idx}">Go to ${esc(t.name)}'s page</button>`;
  // The team's own odds on the measure you are looking at, from the league
  // table every guide carries. Not in a simulated season, where they would
  // be the real season's.
  const lg = !inSeason && RESULT ? RESULT.league.find(r => r.idx === idx) : null;
  const odds = lg && lg.p[METRIC] != null
    ? `<div class="tipodds" title="${esc(metricLabel(METRIC))}"><b>${pctOf(lg.p[METRIC], METRIC)}</b>
        <span>${esc(shortLabel(METRIC))}</span></div>` : "";
  return `<div class="tiphead">${logo(idx, 26)}<div class="tipwho">
      <div class="tipname">${esc(t.name)} <span class="muted">${w}-${l}</span>
        ${ranked ? `<span class="tiprank">${ranked}</span>` : ""}</div>
      <div class="tipsub">${esc(t.conference || "Independent")}${rating}</div></div>${odds}</div>
    ${rows ? `<table class="tiptable"><tbody>${rows}</tbody></table>`
           : '<p class="tipnone">No games played yet.</p>'}
    ${next ? `<p class="tiphead2">Still to play</p>
      <table class="tiptable next"><tbody>${next}</tbody></table>` : ""}
    ${foot}`;
}

/** Fill the card for a team and show it. ``tap`` is a touch screen's: the
 * card can be tapped, and sits by the name instead of the pointer. */
let TIP_Y = 0;

function showTip(el, idx, tap) {
  TIP_TEAM = idx;
  TIP_Y = scrollY;
  const tip = $("teamtip");
  const inSeason = !!el.closest("#seasonview");
  tip.innerHTML = tipHTML(idx, teamResults(idx, inSeason), inSeason, tap);
  tip.classList.toggle("tap", tap);
  if (!tap) tip.classList.remove("sheet");
  tip.style.maxHeight = "";
  const chip = el.closest("#season .sz[data-week]");
  if (chip) {
    const foot = tip.querySelector(".tipfoot") || tip.appendChild(document.createElement("p"));
    foot.className = "tipfoot";
    foot.textContent = `Click to view week ${chip.dataset.week}`;
  }
  tip.hidden = false;
  if (tap) placeTipBy(el);
}

/** Below the tapped name, or above it if there is more room there, and never
 * over it: the card scrolls inside itself rather than cover what was tapped. */
function placeTipBy(el) {
  const tip = $("teamtip");
  // On a phone the card is a sheet along the bottom edge, where the button
  // sits under a thumb; CSS places it.
  const sheet = matchMedia("(max-width: 640px)").matches;
  tip.classList.toggle("sheet", sheet);
  if (sheet) {
    tip.style.left = tip.style.top = tip.style.maxHeight = "";
    return;
  }
  const a = el.getBoundingClientRect();
  const below = innerHeight - a.bottom - 16, above = a.top - 16;
  tip.style.maxHeight = Math.max(160, Math.max(below, above)) + "px";
  const r = tip.getBoundingClientRect();
  const y = r.height <= below || below >= above ? a.bottom + 8 : a.top - r.height - 8;
  const x = Math.min(Math.max(8, a.left), Math.max(8, innerWidth - r.width - 8));
  tip.style.left = x + "px";
  tip.style.top = y + "px";
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
  fitTeamInput();
  if ($("seasonview").hidden) window.scrollTo({ top: 0 });
}

// A touch screen has no hover, and a tap also fires the mouse events a hover
// listens for, so the last pointer down decides which behaviour applies.
let POINTER = "mouse";
document.addEventListener("pointerdown", (e) => { POINTER = e.pointerType; }, true);
const touched = () => POINTER === "touch";

document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-team]");
  if (!el || !STATE || touched()) return;
  const idx = Number(el.dataset.team);
  if (idx === TIP_TEAM && !$("teamtip").hidden) return;
  showTip(el, idx, false);
  placeTip(e);
});
document.addEventListener("mousemove", (e) => {
  if (touched()) return;
  if (!$("teamtip").hidden && e.target.closest("[data-team]")) placeTip(e);
});
document.addEventListener("mouseout", (e) => {
  if (touched()) return;
  const el = e.target.closest("[data-team]");
  if (el && !(e.relatedTarget && el.contains(e.relatedTarget))) hideTip();
});
// Scrolling away closes a tapped card, but not the last few pixels of the
// flick that was still settling when the name was tapped.
window.addEventListener("scroll", () => {
  if (touched() && !$("teamtip").hidden && Math.abs(scrollY - TIP_Y) > 40) hideTip();
}, { passive: true });

// The keyboard. Team names are drawn in a dozen places, so they are made
// focusable as they arrive rather than in each one. Focus opens the card
// beside the name, Enter does what a click does, and Escape closes it. In the
// season row the chip is the button and the logo inside it is not a stop.
function focusableTeams() {
  tagTeamLinks();
  for (const el of document.querySelectorAll("[data-team]:not([tabindex])")) {
    if (el.closest("#season, #teamtip, a, button")) continue;
    el.tabIndex = 0;
    el.setAttribute("role", "link");
  }
}
// One pass after each burst of redrawing, on a timer rather than the next
// frame, which a tab that is not being painted never reaches.
let TAB_PASS = 0;
new MutationObserver(() => {
  clearTimeout(TAB_PASS);
  TAB_PASS = setTimeout(focusableTeams, 0);
}).observe(document.body, { childList: true, subtree: true });
focusableTeams();

document.addEventListener("focusin", (e) => {
  const el = e.target.closest && e.target.closest('[data-team][role="link"], #season button.sz');
  if (!el || !STATE || !el.matches(":focus-visible")) return;
  const host = el.matches("[data-team]") ? el : el.querySelector("[data-team]");
  if (!host) return;
  showTip(host, Number(host.dataset.team), false);
  placeTipBy(el);
});
document.addEventListener("focusout", (e) => {
  if (e.target.closest && e.target.closest('[data-team][role="link"], #season button.sz')) hideTip();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("teamtip").hidden) { hideTip(); return; }
  if ((e.key === "Enter" || e.key === " ") && e.target.matches
      && e.target.matches('[data-team][role="link"]')) {
    e.preventDefault();
    POINTER = "keyboard";
    e.target.click();
  }
});
document.addEventListener("click", (e) => {
  const go = e.target.closest(".tipgo");
  if (go) {
    hideTip();
    pickTeam(Number(go.dataset.go));
    return;
  }
  if (e.target.closest("#teamtip") || e.target.closest("#season")) return;
  const el = e.target.closest("[data-team]");
  if (el && STATE) {
    // A link goes where it says; its card was only the preview.
    if (el.closest("a[href]")) { hideTip(); return; }
    const idx = Number(el.dataset.team);
    if (touched()) {
      // A second tap on the same name closes its card.
      if (idx === TIP_TEAM && !$("teamtip").hidden) hideTip();
      else showTip(el, idx, true);
      return;
    }
    hideTip();
    pickTeam(idx);
    return;
  }
  if (touched()) hideTip();
  // The intro's link to the picker puts the cursor in it.
  if (e.target.closest('a[href="#team"]')) {
    e.preventDefault();
    $("team").focus();
  }
});

// Events

/** A pair of buttons that act as one choice. */
function segmented(id, set, redraw = renderRootList) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b || b.getAttribute("aria-pressed") === "true") return;
    $(id).querySelectorAll("button").forEach(x =>
      x.setAttribute("aria-pressed", String(x === b)));
    set(b.dataset.v);
    if (RESULT) redraw();
  });
}

window.addEventListener("popstate", () => location.reload());
// The logo goes to the front page itself, on the guide.
document.querySelector(".brand").addEventListener("click", () => {
  try {
    sessionStorage.setItem("cfbroot.home", "1");
    localStorage.setItem("cfbroot.tab", "guide");
  } catch { /* private mode: the saved team comes back, which is all */ }
});
$("rootsearch").addEventListener("input", () => { if (RESULT) renderRootList(); });
// A week already played only has results to show, so going to one turns on
// Include played; going back to a week to come turns it off again, if this
// was what turned it on.
let PLAYED_FOR_WEEK = false;

$("season").addEventListener("click", (e) => {
  const chip = e.target.closest("button.sz[data-week]");
  if (!chip || !RESULT) return;
  // The redraw below replaces the row, so by the time this click reached the
  // page's own handler the logo would be out of the page and read as a click
  // on a team. The click is the week's; it stops here.
  e.stopPropagation();
  hideTip();
  const sel = $("week"), played = $("showplayed");
  if (chip.dataset.past && !played.checked) {
    played.checked = true;
    PLAYED_FOR_WEEK = true;
  } else if (!chip.dataset.past && PLAYED_FOR_WEEK) {
    played.checked = false;
    PLAYED_FOR_WEEK = false;
  }
  sel.value = chip.dataset.week;
  sel.dispatchEvent(new Event("change"));
});
// A game in the rest of the season puts its week on the slate, as the season
// row does. Team names keep their own click.
$("needslist").addEventListener("click", (e) => {
  if (e.target.closest("[data-team]") || !RESULT) return;
  const row = e.target.closest(".nd[data-week]");
  if (!row) return;
  e.stopPropagation();
  const sel = $("week");
  if (sel.value === row.dataset.week) return;
  sel.value = row.dataset.week;
  sel.dispatchEvent(new Event("change"));
});
// Turning Include played on or off by hand makes it the reader's choice.
$("showplayed").addEventListener("change", () => { PLAYED_FOR_WEEK = false; });
$("share").addEventListener("click", async () => {
  if (!RESULT) return;
  const t = STATE.teams.find(x => x.name === RESULT.team);
  const url = t && t.slug ? new URL(`team/${t.slug}/`, new URL(BASE, location.href)).href
                          : location.href;
  // The race the team is really in, by the same rule as its share card: the
  // playoff, or failing a real chance of that, its conference.
  const h = RESULT.headline;
  const conf = (t && t.conference) || "";
  // A team out of every race is shared without a number.
  let race = h.make_playoff.p >= 0.005 ? `${pct(h.make_playoff.p)} to make the playoff` : "";
  if (!race && conf && !/independent/i.test(conf)) {
    if (h.win_conference && h.win_conference.p >= 0.005) {
      race = `${pct(h.win_conference.p)} to win the ${conf}`;
    } else if (h.make_conf_title_game && h.make_conf_title_game.p >= 0.005) {
      race = `${pct(h.make_conf_title_game.p)} to reach the ${conf} title game`;
    }
  }
  const text = race ? `${RESULT.team}: ${race}, and who to root for this week.`
                    : `${RESULT.team}: who to root for this week.`;
  const b = $("share");
  if (navigator.share) {
    try { await navigator.share({ title: document.title, text, url }); } catch { /* closed */ }
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    b.textContent = "Link copied";
  } catch {
    b.textContent = "Could not copy";
  }
  setTimeout(() => { b.textContent = "Share"; }, 2000);
});
$("livelist").addEventListener("click", (e) => {
  // Team names keep their own click: the card, or the team's page.
  if (e.target.closest("[data-team]")) return;
  const item = e.target.closest("[data-game]");
  if (!item) return;
  const tile = item.classList.contains("sbt");
  // A tile folds only from its button, since it is mostly things to read; a
  // line opens from anywhere on it.
  if (tile && !e.target.closest(".sb-fold")) return;
  // The redraw takes the clicked element out of the page, which the page's
  // own click handler would misread, so the click ends here.
  e.stopPropagation();
  LIVE_OPEN.set(item.dataset.game, !tile);
  renderScoreboard();
  const again = $("livelist").querySelector(`[data-game="${CSS.escape(item.dataset.game)}"] .sb-fold, `
    + `[data-game="${CSS.escape(item.dataset.game)}"] .tk-fold`);
  if (again && e.detail === 0) again.focus();     // keep a keyboard user's place
});
// A block's heading folds its body away: the scoreboard and the slate.
document.querySelectorAll("button.fold").forEach(b => b.addEventListener("click", () => {
  const open = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(open));
  $(b.getAttribute("aria-controls")).hidden = !open;
}));
for (const id of ["week", "showplayed"]) {
  $(id).addEventListener("change", () => {
    LIMIT = 120;
    nameAllOption();
    if (RESULT) render();
  });
}
$("rootlist").addEventListener("click", (e) => {
  if (!e.target.closest("#showmore")) return;
  LIMIT += 120;
  renderRootList();
});
segmented("sortseg", (v) => { SORT = v; });
segmented("showseg", (v) => { SHOW = v; });
segmented("leagueseg", (v) => { LEAGUE_SCOPE = v; }, () => renderLeague());
$("path").addEventListener("click", (e) => {
  const b = e.target.closest(".step");
  if (b && RESULT) setMetric(b.dataset.key);
});

localizeKickoffs();
boot();
