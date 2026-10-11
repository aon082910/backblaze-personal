#!/usr/bin/env python3
"""Tests for the Container tab's backend: the settings registry and its validation,
the entrypoint's `emit` (stored values over Docker's variables), the page's state
and save, and the update checks and their once-only announcements.

The cases are the ones where a silent regression costs something. A validator that
lets a newline through writes a second line into a shell `export`. An `emit` that
trusts the store runs whatever a hand-edited file says. A state that returns a
secret hands the web login password to whoever opens the page. An announcement that
forgets what it said sends the same notice every time the service restarts.

Nothing here touches /config, /etc or the network. Every path is repointed at a
temporary tree, and the update checks take their fetchers as arguments.

Run:  python3 tests/test-container.py
"""
import io, json, os, shlex, stat, subprocess, sys, tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
LIB = os.path.join(ROOT, "rootfs", "usr", "local", "lib", "bb-monitor")
sys.path.insert(0, LIB)

FIX = tempfile.mkdtemp(prefix="bb-test-container-")
FAIL = []
COUNT = [0]


def ok(cond, msg):
    COUNT[0] += 1
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError:
        return True
    return False


import bbapi
bbapi.DIR = os.path.join(FIX, "bb-api")
bbapi.LOCK = os.path.join(bbapi.DIR, ".lock")
import bbcontainer as C

B = C.BY_VAR

# ---- the registry ------------------------------------------------------------------
vars_ = [r["var"] for r in C.REGISTRY]
ok(len(vars_) == len(set(vars_)), "every setting is listed once")
ok(all(r["group"] in C.GROUP_ORDER for r in C.REGISTRY), "every setting belongs to a known group")
bad = []
for r in C.REGISTRY:
    if r["kind"] == "secret":
        continue
    for label, v in (("default", r["default"]), ("unraid", r["unraid"])):
        try:
            C.normalise(r, v)
        except ValueError as exc:
            bad.append("%s %s=%r: %s" % (r["var"], label, v, exc))
ok(not bad, "each setting's image default and Unraid default pass its own validation" +
   ("" if not bad else ": " + "; ".join(bad)))
ok(B["USER_ID"]["unraid"] == "99" and B["GROUP_ID"]["unraid"] == "100",
   "the Unraid template runs as nobody:users (99:100)")
ok(all(r["kind"] in ("bool01", "booltf", "int", "enum", "text", "secret") for r in C.REGISTRY),
   "every kind is one the validator knows")

# ---- normalise ---------------------------------------------------------------------
ok(C.normalise(B["DARK_MODE"], "true") == "1" and C.normalise(B["DARK_MODE"], False) == "0",
   "a 1/0 boolean takes any common spelling and is stored as 1 or 0")
ok(C.normalise(B["FORCE_LATEST_UPDATE"], "1") == "true" and C.normalise(B["FORCE_LATEST_UPDATE"], "off") == "false",
   "a true/false boolean (the image's own spelling) is stored as true or false, which startapp.sh compares exactly")
ok(raises(C.normalise, B["DARK_MODE"], "maybe"), "a boolean refuses a word that is not a yes or a no")
ok(C.normalise(B["USER_ID"], " 99 ") == "99", "an integer is trimmed")
ok(raises(C.normalise, B["USER_ID"], "0"), "user ID 0 is refused: the app must not run as root")
ok(raises(C.normalise, B["USER_ID"], "99; id"), "an integer refuses trailing text")
ok(raises(C.normalise, B["DISPLAY_WIDTH"], "100"), "an integer below its floor is refused")
ok(raises(C.normalise, B["APP_NICENESS"], "20") and C.normalise(B["APP_NICENESS"], "-20") == "-20",
   "niceness accepts -20 and refuses 20")
ok(C.normalise(B["SECURE_CONNECTION_VNC_METHOD"], "TLS") == "TLS" and
   raises(C.normalise, B["SECURE_CONNECTION_VNC_METHOD"], "plain"), "an enum takes only its choices")
