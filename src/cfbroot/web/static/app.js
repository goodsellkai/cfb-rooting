"use strict";

const $ = (id) => document.getElementById(id);

/* Percentages always show two decimals. */
const pct = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x))
  ? "-" : (100 * x).toFixed(d) + "%";
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
  renderTopMeta();
  if (window.CFBROOT_INFO) {          // a page that is only words
    document.body.classList.add("info");
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
    try { saved = localStorage.getItem("cfbroot.team"); } catch { /* private mode */ }
  }
  if (saved && teamNamed(saved)) {
    $("team").value = saved;
    loadTeam();
  } else {
    setBand(null);
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

function metaItem(text, value, href, title, cls) {
  const inner = `${esc(text)} <b>${esc(value)}</b>`;
  const tip = title ? ` title="${esc(title)}"` : "";
  const kind = cls ? ` class="${cls}"` : "";
  return href
    ? `<a${kind} href="${href}" target="_blank" rel="noopener"${tip}>${inner}</a>`
    : `<span${kind}${tip}>${inner}</span>`;
}

const ASSETS = STATIC ? BASE + "static/" : "/static/";

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
  const age = STATE.loaded_at ? Date.now() / 1000 - STATE.loaded_at : 0;
  const stale = age > 86400;
  const sims = STATE.loaded_at
    ? metaItem("Simulated", ago(new Date(1000 * STATE.loaded_at).toISOString()),
           null,
           stale ? "The numbers have not been rebuilt for a day. Results "
                   + "since then are not in them." : "",
           stale ? "old" : "") : "";
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
    const resp = await fetch(SCOREBOARD,
      { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!resp.ok) return;
    const data = await resp.json();
    LIVE.clear();
    for (const ev of data.events || []) {
      const s = liveState(ev);
      if (s) LIVE.set(gameKey(s), s);
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
    band.style.setProperty("--photo-blend", light ? "screen" : "multiply");
    document.documentElement.style.setProperty("--team-tint", `rgb(${rgb.join(",")})`);
  } else {
    document.documentElement.style.removeProperty("--team-tint");
    for (const v of ["--team", "--team-fg", "--team-mark", "--photo-blend"]) {
      band.style.removeProperty(v);
    }
  }
  // Each team keeps the same photo, so its page looks the same every visit.
  band.dataset.photo = String(idx == null ? 3 : idx % 4 + 1);
  band.classList.toggle("home", idx == null);
  const plate = $("teamlogo");
  if (t && t.logo) {
    plate.innerHTML = `<img src="${esc(t.logo)}" alt="" width="48" height="48">`;
    plate.hidden = false;
  } else {
    plate.hidden = true;
  }
  if (t == null) {
    $("teamline").textContent = "Every game left, sorted by how much it moves your "
      + "team's playoff odds.";
  }
}

/** Wins and losses so far, overall and in conference. */
function recordOf(idx) {
  const r = { w: 0, l: 0, cw: 0, cl: 0 };
  for (const g of STATE.played || []) {
    if (g.home !== idx && g.away !== idx) continue;
    const won = (g.home === idx) === (g.home_points > g.away_points);
    won ? r.w++ : r.l++;
    if (g.conference && !g.title_game) won ? r.cw++ : r.cl++;
  }
  return r;
}

function renderTeamLine(idx) {
  const t = team(idx);
  const r = recordOf(idx);
  const bits = [];
  const conf = t.conference && !/independent/i.test(t.conference)
    ? ` (${r.cw}-${r.cl} ${esc(t.conference)})` : "";
  bits.push(`<span><b>${r.w}-${r.l}</b>${conf}</span>`);
  const pr = pollRank(idx);
  if (pr.best) bits.push(`<span>${pr.kind} No. ${pr.best}</span>`);
  if (RESULT && RESULT.team === t.name) {
    bits.push(`<span><b>${num(RESULT.expected_wins)}</b> wins expected</span>`);
  }
  $("teamline").innerHTML = bits.join(" &nbsp; ");
}

// The road: every measure, in the order a season reaches them

const PATH = [
  ["finish_ranked", "Finish ranked", "Ranked"],
  ["make_conf_title_game", "{c} title game", "CCG"],
  ["win_conference", "Win {c}", "Conf"],
  ["make_playoff", "Make the playoff", "Playoff"],
  ["top4_seed", "Top-4 seed", "Bye"],
  ["reach_quarterfinal", "Quarterfinal", "QF"],
  ["reach_semifinal", "Semifinal", "SF"],
  ["reach_title_game", "National title game", "Final"],
  ["win_national_title", "Win national title", "Title"],
];

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

/** The measures as a row of bars; each one is also the switch for the list. */
function renderPath(grow) {
  const conf = (team(teamIdx()).conference || "conference")
    .replace(/ Conference$/, "");
  const steps = pathSteps();
  if (!steps.some(([k]) => k === METRIC)) METRIC = "make_playoff";
  const el = $("path");
  el.classList.toggle("short", steps.length < PATH.length);
  el.innerHTML = steps.map(([k, label, short]) => {
    const p = RESULT.headline[k].p;
    const full = label.replace("{c}", conf);
    return `<button type="button" class="step" data-key="${k}"
        aria-pressed="${k === METRIC}" title="${esc(metricLabel(k))}: ${pct(p)}">
      <span class="v">${num(100 * p)}<small>%</small></span>
      <span class="bar"><i data-h="${(100 * p).toFixed(2)}"
        style="height:${grow ? 0 : (100 * p).toFixed(2)}%"></i></span>
      <span class="k"><span class="full">${esc(full)}</span><span class="short">${esc(short)}</span></span>
    </button>`;
  }).join("");
  if (grow) {
    // The one moment of motion: a new team's season fills in.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.querySelectorAll(".bar i").forEach(i => { i.style.height = i.dataset.h + "%"; });
    }));
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
  new IntersectionObserver(([e]) => {
    $("toolbar").classList.toggle("stuck", !e.isIntersecting);
  }).observe($("band"));
}

