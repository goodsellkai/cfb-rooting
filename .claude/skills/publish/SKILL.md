---
name: publish
description: Commit, push and trigger a site build.
disable-model-invocation: true
---

# Publish

Run the tests first. `pytest -q` takes about a minute and the published numbers
depend on it.

## Commit

Author is Kai, and there is no Claude co-author trailer:

```bash
git -c user.name="Kai Goodsell" -c user.email="goodsellkai@gmail.com" \
  commit -q -m "<subject>" -m "<body>"
```

Subject in plain words, lower case after the first letter, no "feat:" prefixes.
Body says what was wrong and what now happens instead. No em dashes.

## Push

The credential helper has to be named explicitly on this machine:

```bash
git -c credential.helper= \
    -c 'credential.helper=!"/c/Program Files/GitHub CLI/gh.exe" auth git-credential' push
```

If it is rejected, fetch and rebase onto `origin/main` rather than merging.

## Build

Pushing does not publish. The workflow only runs on a schedule or by hand:

```bash
"/c/Program Files/GitHub CLI/gh.exe" workflow run "Publish site" --ref main
```

Confirm it got a runner, then stop watching. A run that sits queued for fifteen
minutes and is cancelled with no logs means GitHub never assigned one; that is
not a code problem and nothing in the repo can prevent it.

On a Saturday a run keeps rebuilding for about five hours. On a weekday it builds
once. `-f minutes=<n>` overrides that.

## Report

Give the run URL, the commit, and what will be different once it lands.
