# The container's own settings, changeable from the Container tab.
#
# Everything the image reads from an environment variable at start (the user and
# group it runs as, the time zone, the screen size, the web login, which Backblaze
# client to run...) is listed here once, with its type, its limits and its text.
# The same list builds the page, validates what the page sends, and generates the
# Unraid template (tools/gen-template.py), so the three cannot drift apart.
#
# How a choice made on the page reaches the container. The page stores it in
# /config/bb-api/container-settings.json. At the next start the image's entrypoint
# (bb-entrypoint, which runs before the base image's /init) calls `emit` below,
# which exports every stored value over the one Docker gave the container. So a
# value set on the page wins over the container's variable of the same name, the
# variable is what decides when the page names nothing, and "Follow the container"
# on the page deletes the stored value. Nothing here applies a change to a running
# container: every setting is read at start, so the page says "restart to apply".
#
# Two things have to survive a bad choice, because this page can lock its own user
# out (a web login with a password they mistyped, a port nothing maps):
#   * BB_IGNORE_UI_SETTINGS=1 on the container makes the entrypoint ignore the
#     store for that start, and nothing else.
#   * a stored value that fails validation is skipped with a message in the
#     container log, not exported. The store is a file on the user's share, so it
#     is never trusted to be well formed.
#
# Secrets (the VNC password, the web login password) are write-only: the page can
# set or clear one, never read one back, and the applied record keeps a short hash
# of it so "restart needed" can be told without keeping the value.
#
# `emit` runs under `python3 -I` before anything else is up, so this module imports
# nothing outside the standard library at load; bbapi is imported only where a
# write needs its lock.

import hashlib, json, os, re, shlex, sys, tempfile, time

STORE = "/config/bb-api/container-settings.json"
# Not /tmp or /run: the base image empties both during init, after the entrypoint.
APPLIED = "/etc/bb64-applied.json"
RESTART_REQUEST = "/tmp/.bb-restart-request"

GROUPS = (
    ("identity", "Identity and files",
     "Who the app runs as and how the files it makes are owned. On Unraid that is user 99 (nobody) "
     "and group 100 (users)."),
    ("display", "Display",
     "The virtual screen the Backblaze window is drawn on."),
    ("client", "Backblaze client",
     "Which client runs and whether it is allowed to update itself."),
    ("web", "Web interface",
     "How the desktop in your browser behaves."),
    ("security", "Security",
     "Who can open the desktop. Read the warnings: a wrong choice here can lock you out of this page."),
    ("runtime", "Runtime",
     "How the container starts, runs and stops."),
    ("updates", "Update notices",
     "This container checks daily for a newer image, Wine and Backblaze client and tells you through "
     "the notification endpoints in Settings."),
)

# kind: bool01 (1/0, the base image's spelling), booltf (true/false, this image's),
# int, enum, text, secret.
# default: what the image sets. unraid: what the Unraid template sets, when different.
# shown: where the generated template puts it (always, advanced).
REGISTRY = []


def _add(var, group, kind, default, title, summary, warning="", lo=None, hi=None, choices=None,
         pattern=None, unraid=None, shown="advanced", maxlen=256, multiple=None):
    REGISTRY.append({
        "var": var, "group": group, "kind": kind, "default": default,
        "unraid": default if unraid is None else unraid, "title": title, "summary": summary,
        "warning": warning, "lo": lo, "hi": hi, "choices": choices, "pattern": pattern,
        "shown": shown, "maxlen": maxlen, "multiple": multiple,
    })


# ---- identity ---------------------------------------------------------------------
_add("USER_ID", "identity", "int", "1000", "User ID",
     "The user ID the app runs as. It must own the files it backs up, because Backblaze writes a "
     ".bzvol folder into the root of every drive. Find yours with: id -u",
     "Not 0. Files already in /config keep their old owner until the container takes ownership at start.",
     lo=1, hi=65534, unraid="99", shown="always")