ok(C.normalise(B["UMASK"], "0022") == "0022" and raises(C.normalise, B["UMASK"], "0899"),
   "UMASK is octal")
ok(C.normalise(B["TZ"], "America/New_York") == "America/New_York" and raises(C.normalise, B["TZ"], "a b"),
   "a time zone is a tz name, not free text")
ok(C.normalise(B["BACKBLAZE_VERSION"], "") == "" and C.normalise(B["BACKBLAZE_VERSION"], "10.0.1.1069") == "10.0.1.1069"
   and raises(C.normalise, B["BACKBLAZE_VERSION"], "latest"), "the client pin is empty or four dotted numbers")
for evil in ("a\nexport X=1", "a\rb", "a\x00b"):
    ok(raises(C.normalise, B["TZ"], evil) and raises(C.normalise, B["WEB_AUTHENTICATION_USERNAME"], evil),
       "a value with a line break or NUL is refused (%r)" % evil)
ok(raises(C.normalise, B["VNC_PASSWORD"], ""), "a secret cannot be stored empty")

# ---- interlocks --------------------------------------------------------------------
e, w = C.interlocks({"DISPLAY_WIDTH": "902"})
ok(any("divisible by 4" in x for x in e), "a screen width not divisible by 4 is an error")
e, w = C.interlocks({"WEB_AUTHENTICATION": "1", "WEB_AUTHENTICATION_USERNAME": "",
                     "WEB_AUTHENTICATION_PASSWORD": "", "SECURE_CONNECTION": "1"})
ok(any("username and a password" in x for x in e), "the web login without credentials is an error (it would lock the page)")
e, w = C.interlocks({"WEB_AUTHENTICATION": "1", "WEB_AUTHENTICATION_USERNAME": "joe",
                     "WEB_AUTHENTICATION_PASSWORD": "x", "SECURE_CONNECTION": "0"})
ok(any("encrypted connection" in x for x in e), "the web login without TLS or the insecure allowance is an error")
e, w = C.interlocks({"WEB_AUTHENTICATION": "1", "WEB_AUTHENTICATION_USERNAME": "joe",
                     "WEB_AUTHENTICATION_PASSWORD": "x", "SECURE_CONNECTION": "0",
                     "WEB_AUTHENTICATION_ALLOW_INSECURE": "1"})
ok(not e, "the insecure allowance satisfies the web login's transport rule")
e, w = C.interlocks({"WEB_TERMINAL": "1"})
ok(any("no web login" in x for x in w), "a terminal with no web login is called out")
e, w = C.interlocks({"APP_NICENESS": "-5"})
ok(any("SYS_NICE" in x for x in w), "a negative niceness names the capability it needs")

# ---- emit --------------------------------------------------------------------------
store = os.path.join(FIX, "store.json")
applied_path = os.path.join(FIX, "applied.json")


def write_store(settings):
    with open(store, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "settings": settings}, fh)


def run_emit(environ, settings=None):
    if settings is not None:
        write_store(settings)
    elif os.path.exists(store):
        os.unlink(store)
    out, err = io.StringIO(), io.StringIO()
    C.emit(environ=environ, store=store, applied_path=applied_path, out=out, err=err)
    lines = [l for l in out.getvalue().splitlines() if l]
    return lines, err.getvalue(), json.load(open(applied_path, encoding="utf-8"))


lines, err, rec = run_emit({"USER_ID": "1000", "TZ": "Etc/UTC"}, {"TZ": "America/Chicago", "USER_ID": "99"})
ok("export TZ=America/Chicago" in lines and "export USER_ID=99" in lines,
   "a stored value is exported over the one Docker gave")
ok(rec["vars"]["TZ"]["origin"] == "setting" and rec["vars"]["TZ"]["docker"] == "Etc/UTC",
   "the applied record keeps what Docker gave, so the page can offer 'follow the container'")
ok(rec["vars"]["DISPLAY_WIDTH"]["origin"] == "container", "a setting with nothing stored is recorded as the container's")

