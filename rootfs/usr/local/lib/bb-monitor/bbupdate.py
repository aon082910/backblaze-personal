# Update notices: is there a newer image, a newer Wine, a newer Backblaze client?
#
# Three questions, asked once a day (UPDATE_CHECK_INTERVAL_HOURS), answered from public
# endpoints that carry only version numbers:
#
#   image   Docker Hub's record of this image's tag. A floating tag (latest, beta...)
#           is newer when it was pushed after this build was made. A version tag
#           (v10.2.2-aon.1) is newer when a later version tag exists.
#   wine    the Wine in this container against WineHQ's stable (for the image that
#           uses WineHQ's Wine) or against the newest Wine release (for the patched
#           images, which are rebuilt by the maintainer, so this is a heads-up that a
#           rebuild is coming, not something the container can do).
#   client  the installed Backblaze client against the one Backblaze is serving.
#
# Each answer is shown on the Container tab. A change in an answer ("a newer image
# appeared", "Wine 11.20 is out") is also sent once through the notification endpoints
# set up on the Settings tab, as the events image_update, wine_update and client_update.
# Which values were already announced is remembered on disk, so a restart does not repeat
# them. Docker Hub or WineHQ being unreachable is not an answer; it leaves the last one
# in place and says when it last succeeded.
#
# Nothing here updates anything. Updating the image is Unraid's job (the Docker tab
# shows "update ready"); this says that it is worth looking.

import gzip, json, os, re, subprocess, tempfile, threading, time
import urllib.error, urllib.request

import bbapi

STATE = bbapi.DIR + "/update-state.json"
BUILD = "/etc/bb-build"
CLIENT_VERSION = "/config/wine/dosdevices/c:/ProgramData/Backblaze/bzdata/bzreports/bzserv_version.txt"
HUB = "https://hub.docker.com/v2/repositories/%s/tags"
UA = {"User-Agent": "bb64-container-update-check"}
TIMEOUT = 12
FIRST_CHECK_DELAY = 300          # let the network and the client come up first
SLACK = 3 * 3600                 # a push within this long of the build is the same build

_lock = threading.Lock()


def repo():
    """The Docker Hub repository this image is published to."""
    return os.environ.get("AON_IMAGE_REPO", "allornothing/backblaze-personal")


def enabled():
    return os.environ.get("UPDATE_CHECK", "true").strip().lower() not in ("false", "0", "no", "off")


def interval():
    try:
        return max(1, min(168, int(os.environ.get("UPDATE_CHECK_INTERVAL_HOURS", "24")))) * 3600
    except ValueError:
        return 24 * 3600


def build_stamp():
    out = {}
    try:
        with open(BUILD, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except OSError:
        pass
    return out


def _vkey(v):
    parts = [int(x) for x in re.findall(r"\d+", str(v))[:5]]
    while len(parts) > 1 and parts[-1] == 0:      # 11.0 and 11.0.0.0 are the same Wine
        parts.pop()
    return tuple(parts)


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _epoch(iso):
    """Seconds since the epoch for 2026-10-11T08:00:00Z, or None."""
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return None


# ---- the three checks -------------------------------------------------------------

# Which published tag each build variant is. The build stamp's variant is not the tag.
CHANNEL = {"ubuntu24": "latest", "ubuntu24-patched": "latest-patched", "ubuntu26": "ubuntu26",
           "beta": "beta"}
SUFFIX = {"ubuntu24": "", "ubuntu24-patched": "-patched", "ubuntu26": "-ubuntu26", "beta": ""}
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-aon\.(\d+))?(-patched|-ubuntu26)?$")