_add("GROUP_ID", "identity", "int", "1000", "Group ID",
     "The group ID the app runs as. Find yours with: id -g", lo=1, hi=65534, unraid="100", shown="always")
_add("SUP_GROUP_IDS", "identity", "text", "", "Supplementary group IDs",
     "Comma-separated extra groups, for a share that is group-readable only. Empty for none.",
     pattern=r"[0-9]+(,[0-9]+)*|")
_add("UMASK", "identity", "text", "0022", "File creation mask",
     "Octal mask for files the app creates. 0022 is readable by everyone, writable by the owner; "
     "0000 is writable by everyone, the usual Unraid choice.", pattern=r"[0-7]{3,4}")
_add("TZ", "identity", "text", "Etc/UTC", "Time zone",
     "A tz database name such as America/New_York. Backup schedules, quiet hours and log times "
     "follow it.", pattern=r"[A-Za-z0-9_+\-/]{1,64}", shown="always")

# ---- display ----------------------------------------------------------------------
_add("DISPLAY_WIDTH", "display", "int", "900", "Screen width",
     "Width in pixels of the virtual screen. Must be divisible by 4.", lo=400, hi=7680)
_add("DISPLAY_HEIGHT", "display", "int", "700", "Screen height",
     "Height in pixels of the virtual screen. Must be divisible by 4.", lo=300, hi=4320)
_add("DISABLE_VIRTUAL_DESKTOP", "display", "booltf", "false", "Disable Wine's virtual desktop",
     "Draw Backblaze's windows directly on the screen instead of inside one Wine desktop window.")
_add("DARK_MODE", "display", "bool01", "0", "Dark mode",
     "Use a dark theme for the browser desktop's own controls.")
_add("ENABLE_CJK_FONT", "display", "bool01", "0", "Chinese, Japanese and Korean font",
     "Installs the WenQuanYi Zen Hei font at start, for file names in those scripts. Needs internet "
     "access at start.")

# ---- client -----------------------------------------------------------------------
_add("BACKBLAZE_VERSION", "client", "text", "", "Pin the Backblaze client",
     "Run exactly this client version, for example 10.0.1.1069. At each start the container installs "
     "it if a different one is there, newer or older, and skips the update check. Empty lets the two "
     "switches below decide. The stable image pins 10.0.1.1069 because 10.0.3.1075 completes no "
     "backup pass under WineHQ's Wine; the patched and beta images follow Backblaze's newest.",
     "Pinning a client that needs the Wine service-token fix on an image without it will stall backups.",
     pattern=r"|[0-9]+(\.[0-9]+){3}", unraid="10.0.1.1069", shown="always")
_add("FORCE_LATEST_UPDATE", "client", "booltf", "true", "Update the client at every start",
     "Download the newest Backblaze client at each start. Off keeps the installed version and skips "
     "the check.")
_add("DISABLE_AUTOUPDATE", "client", "booltf", "false", "Skip the update check at start",
     "Start faster by not asking Backblaze for a newer client. The client's own updater is separate.")

# ---- web interface ----------------------------------------------------------------
_add("WEB_LISTENING_PORT", "web", "int", "5800", "Web port inside the container",
     "Change only when the container shares the host's network and 5800 is taken. With a bridge "
     "network leave it and change the port mapping instead.", lo=1, hi=65535)
_add("VNC_LISTENING_PORT", "web", "int", "5900", "VNC port inside the container",
     "As above, for VNC clients.", lo=1, hi=65535)
_add("WEB_FILE_MANAGER", "web", "bool01", "0", "File manager in the browser",
     "Adds a file manager to the desktop's side panel, for looking at what is mapped into the "
     "container.", "Anyone who can open the desktop can then browse the allowed paths.")
_add("WEB_FILE_MANAGER_ALLOWED_PATHS", "web", "text", "AUTO", "File manager: allowed paths",
     "AUTO allows the mapped folders. Otherwise a comma-separated list of paths.", maxlen=1024)