function renderToolbarContext() {
  const idx = teamIdx();
  if (idx == null) { $("tbctx").innerHTML = ""; return; }
  $("tbctx").innerHTML = `${logo(idx, 20)}${esc(RESULT.team)}
    <span class="muted">${esc(metricLabel(METRIC))}</span> ${pct(RESULT.headline[METRIC].p)}`;
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
    render(true);
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
  $("empty").hidden = true;
  $("results").hidden = false;
  const idx = teamIdx();
  renderTeamLine(idx);
  renderPath(newTeam);
  renderToolbarContext();
  renderDist("winsdist", RESULT.wins_distribution, (i) => i);
  // Missing out is already on the road above, and as a bar it dwarfs the
  // seeds, so the chart is seeds only and the miss is a number.
  const seeds = RESULT.seed_distribution;
  const inField = seeds.slice(1).reduce((a, b) => a + b, 0);
  $("seednote").textContent = `misses in ${pct(seeds[0])}`;
  $("seeddist").hidden = inField < 0.002;
  if (inField >= 0.002) renderDist("seeddist", seeds.map((v, i) => (i ? v : 0)), String);
  $("winsnote").textContent = `${num(RESULT.expected_wins)} expected`;
  renderOwnGames();
  renderRootList();
  renderLeague();
  $("weeklabel").textContent = selectedWeek() === null
    ? "all remaining" : "week " + selectedWeek();
  $("footmeta").textContent =
    `${STATE.year} week ${STATE.current_week}, `
    + `${RESULT.n_sims.toLocaleString()} simulated seasons`;
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
    b.innerHTML = `<span class="pv">${num(100 * dist[i])}</span>`
      + `<span class="col"><i style="height:${(100 * dist[i] / max).toFixed(2)}%"></i></span>`
      + `<span class="lb">${labelFn(i)}</span>`;
    el.appendChild(b);
  }
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

/** Kickoff and network, what the game is doing now, or that it is over.
 * ``opp`` is the "over Utah" a phone shows here, where it has room. */