lines, err, rec = run_emit({"USER_ID": "1000"}, {})
ok(lines == [], "an empty store exports nothing")

lines, err, rec = run_emit({"PUID": "99", "PGID": "100", "USER_ID": "1000", "GROUP_ID": "1000"}, {})
ok("export USER_ID=99" in lines and "export GROUP_ID=100" in lines and rec["vars"]["USER_ID"]["origin"] == "alias",
   "PUID and PGID stand in for USER_ID and GROUP_ID")
lines, err, rec = run_emit({"PUID": "99", "USER_ID": "1000"}, {"USER_ID": "568"})
ok("export USER_ID=568" in lines and "export USER_ID=99" not in lines, "a value saved on the page beats PUID")
lines, err, rec = run_emit({"PUID": "root"}, {})
ok(lines == [] and "PUID ignored" in err, "a bad PUID is skipped with a message, not exported")

lines, err, rec = run_emit({"BB_IGNORE_UI_SETTINGS": "1", "TZ": "Etc/UTC"}, {"TZ": "America/Chicago"})
ok(lines == [] and rec["ignored"] is True and "BB_IGNORE_UI_SETTINGS" in err,
   "BB_IGNORE_UI_SETTINGS=1 skips the store: the way back in after a bad choice")

lines, err, rec = run_emit({}, {"TZ": "bad zone", "NOT_A_SETTING": "1", "USER_ID": "0", "DARK_MODE": "1"})
ok(lines == ["export DARK_MODE=1"], "stored values that fail validation are skipped; valid ones still apply")
ok("TZ" in err and "NOT_A_SETTING" in err and "User ID" in err, "each skipped value is named in the log")

# The store is a file on the user's share. Whatever is in it, the output must be inert shell.
nasty = "x'; touch /tmp/pwned; echo '"
write_store({"WEB_AUTHENTICATION_USERNAME": nasty})
lines, err, rec = run_emit({}, {"WEB_AUTHENTICATION_USERNAME": nasty})
ok(lines == [], "a username with shell metacharacters fails its pattern and is not exported")
lines, err, rec = run_emit({}, {"WEB_FILE_MANAGER_DENIED_PATHS": "/a b/c's;d"})
ok(lines and shlex.split(lines[0])[1] == "WEB_FILE_MANAGER_DENIED_PATHS=/a b/c's;d",
   "what is exported is quoted so that the shell reads back exactly the stored text")
r = subprocess.run(["sh", "-c", "eval '%s'; printf %%s \"$WEB_FILE_MANAGER_DENIED_PATHS\"" % lines[0].replace("'", "'\\''")],
                   capture_output=True, text=True)
ok(r.stdout == "/a b/c's;d", "and eval gives the text back, with the `;` and the quote inert")

lines, err, rec = run_emit({"VNC_PASSWORD": "from-docker"}, {"WEB_AUTHENTICATION_PASSWORD": "s3cret-pw"})
flat = json.dumps(rec)
ok("s3cret-pw" not in flat and "from-docker" not in flat, "the applied record never holds a secret's value")
ok(rec["vars"]["WEB_AUTHENTICATION_PASSWORD"]["set"] is True and rec["vars"]["VNC_PASSWORD"]["set"] is True,
   "it records that a secret is set")
mode = stat.S_IMODE(os.stat(applied_path).st_mode)
ok(mode == 0o644, "the applied record is world-readable (the web service is not root) and holds no secret")

# ---- state and save ----------------------------------------------------------------
C.STORE = store
C.APPLIED = applied_path
if os.path.exists(store):
    os.unlink(store)
run_emit({"TZ": "Etc/UTC", "USER_ID": "99", "DISPLAY_WIDTH": "900"}, {})
S = C.state(store_path=store)
by = {s["var"]: s for s in S["settings"]}
ok(by["TZ"]["source"] == "container" and not by["TZ"]["pending"], "with nothing stored a setting follows the container and is not pending")
ok(S["pending"] is False, "no pending changes on a fresh container")