_add("WEB_FILE_MANAGER_DENIED_PATHS", "web", "text", "", "File manager: denied paths",
     "Comma-separated paths the file manager must not show.", maxlen=1024)
_add("WEB_TERMINAL", "web", "bool01", "0", "Terminal in the browser",
     "Adds a shell to the desktop's side panel.",
     "Anyone who can open the desktop gets a shell as the app's user. Turn the web login on first.")
_add("WEB_NOTIFICATION", "web", "bool01", "0", "Desktop notifications",
     "Show the desktop's own notifications as browser notifications.")
_add("WEB_HOST_CLIPBOARD_SYNC", "web", "bool01", "1", "Clipboard sync",
     "Copy and paste between your computer and the desktop.")
_add("WEB_LOCALHOST_ONLY", "web", "bool01", "0", "Web interface on localhost only",
     "Listen on 127.0.0.1 only, for use behind a reverse proxy in the same network namespace.",
     "Nothing outside the container can reach the desktop, or this page, while it is on.")
_add("VNC_LOCALHOST_ONLY", "web", "bool01", "0", "VNC on localhost only",
     "Do not accept VNC clients from outside the container.")

# ---- security ---------------------------------------------------------------------
_add("SECURE_CONNECTION", "security", "bool01", "0", "Encrypted connection",
     "Serve the desktop over HTTPS and VNC over TLS, with a certificate made on first start. "
     "Needed for the web login unless you allow the insecure one.", shown="always")
_add("SECURE_CONNECTION_VNC_METHOD", "security", "enum", "SSL", "VNC encryption method",
     "SSL wraps the connection (stunnel); TLS uses VNC's own upgrade.", choices=["SSL", "TLS"])
_add("WEB_AUTHENTICATION", "security", "bool01", "0", "Web login",
     "Ask for a username and password before the desktop, this page and the monitor open. "
     "Off by default, which leaves the monitor's controls open to anything on your network.",
     "Set the username and password below first. If you lock yourself out, set "
     "BB_IGNORE_UI_SETTINGS=1 on the container, restart it, fix the settings here, remove the variable.",
     shown="always")
_add("WEB_AUTHENTICATION_USERNAME", "security", "text", "", "Web login username",
     "The username for the web login.", pattern=r"[A-Za-z0-9_.@\-]{0,64}")
_add("WEB_AUTHENTICATION_PASSWORD", "security", "secret", "", "Web login password",
     "The password for the web login. It is stored in the settings file on your appdata share and "
     "is never shown again.", maxlen=256)
_add("WEB_AUTHENTICATION_ALLOW_INSECURE", "security", "bool01", "0", "Allow the web login over plain HTTP",
     "Lets the login work without the encrypted connection.",
     "The password then crosses your network in the clear.")
_add("WEB_AUTHENTICATION_TOKEN_VALIDITY_TIME", "security", "int", "24", "Login lifetime (hours)",
     "How long a browser stays logged in.", lo=1, hi=8760)
_add("VNC_PASSWORD", "security", "secret", "", "VNC password",
     "Password for VNC clients and the browser desktop. VNC passwords are cut to 8 characters by the "
     "protocol. Stored in the settings file and never shown again.", maxlen=64, shown="always")
_add("API_CORS_ORIGINS", "security", "text", "", "API origins",
     "Comma-separated origins a browser page may call the HTTP API from. Empty allows none. There is no "
     "wildcard.", pattern=r"[A-Za-z0-9:/.,\-\[\]]{0,512}", maxlen=512)

# ---- runtime ----------------------------------------------------------------------
_add("APP_NICENESS", "runtime", "int", "0", "Priority (niceness)",
     "-20 is the highest priority, 19 the lowest. Lower the priority to keep a busy server responsive.",
     "A negative value needs the container to be started with --cap-add=SYS_NICE.", lo=-20, hi=19)
_add("KEEP_APP_RUNNING", "runtime", "bool01", "0", "Restart the app when it exits",
     "When Backblaze's launcher exits, start it again instead of stopping the container.")
