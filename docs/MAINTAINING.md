# Maintaining this fork

This repository is a fork of `iamfoz/backblaze-64-personal-wine-container` (AGPL-3.0). Three things need tending: the upstream
project, Wine, and Backblaze's client. The repository watches all of them and tells you; you decide when to publish.

## What watches what

`.github/workflows/upstream-watch.yml` runs daily (06:17 UTC) and on demand. For each thing that moved it opens one issue titled
`[update] <what> <version>` and, when `NOTIFY_URL` is set, sends one push notification that links to the Release workflow.

| Finding | Meaning | Your next step |
|---|---|---|
| `[update] Wine wine-11.20` | A newer Wine than the one `:beta` (`Dockerfile.beta`) and `:latest-patched` (`patches/promoted`) build | Run **Release** with `wine_ref` set. Test the beta first. |
| `[update] WineHQ stable 11.0.1` | WineHQ's stable moved past the Wine in the published `:latest` | Run **Release** to rebuild `:latest`. |
| `[update] Backblaze client 10.0.4.1080` | Backblaze is serving a new client | Watch `:beta` for a pass that completes. The stable image stays pinned. |
| `[update] jlesage base ubuntu-24.04-v4.15.0` | A newer GUI base image | Bump the tag and digest in the Dockerfile, then Release. |
| `[update] Upstream abc1234` | iamfoz's repository has commits this fork lacks | `git fetch upstream && git merge upstream/main`, check, push, Release. The issue says whether a trial merge is clean. |

Close an issue when you have dealt with it. Closing is the memory: a closed issue stops the same finding being filed again.

Inside each running container a second check (`bbupdate.py`) tells users when a newer *image* is on Docker Hub; that needs
nothing from you except pushing images.

## Publishing

Actions → **Release** → *Run workflow*.

* `version`: `v10.2.2-aon.1` (upstream's version, then `-aon.N` for this fork's own releases).
* `wine_ref`: leave empty to keep the Wine pins, or `wine-11.20` to move the beta and `:latest-patched` first.

The workflow tags the commit, starts **Docker Build and Push** on the tag, and creates a GitHub release. That workflow runs the
test suites, builds each image, boots it and runs the smoke test, then pushes to Docker Hub:
`:latest`, `:ubuntu24`, `:ubuntu26`, `:<version>`, `:<version>-ubuntu26`, `:latest-patched`, `:<version>-patched`. `:beta` is
rebuilt every Monday on its own schedule.

Unraid users then see "update ready" on their Docker tab.

## One-time setup

Secrets are typed at the prompt, never pasted into chat or a file:

```bash
gh secret set DOCKERHUB_USERNAME --repo aon082910/backblaze-personal
gh secret set DOCKERHUB_TOKEN    --repo aon082910/backblaze-personal   # a Docker Hub access token with read/write
gh secret set NOTIFY_URL         --repo aon082910/backblaze-personal   # e.g. https://ntfy.sh/<long-random-topic>
gh secret set NOTIFY_KIND        --repo aon082910/backblaze-personal   # ntfy (default), discord, slack, gotify, webhook
```

`NOTIFY_URL` is optional: without it you still get an issue (and GitHub's own email or mobile notification if you watch the
repository). Subscribe your phone to the ntfy topic with the ntfy app.

```bash
git remote add upstream https://github.com/iamfoz/backblaze-64-personal-wine-container.git
```

## Changing the settings the container offers

Edit `rootfs/usr/local/lib/bb-monitor/bbcontainer.py` (`REGISTRY`). That one list drives the Container tab, the validation, and
the template. Then:

```bash
python3 tools/gen_template.py          # rewrites templates/backblaze-personal.xml
python3 tests/test-container.py        # fails if the template is stale or a default does not validate
```

Copy the template to `AoN-Unraid-Apps/Backblaze-Personal/backblaze-personal.xml` (the file Unraid's Community Applications
reads).

## Tests

```bash
sh tests/test-parse.sh                 # every script parses (needs python3 and node)
python3 tests/test-beta.py             # web interface, API, notifications
python3 tests/test-container.py        # Container tab, entrypoint, update checks, template
bash tests/test-switches.sh
```

Build and boot locally with `docker build -f Dockerfile.ubuntu24 -t bb-local:stock .` and
`.github/smoke-test.sh bb-local:stock`.

## Line endings

The repository is pinned to LF in `.gitattributes`. If a Windows checkout has `core.autocrlf=true`, shell scripts arrive with CRLF
and the image fails at its first `sh` script. Run `git config core.autocrlf false` in this clone.