try:
    C.save({"TZ": "America/Denver", "DISPLAY_WIDTH": "1280"}, store_path=store)
    saved = True
except Exception as exc:
    saved = False
    print("   save raised:", exc)
ok(saved, "a valid save is accepted")
S = C.state(store_path=store)
by = {s["var"]: s for s in S["settings"]}
ok(by["TZ"]["value"] == "America/Denver" and by["TZ"]["running"] == "Etc/UTC" and by["TZ"]["pending"] is True,
   "a saved value shows as the next start's value, differs from what is running, and is pending")
ok(by["TZ"]["source"] == "setting" and by["TZ"]["container_value"] == "Etc/UTC", "and it says where it came from")
ok(S["pending"] is True, "the page is told a restart is needed")
ok(stat.S_IMODE(os.stat(store).st_mode) == 0o600, "the store is owner-only: it can hold the web password")

C.save({"TZ": None}, store_path=store)
by = {s["var"]: s for s in C.state(store_path=store)["settings"]}
ok(by["TZ"]["source"] == "container" and by["TZ"]["pending"] is False, "follow-the-container removes the stored value")

ok(raises(C.save, {"NOPE": "1"}, store_path=store), "an unknown setting name is refused")
ok(raises(C.save, {"USER_ID": "0"}, store_path=store), "an invalid value is refused and nothing is written")
ok(raises(C.save, {}, store_path=store), "an empty save is refused")
ok(raises(C.save, {"WEB_AUTHENTICATION": "1"}, store_path=store),
   "turning the web login on without credentials is refused, so this page cannot lock itself out")
ok(raises(C.save, {"DISPLAY_WIDTH": "902"}, store_path=store), "a width not divisible by 4 is refused")

C.save({"WEB_AUTHENTICATION": "1", "WEB_AUTHENTICATION_USERNAME": "joe", "WEB_AUTHENTICATION_PASSWORD": "hunter22",
        "SECURE_CONNECTION": "1"}, store_path=store)
S = C.state(store_path=store)
dump = json.dumps(S)
ok("hunter22" not in dump, "the state sent to the page never contains the password")
by = {s["var"]: s for s in S["settings"]}
ok(by["WEB_AUTHENTICATION_PASSWORD"]["set"] is True and "value" not in by["WEB_AUTHENTICATION_PASSWORD"],
   "a secret is reported as set or not, with no value")
C.save({"WEB_AUTHENTICATION_PASSWORD": ""}, store_path=store)
ok(json.load(open(store))["settings"]["WEB_AUTHENTICATION_PASSWORD"] == "hunter22",
   "a blank secret leaves the stored one alone")
ok(raises(C.save, {"WEB_AUTHENTICATION_USERNAME": None}, store_path=store),
   "removing the username while the login is on is refused")

# a hand-edited store with garbage in it must not break the page
with open(store, "w") as fh:
    fh.write("{not json")
S = C.state(store_path=store)
ok(isinstance(S["settings"], list) and S["pending"] in (True, False), "a corrupt store reads as empty rather than breaking the page")

# ---- the update checks -------------------------------------------------------------
import bbupdate as U

stamp = {"variant": "ubuntu24", "version": "latest", "built": "2026-10-01T00:00:00Z"}
img = U.check_image(stamp, fetch=lambda u: {"last_updated": "2026-10-05T12:00:00.000000Z"})
ok(img["update"] is True and img["channel"] == "latest", "a floating tag pushed after the build is an update")
img = U.check_image(stamp, fetch=lambda u: {"last_updated": "2026-10-01T01:00:00.000000Z"})
ok(img["update"] is False, "a push within a few hours of the build is the same build, not an update")
img = U.check_image(stamp, fetch=lambda u: None)
ok(img["update"] is False and "yet" in img["note"] and "error" not in img, "a repository nobody has pushed to is a note, not an error")
c = U.check_client(installed=None, fetch=lambda u: '<update win32_version="10.0.3.1075" />')
ok(c["update"] is False and "No client is installed" in c["note"], "no client installed yet is not reported as up to date")
img = U.check_image({"variant": "", "version": "dev"}, fetch=lambda u: 1 / 0)
ok(img["update"] is False and "local" in img["note"], "a local build is not compared with anything")
tags = {"results": [{"name": "v10.2.2-aon.1", "last_updated": "a"}, {"name": "v10.2.2-aon.2", "last_updated": "b"},
                    {"name": "v10.2.2-aon.2-ubuntu26", "last_updated": "c"}, {"name": "latest", "last_updated": "d"},
                    {"name": "v10.3.0-patched", "last_updated": "e"}]}