function whenHTML(g, done, opp = "") {
  const tv = g.broadcast ? esc(g.broadcast) : "";
  const live = LIVE.get(gameKey(g));
  const o = opp ? `<span class="opp">${opp}</span>` : "";
  if (done) return `<div class="when">${o}<b>Final</b></div>`;
  if (live && live.state === "in") {
    return `<div class="when">${o}<b class="live">${esc(live.detail)}</b>`
      + `<span class="tv">${esc(live.score)}${tv ? ", " + tv : ""}</span></div>`;
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
// for, over the other one. With 2.5 million seasons most games clear the bar,
// so a slate runs to dozens of rows. The biggest swings open with both sides'
// numbers; the rest stay one line until opened.

const OPENED = new Map();     // a game's key -> opened or closed by hand

function rowKey(g) { return `${g.week}|${gameKey(g)}`; }

/** The games that open by themselves. As many as are worth 40% of the
 * biggest, never fewer than three or more than ten. In impact order they are
 * the top of the list, so the open rows sit together; in kickoff order they
 * are the big ones, wherever they fall. */
function featured(games) {
  const gap = (g) => Math.abs(g.swings[METRIC].delta || 0);
  const live = games.filter(g => !finalOf(g));
  const top = Math.max(0, ...live.map(gap));
  const big = live.filter(g => gap(g) >= 0.4 * top).length;
  const n = Math.min(10, Math.max(3, big));
  const pick = SORT === "time" ? live.slice().sort((a, b) => gap(b) - gap(a)) : live;
  return new Set(pick.slice(0, n).map(rowKey));
}

/** A list of games. ``open`` says which start opened; ``slots`` groups them
 * under their kickoff time. */
function gameRowsHTML(games, open, slots) {
  const key = METRIC;
  // A list of finished games has nothing to root for: it is results.
  const over = games.length && games.every(g => finalOf(g));
  const maxAbs = Math.max(1e-6, ...games.filter(g => !g.unvalued)
    .map(g => Math.abs(g.swings[key].delta || 0)));
  const head = `<div class="slatehead" aria-hidden="true">
      <span class="h-when">${over ? "Played" : "Kickoff" + (ZONE ? ", " + esc(ZONE) : "")}</span>
      <span>${over ? "Winner" : "Root for"}</span>
      <span class="num" title="${over ? "Final score" : "How likely that side is to win the game"}">${over ? "Score" : "Win"}</span>
      ${games.every(g => g.unvalued) ? `<span class="h-od"></span><span></span><span class="h-imp"></span>` : `
      <span class="num h-od" title="${esc(metricLabel(key))}, if that side wins">Your odds</span>
      <span class="num" title="Change from the odds now, in percentage points">Change</span>
      <span class="h-imp" title="How far apart the two results leave your odds, in points">Impact</span>`}
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
    body += g.unvalued ? resultHTML(g) : gameHTML(g, key, maxAbs, open.has(rowKey(g)));
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
        <span class="nm"><i class="at">${at}</i><span class="who" data-team="${w}">${logo(w, 18)}${pollTag(w)}<b>${esc(homeWon ? g.home : g.away)}</b></span><span class="over">${opp}</span></span>
        <span class="num pr score">${score}</span>
      </div>
      <div class="imp"></div>
    </article>`;
}

/** One game. Before it, the first line is the side to root for; after it,
 * the winner. */
function gameHTML(g, key, maxAbs, startOpen) {
  const s = g.swings[key];
  const done = finalOf(g);
  const base = baseFor(g, key);
  const conf = confidenceOf(s);
  const pHome = g.p_home_win;
  const homeWon = done ? done.home > done.away : null;
  const firstHome = done ? homeWon : s.home;
  const k = rowKey(g);
  const open = OPENED.has(k) ? OPENED.get(k) : startOpen;

  const side = (isHome, first) => {
    const idx = isHome ? g.home_idx : g.away_idx;
    const p = isHome ? s.p_if_home : s.p_if_away;
    const d = p - base;
    const cls = !first ? "side other" + (done ? " lost" : "")
      : done ? "side root won" : "side root";
    const at = isHome ? (g.neutral ? "vs" : "@") : "";
    const otherIdx = isHome ? g.away_idx : g.home_idx;
    const over = first ? `<span class="over">${done ? "beat" : "over"} `
      + `${vsName(otherIdx, isHome ? g.away : g.home)}</span>` : "";
    const lead = done
      ? `<span class="num pr" title="Final score">${isHome ? done.home : done.away}</span>`
      : `<span class="num pr">${pct(isHome ? pHome : 1 - pHome, 0)}</span>`;
    return `<div class="${cls}">
      <span class="nm"><i class="at">${at}</i><span class="who" data-team="${idx}">${logo(idx, 18)}${pollTag(idx)}<b>${esc(isHome ? g.home : g.away)}</b></span>${over}</span>
      ${lead}
      <span class="num od">${pct(p)}</span>
      <span class="num dd ${dirOf(d)}">${signed(d)}</span>
    </div>`;
  };

  let imp;
  if (done) {
    const move = (homeWon ? s.p_if_home : s.p_if_away) - base;
    imp = `<div class="imp" title="What the result did to your odds">
        <b class="${dirOf(move)}">${signed(move)}</b></div>`;
  } else {
    const gap = Math.abs(s.delta);
    const w = Math.min(100, 100 * gap / maxAbs);
    imp = `<div class="imp" title="${esc(CONF_TITLE[conf])}">
        <b>${(100 * gap).toFixed(2)}</b>
        <span class="bar"><i style="width:${w.toFixed(1)}%"></i></span></div>`;
  }
  const opp = `${done ? "beat" : "over"} `
    + vsName(firstHome ? g.away_idx : g.home_idx, firstHome ? g.away : g.home);
  return `<article class="game ${done ? "played" : conf}${open ? " open" : ""}" data-key="${esc(k)}">
      ${whenHTML(g, done, opp)}${side(firstHome, true)}${side(!firstHome, false)}${imp}
      <button type="button" class="chev" aria-expanded="${open}"
        aria-label="Both sides of ${esc(g.away)} at ${esc(g.home)}"></button></article>`;
}

/** Open or close a game, and remember it through the next redraw. */
function toggleGame(row) {
  const open = !row.classList.contains("open");
  row.classList.toggle("open", open);
  row.querySelector(".chev").setAttribute("aria-expanded", String(open));
  OPENED.set(row.dataset.key, open);
}

function renderOwnGames() {
  const games = ownGames();
  const panel = $("owngames");
  if (!games.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("ownhead").textContent = `${RESULT.team} game${games.length > 1 ? "s" : ""}`;
  $("owntable").innerHTML = gameRowsHTML(games, new Set(games.map(rowKey)), false);
}

const KEY_HTML = `<p class="key">
  <span><b class="k-clear">Team</b> clear</span>
  <span><b class="k-lean">Team</b> a lean</span>
  <span><b>Team</b> too few seasons to be sure</span></p>`;

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
    ? `<p class="foot">What each result was worth is worked out for week `
      + `${replay.week} only. Earlier weeks show the score.</p>` : "";
  const more = games.length > shown.length
    ? `<button type="button" id="showmore" class="more">Show ${Math.min(120,
        games.length - shown.length)} more of ${games.length - shown.length}</button>` : "";

  let body;
  if (shown.length) {
    body = gameRowsHTML(shown, featured(shown), SORT === "time") + more + KEY_HTML + old;
  } else if (!slate) {
    body = old || (!$("showplayed").checked && wk !== null && wk < STATE.current_week
      ? `<p class="foot">Week ${wk} is over. Turn on Include played to see its results.</p>`
      : `<p class="foot">No games left on this slate.</p>`);
  } else {
    body = `<p class="foot">No game here moves your odds enough to call. `
      + `Switch to all games to see the rest.</p>` + old;
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
    <th class="num">#</th><th>Team</th>
    <th class="num" title="${esc(STATE.rating_label)} rating">${esc(STATE.rating_label)}</th>
    <th class="num">Odds</th>
    ${showEspn ? '<th class="num" title="What ESPN gives for the same thing, '
      + 'from its FPI page">ESPN</th>' : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    html += `<tr${r.idx === me ? ' class="mine"' : ""}><td class="num">${i + 1}</td>
      <td class="t"><span class="teamcell" data-team="${r.idx}">${logo(r.idx, 16)}${pollTag(r.idx)}<span class="tn">${esc(r.team)}</span></span></td>
      <td class="num muted">${num(r.rating, 1)}</td>
      <td class="num"><b>${pct(r.p[key])}</b></td>
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
  const pick = STATE.teams.some(x => x.idx === idx) ? "Click to make this your team" : "";
  return `<div class="tiphead">${logo(idx, 26)}<div>
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
  fitTeamInput();
  if ($("seasonview").hidden) window.scrollTo({ top: 0 });
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
  if (el && STATE) {
    hideTip();
    pickTeam(Number(el.dataset.team));
    return;
  }
  // Anywhere else on a game opens or closes it.
  const row = e.target.closest(".game[data-key]");
  if (row && !String(getSelection())) {
    toggleGame(row);
    return;
  }
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
$("rootsearch").addEventListener("input", () => { if (RESULT) renderRootList(); });
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

boot();