_add("SERVICES_GRACETIME", "runtime", "int", "5000", "Shutdown grace time (ms)",
     "How long each service gets to stop before it is killed when the container stops. Raise it if "
     "a stop leaves the client's database in recovery.", lo=0, hi=600000)

# ---- update notices ---------------------------------------------------------------
_add("UPDATE_CHECK", "updates", "booltf", "true", "Check for updates",
     "Once a day, ask Docker Hub, WineHQ and Backblaze whether anything newer exists. Only version "
     "numbers are fetched; nothing about your backup leaves the container.")
_add("UPDATE_CHECK_INTERVAL_HOURS", "updates", "int", "24", "Hours between checks",
     "How often the check runs.", lo=1, hi=168)

BY_VAR = {r["var"]: r for r in REGISTRY}
GROUP_ORDER = [g[0] for g in GROUPS]


# ---- validation -------------------------------------------------------------------

def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "on", "enabled", "enable")


def normalise(row, value):
    """The canonical text for a value, in the spelling the image reads, or ValueError
    naming the problem. Booleans may arrive as Python bools or any common spelling."""
    kind = row["kind"]
    if isinstance(value, bool):
        value = "true" if value else "false"
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError("%s: the value must be text" % row["title"])
    value = str(value).strip()
    if len(value) > row["maxlen"] or "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("%s: too long, or contains a line break" % row["title"])
    if kind in ("bool01", "booltf"):
        low = value.lower()
        if low not in ("1", "0", "true", "false", "yes", "no", "on", "off", "y", "n"):
            raise ValueError("%s: must be on or off" % row["title"])
        on = low in ("1", "true", "yes", "on", "y")
        return ("1" if on else "0") if kind == "bool01" else ("true" if on else "false")
    if kind == "int":
        if not re.fullmatch(r"-?[0-9]{1,9}", value):
            raise ValueError("%s: must be a whole number" % row["title"])
        n = int(value)
        if (row["lo"] is not None and n < row["lo"]) or (row["hi"] is not None and n > row["hi"]):
            raise ValueError("%s: must be between %d and %d" % (row["title"], row["lo"], row["hi"]))
        return str(n)
    if kind == "enum":
        if value not in row["choices"]:
            raise ValueError("%s: must be one of %s" % (row["title"], ", ".join(row["choices"])))
        return value
    if kind == "secret":
        if not value:
            raise ValueError("%s: cannot be empty (clear it instead)" % row["title"])
        return value
    if row["pattern"] is not None and not re.fullmatch(row["pattern"], value):
        raise ValueError("%s: not a valid value" % row["title"])
    return value


def interlocks(values):
    """(errors, warnings) for the full set of effective values, {var: text}. Errors
    refuse a save; warnings are shown beside it."""
    errors, warnings = [], []

    def v(name):
        return values.get(name, BY_VAR[name]["default"])

    for dim in ("DISPLAY_WIDTH", "DISPLAY_HEIGHT"):
        if v(dim).lstrip("-").isdigit() and int(v(dim)) % 4:
            errors.append("%s must be divisible by 4" % BY_VAR[dim]["title"])
    if _truthy(v("WEB_AUTHENTICATION")):
        if not v("WEB_AUTHENTICATION_USERNAME") or not v("WEB_AUTHENTICATION_PASSWORD"):
            errors.append("The web login needs both a username and a password")
        if not _truthy(v("SECURE_CONNECTION")) and not _truthy(v("WEB_AUTHENTICATION_ALLOW_INSECURE")):
            errors.append("The web login needs the encrypted connection, or 'Allow the web login over "
                          "plain HTTP'")
    elif _truthy(v("WEB_TERMINAL")) or _truthy(v("WEB_FILE_MANAGER")):
        warnings.append("The terminal or file manager is on with no web login: anyone who can reach "
                        "this port can use it")
    if not _truthy(v("WEB_AUTHENTICATION")):
        warnings.append("There is no web login, so anyone on your network can open this page")
    if v("APP_NICENESS").lstrip("-").isdigit() and int(v("APP_NICENESS")) < 0:
        warnings.append("A negative priority needs --cap-add=SYS_NICE on the container")
    if v("WEB_LOCALHOST_ONLY") in ("1",) and v("VNC_LOCALHOST_ONLY") in ("1",):
        warnings.append("Both listeners are localhost-only: nothing outside the container can connect")
    return errors, warnings