def _hub(url):
    """Docker Hub's JSON, or None when the repository or tag is not there (404): an image
    nobody has pushed yet is a finding to show, not an error."""
    try:
        return json.loads(_get(url))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def check_image(stamp=None, fetch=None):
    stamp = stamp if stamp is not None else build_stamp()
    fetch = fetch or _hub
    variant = stamp.get("variant", "")
    running = stamp.get("version", "unknown")
    out = {"repo": repo(), "running": running, "variant": variant, "built": stamp.get("built"),
           "channel": CHANNEL.get(variant), "latest": None, "update": False, "note": ""}
    if not variant or variant not in CHANNEL:
        out["note"] = "This is a local or unlabelled build, so there is nothing to compare."
        return out
    m = VERSION_TAG.match(running)
    if m:
        # A version tag: newer means a later version tag with the same suffix.
        data = fetch((HUB % repo()) + "?page_size=100&ordering=last_updated")
        if data is None:
            out["note"] = "Nothing is published as %s on Docker Hub yet." % repo()
            return out
        suffix = SUFFIX[variant]
        best = None
        for t in data.get("results", []):
            tm = VERSION_TAG.match(t.get("name", ""))
            if tm and (tm.group(5) or "") == suffix:
                key = tuple(int(x or 0) for x in tm.groups()[:4])
                if best is None or key > best[0]:
                    best = (key, t["name"], t.get("last_updated"))
        have = tuple(int(x or 0) for x in m.groups()[:4])
        if best:
            out["latest"] = best[1]
            out["latest_pushed"] = best[2]
            out["update"] = best[0] > have
            out["note"] = ("%s is out; this container runs %s." % (best[1], running)) if out["update"] \
                else "This is the newest version tag."
        return out
    # A floating tag: newer means pushed after this build was made.
    tag = out["channel"]
    data = fetch((HUB % repo()) + "/" + tag)
    if data is None:
        out["note"] = "Nothing is published as %s:%s on Docker Hub yet." % (repo(), tag)
        return out
    pushed = data.get("last_updated")
    out["latest"] = tag
    out["latest_pushed"] = pushed
    pe, be = _epoch(pushed or ""), _epoch(stamp.get("built", ""))
    if pe and be and pe > be + SLACK:
        out["update"] = True
        out["note"] = ":%s was pushed %s, after this container's image was built (%s)." % (
            tag, pushed[:16].replace("T", " ") + " UTC", stamp.get("built", "?")[:16].replace("T", " ") + " UTC")
    else:
        out["note"] = "This is the newest :%s." % tag
    return out