img = U.check_image({"variant": "ubuntu24", "version": "v10.2.2-aon.1", "built": "2026-10-01T00:00:00Z"}, fetch=lambda u: tags)
ok(img["update"] is True and img["latest"] == "v10.2.2-aon.2", "a version tag is compared with the newest version tag of the same kind")
img = U.check_image({"variant": "ubuntu24-patched", "version": "v10.2.2-aon.1-patched", "built": "x"}, fetch=lambda u: tags)
ok(img["update"] is True and img["latest"] == "v10.3.0-patched", "the -patched flavour only sees -patched tags")
img = U.check_image({"variant": "ubuntu24", "version": "v10.2.2-aon.2", "built": "x"}, fetch=lambda u: tags)
ok(img["update"] is False, "the newest version tag is up to date")

w = U.check_wine(running=("11.0", False), fetch_stable=lambda: "11.0.1")
ok(w["update"] is True and w["flavour"] == "winehq", "WineHQ's stable moving on is an update for the stable image")
w = U.check_wine(running=("11.0", False), fetch_stable=lambda: "11.0.0.0")
ok(w["update"] is False, "11.0 and 11.0.0.0 are the same Wine")
w = U.check_wine(running=("11.19", True), fetch_dev=lambda: ["11.17", "11.20", "11.19"])
ok(w["update"] is True and w["latest"] == "11.20" and "rebuilds" in w["note"], "the patched image reports a newer Wine as a heads-up that a rebuild follows")
w = U.check_wine(running=("11.19", True), fetch_dev=lambda: 1 / 0)
ok(w["update"] is False and "Could not reach" in w["note"], "WineHQ being unreachable is a note, not a crash")

xml = '<clientversion><update win32_version="10.0.3.1075" /></clientversion>'
c = U.check_client(installed="10.0.1.1069", fetch=lambda u: xml)
ok(c["update"] is True and c["available"] == "10.0.3.1075", "a client newer than the installed one is an update")
os.environ["BACKBLAZE_VERSION"] = "10.0.1.1069"
c = U.check_client(installed="10.0.1.1069", fetch=lambda u: xml)
ok("pinned" in c["note"], "a pinned container is told it will stay on its pin")
del os.environ["BACKBLAZE_VERSION"]
c = U.check_client(installed="10.0.3.1075", fetch=lambda u: xml)
ok(c["update"] is False, "the newest client is up to date")

# announcements: once, and not on the first run
U.STATE = os.path.join(bbapi.DIR, "update-state.json")
sent = []
fire = lambda k, t, m: sent.append(k)
fake = {"at": 1760000000, "image": {"update": True, "latest_pushed": "T1", "note": "n"},
        "wine": {"update": True, "latest": "11.20", "note": "n"},
        "client": {"update": True, "available": "10.0.4.1", "note": "n"}}
