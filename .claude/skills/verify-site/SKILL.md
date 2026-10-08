---
name: verify-site
description: Build a small static export, serve it, and check the pages at desktop and phone widths. Use after any change to web/ (pages.py, export.py, templates, app.js, season.js, app.css) to confirm it works in a browser rather than in theory.
---

# Verify the site

A change to the web layer is not done until a built page has been looked at. The
dev server is not enough: the static build rewrites paths, injects the head and
writes the data files, and most things that break, break there.

## Build

```bash
CFBROOT_SAMPLES=1 CFBROOT_REPLAY_SIMS=20000 CFBROOT_SITE_URL=https://cfbroot.com \
  .venv/Scripts/python.exe -m cfbroot.cli export --out "<scratchpad>/site" --sims 20000
```

Small sims keep it near 40 seconds. The numbers will be noisy and almost nothing
will clear the significance filter; that is expected and not what is being checked.

## Serve and look

Add a config to `.claude/launch.json` pointing `python -m http.server` at the
export, then `preview_start` it. Never background a server with Bash.

Check, with `javascript_tool` rather than screenshots where possible, since the
pane often fails to paint:

- `document.documentElement.scrollWidth` equals `clientWidth`. Any excess is a
  horizontal scroll, which is the most common regression.
- The same at `resize_window` mobile (375) and 1280 wide.
- Tables: `table.scrollWidth > wrap.clientWidth` says it needs sideways dragging.
- `#writeup` is present and visible after the app loads. It is what crawlers read.
- The console, via `read_console_messages`.

## Afterwards

Stop the preview server, take the config back out of `.claude/launch.json`, and
delete the export. Leaving a 150 MB build in the scratchpad is untidy.

## Cached assets

The HTML tags `app.css` and `app.js` with the build time, so a rebuilt export
serves fresh ones. Hand-copying a file into an existing export does not: fetch
the versioned URL with `{cache: "reload"}` first, then reload.
