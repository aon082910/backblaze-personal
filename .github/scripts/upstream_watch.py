#!/usr/bin/env python3
"""Watch everything this fork depends on and say so when something moves.

Runs daily from .github/workflows/upstream-watch.yml. Standard library only.

What it watches, and what "moved" means for each:

  wine-dev     the newest wine-X.Y tag on WineHQ's GitLab, against the WINE_REF the
               beta (Dockerfile.beta) and :latest-patched (patches/promoted) build
  wine-stable  winehq-stable in WineHQ's apt repository, against the Wine inside the
               last published :latest image (its org.opencontainers.image.* label)
  client       the Backblaze client Backblaze is serving, against the last one
               announced (an issue title is the memory, see below)
  base         the newest jlesage/baseimage-gui for the Ubuntu in each Dockerfile,
               against the tag the Dockerfile pins
  upstream     iamfoz/backblaze-64-personal-wine-container: new commits on main and
               new release tags, against the commits this fork already contains

How it remembers. There is no state file. Every finding is an issue titled
"[update] <what> <version>", and a finding is new only if no issue (open or closed)
carries that title. Closing the issue is how the maintainer says "seen". This keeps the
memory visible, survives a re-run, and costs no extra permissions.

How it tells the maintainer. A new issue is the record; the push notification is the
nudge, sent to NOTIFY_URL when that secret is set (ntfy, Discord, Slack, Gotify or a
JSON webhook, chosen by NOTIFY_KIND). The message names the next step and carries a
link to the page where it is done.

Dry run: DRY_RUN=1 prints what it would file and send and touches nothing.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "aon082910/backblaze-personal")
UPSTREAM = os.environ.get("UPSTREAM_REPO", "iamfoz/backblaze-64-personal-wine-container")
IMAGE = os.environ.get("IMAGE", "allornothing/backblaze-personal")
DRY = os.environ.get("DRY_RUN") == "1"
UA = {"User-Agent": "bb-upstream-watch"}


def get(url, accept=None, timeout=30):
    req = urllib.request.Request(url, headers=dict(UA, **({"Accept": accept} if accept else {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url):
    return json.loads(get(url))


def vkey(v):
    """Sortable key for 11.19, 11.0.1, 10.0.3.1075 and the like."""
    parts = [int(x) for x in re.findall(r"\d+", v)[:5]]
    while len(parts) > 1 and parts[-1] == 0:      # 11.0 and 11.0.0.0 are the same Wine
        parts.pop()
    return tuple(parts)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def gh(*args, check=True):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError("gh %s: %s" % (" ".join(args[:3]), p.stderr.strip()))
    return p.stdout


# ---- the checks -----------------------------------------------------------------
# Each returns a list of findings: (key, title_suffix, detail, action).
# key is the issue title after "[update] ". action is what the maintainer does next.

def check_wine_dev():
    tags = get_json("https://gitlab.winehq.org/api/v4/projects/wine%2Fwine/repository/tags"
                    "?search=%5Ewine-1&order_by=updated&sort=desc&per_page=30")
    names = [t["name"] for t in tags if re.fullmatch(r"wine-\d+\.\d+", t["name"])]
    if not names:
        return []
    newest = max(names, key=vkey)
    pinned = {}
    m = re.search(r"^WINE_REF=(\S+)", read("patches/promoted"), re.M)
    if m:
        pinned[":latest-patched"] = m.group(1)
    m = re.search(r"^ARG WINE_REF=(\S+)", read("Dockerfile.beta"), re.M)
    if m:
        pinned[":beta"] = m.group(1)
    behind = {k: v for k, v in pinned.items() if vkey(v) < vkey(newest)}
    if not behind:
        return []
    where = ", ".join("%s is on %s" % (k, v) for k, v in sorted(behind.items()))
    return [("Wine %s" % newest,
             "Wine %s is out on WineHQ (%s)." % (newest, where),
             "Bump WINE_REF in Dockerfile.beta and, once the beta has run well, in "
             "patches/promoted, then run the Release workflow.")]


def check_wine_stable():
    import gzip
    raw = gzip.decompress(get("https://dl.winehq.org/wine-builds/ubuntu/dists/noble/main/"
                              "binary-amd64/Packages.gz", timeout=60)).decode("utf-8", "replace")
    m = re.search(r"^Package: winehq-stable\n(?:.*\n)*?Version: (\S+)", raw, re.M)
    if not m:
        return []
    avail = m.group(1).split("~")[0]
    have = published_label("wine")
    if have is None or vkey(have) >= vkey(avail):
        return []
    return [("WineHQ stable %s" % avail,
             "WineHQ stable is %s; the published :latest image carries Wine %s." % (avail, have),
             "Run the Release workflow to rebuild :latest on the new Wine.")]


def published_label(key):
    """A label off the published :latest image, or None when it cannot be read.
    The image stamps wine=... into its labels at build time (see the Dockerfiles)."""
    try:
        token = get_json("https://auth.docker.io/token?service=registry.docker.io&scope="
                         "repository:%s:pull" % IMAGE)["token"]
        def reg(path, accept):
            req = urllib.request.Request("https://registry-1.docker.io/v2/%s/%s" % (IMAGE, path),
                                         headers=dict(UA, Authorization="Bearer " + token, Accept=accept))
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        idx = reg("manifests/latest", "application/vnd.oci.image.index.v1+json,"
                  "application/vnd.docker.distribution.manifest.list.v2+json,"
                  "application/vnd.oci.image.manifest.v1+json,"
                  "application/vnd.docker.distribution.manifest.v2+json")
        if "manifests" in idx:
            idx = reg("manifests/" + idx["manifests"][0]["digest"], "application/vnd.oci.image.manifest.v1+json,"
                      "application/vnd.docker.distribution.manifest.v2+json")
        cfg = reg("blobs/" + idx["config"]["digest"], "application/json")
        return (cfg.get("config", {}).get("Labels") or {}).get("net.aon.%s" % key)
    except Exception as exc:     # no image yet, Hub down: nothing to compare, not a finding
        print("note: could not read the published image label: %s" % exc, file=sys.stderr)
        return None


def check_client():
    for mirror in ("ca000", "ca001", "ca002"):
        try:
            xml = get("https://%s.backblaze.com/api/clientversion.xml" % mirror, timeout=15).decode()
        except Exception:
            continue
        m = re.search(r'<update win32_version="([0-9.]+)"', xml)
        if m:
            v = m.group(1)
            return [("Backblaze client %s" % v,
                     "Backblaze is now serving client %s." % v,
                     "Containers on the unpinned tags install it at the next start. Watch the beta "
                     "for a backup pass that completes before telling anyone to move; the stable "
                     "image pins 10.0.1.1069 until the Wine service-token fix is promoted.")]
    return []


def check_base():
    out = []
    for df in ("Dockerfile.ubuntu24", "Dockerfile.ubuntu26", "Dockerfile.beta"):
        if not os.path.exists(df):
            continue
        m = re.search(r"jlesage/baseimage-gui:(ubuntu-\d+\.\d+)-v(\d+\.\d+\.\d+)", read(df))
        if not m:
            continue
        flavour, cur = m.groups()
        try:
            res = get_json("https://hub.docker.com/v2/repositories/jlesage/baseimage-gui/tags"
                           "?name=%s-v&page_size=50&ordering=last_updated" % flavour)["results"]
        except Exception:
            continue
        vers = [t["name"].split("-v", 1)[1] for t in res
                if re.fullmatch(re.escape(flavour) + r"-v\d+\.\d+\.\d+", t["name"])]
        if vers and vkey(max(vers, key=vkey)) > vkey(cur):
            new = max(vers, key=vkey)
            out.append(("jlesage base %s-v%s" % (flavour, new),
                        "%s pins jlesage/baseimage-gui %s-v%s; v%s is out." % (df, flavour, cur, new),
                        "Bump the tag and its digest in %s, then run the Release workflow." % df))
    return out


def check_upstream():
    out = []
    # Commits on iamfoz's main that this fork does not contain.
    subprocess.run(["git", "remote", "remove", "upstream"], capture_output=True)
    subprocess.run(["git", "remote", "add", "upstream", "https://github.com/%s.git" % UPSTREAM], check=True)
    subprocess.run(["git", "fetch", "--quiet", "--tags", "upstream", "main"], check=True)
    behind = int(subprocess.run(["git", "rev-list", "--count", "HEAD..upstream/main"],
                                capture_output=True, text=True, check=True).stdout.strip() or 0)
    if behind:
        head = subprocess.run(["git", "rev-parse", "--short", "upstream/main"],
                              capture_output=True, text=True, check=True).stdout.strip()
        subjects = subprocess.run(["git", "log", "--format=- %s", "-n", "8", "HEAD..upstream/main"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        clean = merges_clean()
        out.append(("Upstream %s" % head,
                    "iamfoz/backblaze-64-personal-wine-container is %d commit(s) ahead of this fork "
                    "(upstream/main at %s). %s\n\nNewest:\n%s" % (
                        behind, head,
                        "A trial merge is clean." if clean else
                        "A trial merge has conflicts: resolve them by hand.", subjects),
                    "git fetch upstream && git merge upstream/main, check the template and "
                    "README.md still carry this fork's additions, push, then run the Release workflow."))
    return out


def merges_clean():
    """Whether upstream/main merges into HEAD without a conflict, tried and undone."""
    p = subprocess.run(["git", "merge", "--no-commit", "--no-ff", "upstream/main"],
                       capture_output=True, text=True)
    subprocess.run(["git", "merge", "--abort"], capture_output=True)
    return p.returncode == 0


CHECKS = (("upstream", check_upstream), ("wine-dev", check_wine_dev), ("wine-stable", check_wine_stable),
          ("client", check_client), ("base", check_base))


# ---- filing and telling -----------------------------------------------------------

def known(title):
    out = gh("issue", "list", "--repo", REPO, "--state", "all", "--search",
             '"%s" in:title' % title, "--json", "title", "--limit", "20", check=False)
    try:
        return any(i["title"] == title for i in json.loads(out or "[]"))
    except ValueError:
        return False


def file_issue(title, body):
    if DRY:
        print("DRY  would file: %s\n%s\n" % (title, body))
        return "(dry run)"
    gh("label", "create", "update-available", "--repo", REPO, "--color", "0E8A16",
       "--description", "A dependency or upstream moved", "--force", check=False)
    return gh("issue", "create", "--repo", REPO, "--title", title, "--body", body,
              "--label", "update-available").strip()


def notify(title, message, click):
    url, kind = os.environ.get("NOTIFY_URL", ""), (os.environ.get("NOTIFY_KIND") or "ntfy").lower()
    if not url:
        return
    if DRY:
        print("DRY  would notify (%s): %s - %s" % (kind, title, message))
        return
    try:
        if kind == "ntfy":
            req = urllib.request.Request(url, data=message.encode(), method="POST", headers=dict(
                UA, Title=title, Click=click, Tags="package", Priority="default"))
        else:
            shape = {"discord": {"content": "**%s**\n%s\n%s" % (title, message, click)},
                     "slack": {"text": "*%s*\n%s\n%s" % (title, message, click)},
                     "gotify": {"title": title, "message": message + "\n" + click, "priority": 5}
                     }.get(kind, {"title": title, "message": message, "url": click})
            req = urllib.request.Request(url, data=json.dumps(shape).encode(), method="POST",
                                         headers=dict(UA, **{"Content-Type": "application/json"}))
        urllib.request.urlopen(req, timeout=20).read()
        print("notified via %s" % kind)
    except Exception as exc:
        print("warning: notification failed: %s" % exc, file=sys.stderr)


def main():
    filed, errors = [], []
    for name, fn in CHECKS:
        try:
            findings = fn()
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
            print("check %s failed: %s" % (name, exc), file=sys.stderr)
            continue
        for key, detail, action in findings:
            title = "[update] " + key
            if known(title):
                print("known    %s" % title)
                continue
            body = ("%s\n\n**Next step:** %s\n\nPublish: https://github.com/%s/actions/workflows/release.yml\n\n"
                    "Close this issue once it is dealt with; closing it is what stops the nudge, "
                    "and a closed issue still stops it being filed twice.\n" % (detail, action, REPO))
            file_issue(title, body)
            filed.append((key, detail, action))
            print("filed    %s" % title)
    if filed:
        lines = ["%s" % d for _, d, _ in filed]
        notify("Backblaze container: %d update%s waiting" % (len(filed), "" if len(filed) == 1 else "s"),
               "\n".join(lines) + "\n\nWhen you are ready: Release workflow.",
               "https://github.com/%s/actions/workflows/release.yml" % REPO)
    if not filed and not errors:
        print("nothing new")
    if errors and len(errors) == len(CHECKS):
        sys.exit("every check failed: " + "; ".join(errors))


if __name__ == "__main__":
    main()