U.run_checks = lambda: json.loads(json.dumps(fake))
U.check_now(fire)
ok(sent == [], "the first run records a baseline and announces nothing: an update already waiting is shown, not announced")
U.check_now(fire)
ok(sent == [], "a second run with the same findings still announces nothing")
fake["wine"]["latest"] = "11.21"
U.check_now(fire)
ok(sent == ["wine_update"], "a newer Wine than the one last seen is announced")
U.check_now(fire)
ok(sent == ["wine_update"], "and only once")
fake["image"]["latest_pushed"] = "T2"
fake["client"]["available"] = "10.0.5.1"
U.check_now(fire)
ok(sent == ["wine_update", "image_update", "client_update"], "an image push and a client release are each announced")
saved = U.load()
ok(saved.get("result", {}).get("at") is not None, "the last answers are kept on disk for the page")

import bbnotify
keys = [e[0] for e in bbnotify.EVENTS]
ok(all(k in keys for k in ("image_update", "wine_update", "client_update")), "the three update events exist in the notification table")
ok(bbnotify.default()["events"]["image_update"] is True, "and are on by default")

# ---- the entrypoint ----------------------------------------------------------------
ep = os.path.join(ROOT, "rootfs", "usr", "local", "lib", "bb-entrypoint.sh")
ok(subprocess.run(["sh", "-n", ep]).returncode == 0, "bb-entrypoint.sh parses under sh")
body = open(ep, encoding="utf-8").read()
ok('exec "$@"' in body, "the entrypoint hands over to the command it was given (/init)")
ok("python3 -I" in body, "and runs the emitter isolated: nothing from /config is importable before the container is up")
ok(subprocess.run(["sh", ep, "true"], capture_output=True).returncode == 0,
   "run where the module's paths do not exist, it fails open and still runs the command")

# ---- the page ----------------------------------------------------------------------
import bbcontainer_page as P
page = P.html("/*theme*/", "/*page*/")
ok("@@" not in page, "every template placeholder in the page is filled")
ok("manage/container" in page and "manage/updates" in page, "the page talks to the container and update routes")
ok("innerHTML" not in page, "the page never builds markup from strings: a setting's text cannot become HTML")
ok("type:'password'" in page or 'type: "password"' in page or "type:'password'" in page.replace(" ", ""), "a secret is entered in a password field")

# ---- the Unraid template -------------------------------------------------------------
import importlib.util
import xml.etree.ElementTree as ET
spec = importlib.util.spec_from_file_location("gen_template", os.path.join(ROOT, "tools", "gen_template.py"))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
fresh = G.build()
committed = open(os.path.join(ROOT, "templates", "backblaze-personal.xml"), encoding="utf-8").read()
ok(fresh == committed, "the committed template is what the generator makes from the registry (run tools/gen_template.py)")
tpl = ET.fromstring(fresh)
cfgs = tpl.findall("Config")
targets = [c.get("Target") for c in cfgs]
ok(len(targets) == len(set(targets)), "no Config appears twice in the template")
ok(all(r["var"] in targets for r in C.REGISTRY), "every setting in the registry is a field in the template")
bydef = {c.get("Target"): c for c in cfgs}
ok(all((bydef[r["var"]].text or "") == r["unraid"] for r in C.REGISTRY),
   "each field starts at the registry's Unraid default")
ok(all(bydef[r["var"]].get("Mask") == "true" for r in C.REGISTRY if r["kind"] == "secret"), "secrets are masked in the template")
ok(bydef["USER_ID"].text == "99" and bydef["GROUP_ID"].text == "100", "the template runs as nobody:users")
ok(bydef["BACKBLAZE_VERSION"].text == "10.0.1.1069", "the template pins the client that completes passes on WineHQ's Wine")
ok(tpl.findtext("ExtraParams") == "--restart=unless-stopped", "the template sets a restart policy, which the Container tab's Restart button needs")
ok(tpl.findtext("Repository").startswith("allornothing/backblaze-personal:"), "the template pulls from your Docker Hub namespace")
ok(bydef["/config"].get("Required") == "true" and bydef["5800"].get("Type") == "Port", "the config path and web port are present and required")
wine = [t for t in targets if t.startswith("WINE_")]
ok(len(wine) == 5, "the five Wine patch switches are in the template")

print()
print("%d checks, %d failures" % (COUNT[0], len(FAIL)))
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