# ---- the store --------------------------------------------------------------------

def load_store(path=None):
    """{var: text} from the store, with anything invalid dropped (and reported on
    stderr by `emit`). A missing or unreadable file is an empty store."""
    try:
        with open(path or STORE, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}, []
    raw = doc.get("settings") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        return {}, []
    good, bad = {}, []
    for var, value in raw.items():
        row = BY_VAR.get(var)
        if row is None:
            bad.append("%s: not a setting this image knows" % var)
            continue
        try:
            good[var] = normalise(row, value)
        except ValueError as exc:
            bad.append("%s: %s" % (var, exc))
    return good, bad


def _same(row, a, b):
    """Whether two values are the same setting once spelled the same way, so a container
    that passes DARK_MODE=false and a page that stores 0 are not flagged as different."""
    try:
        return normalise(row, a if a is not None else "") == normalise(row, b if b is not None else "")
    except ValueError:
        return str(a) == str(b)


def _fp(value):
    """A short fingerprint, so a changed secret can be told without keeping it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def emit(environ=None, store=None, applied_path=None, out=None, err=None):
    """Print the `export` lines that apply the store over the container's own
    variables, and write the record of what was applied. Runs before /init."""
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    err = err or sys.stderr
    record = {"at": int(time.time()), "ignored": False, "vars": {}}
    overrides = {}
    if _truthy(environ.get("BB_IGNORE_UI_SETTINGS")):
        record["ignored"] = True
        print("settings: BB_IGNORE_UI_SETTINGS is set, so values saved on the Container tab are "
              "ignored for this start", file=err)
        stored = {}
    else:
        stored, bad = load_store(store)
        for msg in bad:
            print("settings: skipped a stored value: %s" % msg, file=err)
    # PUID and PGID are the names most images use. They stand in for USER_ID and
    # GROUP_ID when set, and a value saved on the page still beats them.
    aliases = {"PUID": "USER_ID", "PGID": "GROUP_ID"}
    for alias, target in aliases.items():
        val = environ.get(alias, "").strip()
        if val:
            try:
                overrides[target] = (normalise(BY_VAR[target], val), "alias")
            except ValueError as exc:
                print("settings: %s ignored: %s" % (alias, exc), file=err)
    for var, value in stored.items():
        overrides[var] = (value, "setting")
    for row in REGISTRY:
        var = row["var"]
        docker = environ.get(var)
        secret = row["kind"] == "secret"
        if var in overrides:
            value, origin = overrides[var]
            print("export %s=%s" % (var, shlex.quote(value)), file=out)
            effective = value
        else:
            value, origin, effective = None, "container", docker
        entry = {"origin": origin}
        if secret:
            entry["set"] = bool(effective)
            entry["fp"] = _fp(effective) if effective else None
        else:
            entry["value"] = effective
            entry["docker"] = docker
        record["vars"][var] = entry
    try:
        path = applied_path or APPLIED
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as exc:
        print("settings: could not write the applied record: %s" % exc, file=err)
    n = sum(1 for _, o in overrides.values() if o == "setting")
    if n:
        print("settings: applying %d value(s) saved on the Container tab" % n, file=err)


# ---- the page's side --------------------------------------------------------------

def applied():
    try:
        with open(APPLIED, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def state(store_path=None, applied_doc=None, environ=None):
    """Everything the page draws: each setting with the value that would apply at
    the next start, the value running now, where each comes from, and whether a
    restart would change anything."""
    environ = os.environ if environ is None else environ
    ap = applied_doc if applied_doc is not None else applied()
    stored, bad = load_store(store_path)
    rows, pending_any = [], False
    for row in REGISTRY:
        var = row["var"]
        run = (ap or {}).get("vars", {}).get(var)
        secret = row["kind"] == "secret"
        docker = (run or {}).get("docker") if run else environ.get(var)
        if run is None:
            running = environ.get(var)
        else:
            running = run.get("value")
        item = {"var": var, "group": row["group"], "kind": row["kind"], "title": row["title"],
                "summary": row["summary"], "warning": row["warning"], "default": row["default"],
                "unraid": row["unraid"], "lo": row["lo"], "hi": row["hi"], "choices": row["choices"],
                "stored": var in stored}
        if secret:
            have = var in stored or bool((run or {}).get("set"))
            item["set"] = have
            fp_run = (run or {}).get("fp")
            if var in stored:
                pending = (fp_run != _fp(stored[var]))
            else:
                pending = ((run or {}).get("origin") == "setting")
            item["source"] = "setting" if var in stored else "container"
        else:
            desired = stored.get(var, docker if docker is not None else row["default"])
            item["value"] = desired
            item["running"] = running if running is not None else row["default"]
            item["container_value"] = docker if docker is not None else row["default"]
            item["source"] = "setting" if var in stored else "container"
            pending = ap is not None and not _same(row, desired, item["running"])
        item["pending"] = bool(pending)
        pending_any = pending_any or bool(pending)
        rows.append(item)
    eff = {r["var"]: (r["value"] if "value" in r else "x" if r.get("set") else "") for r in rows}
    errors, warnings = interlocks(eff)
    return {"groups": [{"id": g, "title": t, "about": a} for g, t, a in GROUPS],
            "settings": rows, "pending": pending_any, "warnings": warnings,
            "ignored": bool((ap or {}).get("ignored")), "dropped": bad,
            "applied_at": (ap or {}).get("at"), "running_with_record": ap is not None,
            "restart_possible": os.path.isdir("/etc/services.d/bb-restart")}


def save(changes, store_path=None):
    """Store changes: {var: value} sets one, {var: None} makes that setting follow the
    container again. A blank secret is left alone. Validates every value and the
    combination before writing anything. Raises ValueError with the reason."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nothing to save")
    stored, _ = load_store(store_path)
    new = dict(stored)
    for var, value in changes.items():
        row = BY_VAR.get(var)
        if row is None:
            raise ValueError("%r is not a setting this page changes" % (var,))
        if value is None:
            new.pop(var, None)
            continue
        if row["kind"] == "secret" and value == "":
            continue
        new[var] = normalise(row, value)
    # The combination is checked as it will run: stored values over the container's own.
    env = {r["var"]: (os.environ.get(r["var"]) or r["default"]) for r in REGISTRY}
    ap = applied()
    if ap:
        for var, e in ap.get("vars", {}).items():
            if var in env and e.get("docker") is not None:
                env[var] = e["docker"]
            elif var in env and e.get("set") is not None and BY_VAR[var]["kind"] == "secret":
                env[var] = "x" if e.get("set") else ""
    env.update(new)
    errors, _ = interlocks(env)
    if errors:
        raise ValueError("; ".join(errors))
    import bbapi
    with bbapi._mutate():
        os.makedirs(os.path.dirname(store_path or STORE), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(store_path or STORE))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "settings": new}, fh, sort_keys=True)
                fh.write("\n")
            os.chmod(tmp, 0o600)
            bbapi._own_like_config(tmp)
            os.replace(tmp, store_path or STORE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def request_restart():
    """Ask the bb-restart service to stop the container. It comes back by itself only
    if its restart policy says so; the page says that."""
    if not os.path.isdir("/etc/services.d/bb-restart"):
        raise ValueError("this image has no restart service")
    with open(RESTART_REQUEST, "w", encoding="utf-8") as fh:
        fh.write(str(int(time.time())) + "\n")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "emit":
        emit()
    else:
        sys.exit("usage: bbcontainer.py emit")