def _wine_running():
    for cmd in (("/opt/wine/bin/wine",), ("wine",)):
        try:
            p = subprocess.run(list(cmd) + ["--version"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"wine-([0-9.]+)", p.stdout)
        if m:
            return m.group(1), cmd[0].startswith("/opt/wine")
    return None, False


def check_wine(stamp=None, running=None, fetch_stable=None, fetch_dev=None):
    stamp = stamp if stamp is not None else build_stamp()
    have, patched = running if running is not None else _wine_running()
    out = {"running": have, "flavour": "patched" if patched else "winehq", "latest": None,
           "update": False, "note": ""}
    if not have:
        out["note"] = "Could not read the Wine version."
        return out
    if patched:
        # The patched images carry a Wine the maintainer builds; this is only a heads-up.
        try:
            tags = (fetch_dev or _wine_dev_tags)()
            newest = max(tags, key=_vkey) if tags else None
        except Exception as exc:
            out["note"] = "Could not reach WineHQ: %s" % exc
            return out
        out["latest"] = newest
        if newest and _vkey(newest) > _vkey(have):
            out["update"] = True
            out["note"] = ("Wine %s is out. This image carries Wine %s with the fork's patches; "
                           "the maintainer rebuilds it after testing, and an image update will follow."
                           % (newest, have))
        else:
            out["note"] = "This is the newest Wine release."
        return out
    try:
        newest = (fetch_stable or _winehq_stable)()
    except Exception as exc:
        out["note"] = "Could not reach WineHQ: %s" % exc
        return out
    out["latest"] = newest
    if newest and _vkey(newest) > _vkey(have):
        out["update"] = True
        out["note"] = ("WineHQ's stable Wine is %s; this image carries %s. A rebuilt image "
                       "will follow." % (newest, have))
    else:
        out["note"] = "This is WineHQ's current stable Wine."
    return out


def _winehq_stable():
    raw = gzip.decompress(_get("https://dl.winehq.org/wine-builds/ubuntu/dists/noble/main/"
                               "binary-amd64/Packages.gz", 25)).decode("utf-8", "replace")
    m = re.search(r"^Package: winehq-stable\n(?:.*\n)*?Version: (\S+)", raw, re.M)
    return m.group(1).split("~")[0] if m else None


def _wine_dev_tags():
    data = json.loads(_get("https://gitlab.winehq.org/api/v4/projects/wine%2Fwine/repository/tags"
                           "?search=%5Ewine-1&order_by=updated&sort=desc&per_page=30"))
    return [t["name"][5:] for t in data if re.fullmatch(r"wine-\d+\.\d+", t["name"])]


def check_client(installed=None, fetch=None):
    out = {"installed": None, "available": None, "update": False, "note": ""}
    if installed is None:
        try:
            with open(CLIENT_VERSION, encoding="utf-8", errors="replace") as fh:
                installed = fh.read().strip() or None
        except OSError:
            installed = None
    out["installed"] = installed
    avail = None
    for mirror in ("ca000", "ca001", "ca002"):
        try:
            xml = (fetch or (lambda u: _get(u, 15).decode("utf-8", "replace")))(
                "https://%s.backblaze.com/api/clientversion.xml" % mirror)
        except Exception:
            continue
        m = re.search(r'<update win32_version="([0-9.]+)"', xml)
        if m:
            avail = m.group(1)
            break
    out["available"] = avail
    if not avail:
        out["note"] = "Could not reach Backblaze's version service."
    elif not installed:
        out["note"] = "No client is installed yet. Backblaze is serving %s; it installs at start." % avail
    elif _vkey(avail) > _vkey(installed):
        out["update"] = True
        pin = os.environ.get("BACKBLAZE_VERSION", "").strip()
        out["note"] = ("Backblaze is serving %s. %s" % (
            avail, ("This container is pinned to %s, so it will stay on it." % pin) if pin else
            "It installs at the next start unless the client is pinned or updates are off."))
    else:
        out["note"] = "The installed client is the newest Backblaze is serving."
    return out


# ---- running them, remembering, telling ---------------------------------------------

def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc):
    with bbapi._mutate():
        fd, tmp = tempfile.mkstemp(dir=bbapi.DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, sort_keys=True)
            os.chmod(tmp, 0o600)
            bbapi._own_like_config(tmp)
            os.replace(tmp, STATE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def run_checks():
    """One round of all three, side by side so the page's Check now cannot outlast the
    web proxy's timeout. A check that raises keeps its previous answer."""
    doc = load()
    result = {"at": int(time.time())}
    found = {}

    def work(name, fn):
        try:
            found[name] = (fn(), None)
        except Exception as exc:
            found[name] = (None, str(exc)[:200])

    threads = [threading.Thread(target=work, args=(n, f), daemon=True)
               for n, f in (("image", check_image), ("wine", check_wine), ("client", check_client))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    for name in ("image", "wine", "client"):
        answer, error = found.get(name, (None, "timed out"))
        if answer is not None:
            result[name] = answer
            result[name]["ok_at"] = result["at"]
        else:
            result[name] = dict(doc.get("result", {}).get(name) or {}, error=error)
    return result


def announce(result, doc, fire):
    """Send each newly found update once. `doc` holds what was already announced."""
    told = doc.setdefault("told", {})
    msgs = []
    img, wine, cl = result.get("image") or {}, result.get("wine") or {}, result.get("client") or {}
    if img.get("update") and img.get("latest_pushed") != told.get("image"):
        msgs.append(("image_update", "Container image update available",
                     (img.get("note") or "A newer image is published.") +
                     " Update it from the Docker tab.", "image", img.get("latest_pushed")))
    if wine.get("update") and wine.get("latest") != told.get("wine"):
        msgs.append(("wine_update", "Newer Wine available", wine.get("note", ""), "wine", wine.get("latest")))
    if cl.get("update") and cl.get("available") != told.get("client"):
        msgs.append(("client_update", "Newer Backblaze client available", cl.get("note", ""),
                     "client", cl.get("available")))
    for key, title, message, slot, marker in msgs:
        fire(key, title, message)
        told[slot] = marker
    return [m[0] for m in msgs]


def check_now(fire=None):
    """Run the checks, store the answers, announce new findings. Returns the answers."""
    with _lock:
        doc = load()
        result = run_checks()
        first = "told" not in doc
        fired = []
        if fire is not None:
            if first:
                # The first run on a container records the baseline. An update that was
                # already waiting is shown on the page, not announced as news.
                told = doc.setdefault("told", {})
                for slot, val in (("image", (result.get("image") or {}).get("latest_pushed")),
                                  ("wine", (result.get("wine") or {}).get("latest")),
                                  ("client", (result.get("client") or {}).get("available"))):
                    told[slot] = val
            else:
                fired = announce(result, doc, fire)
        doc["result"] = result
        doc["fired"] = fired
        try:
            _save(doc)
        except OSError:
            pass
        return result


def snapshot():
    doc = load()
    return {"enabled": enabled(), "interval_hours": interval() // 3600, "result": doc.get("result"),
            "repo": repo(), "stamp": build_stamp()}


def loop(fire):
    """The service thread: one check shortly after start, then one per interval."""
    time.sleep(FIRST_CHECK_DELAY)
    while True:
        if enabled():
            try:
                check_now(fire)
            except Exception as exc:        # never let the thread die
                print("bbupdate: check failed: %s" % exc, flush=True)
        time.sleep(interval())
