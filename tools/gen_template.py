#!/usr/bin/env python3
"""Generate the Unraid template from the settings registry.

The container's settings are listed once, in rootfs/usr/local/lib/bb-monitor/bbcontainer.py.
That list builds the Container tab, validates what the tab sends, and, through this script,
builds the template, so a setting cannot be on the page and missing from the template, or
the other way round. tests/test-container.py fails when the committed template is stale.

    python3 tools/gen_template.py                 # writes templates/backblaze-personal.xml
    python3 tools/gen_template.py --check         # exits 1 if the committed file is stale
    python3 tools/gen_template.py --out PATH      # writes somewhere else

What is not in the registry and is added here: the ports and paths, the Wine patch switches
(which the Settings tab also sets), the watchdog and dataset switches, and the escape hatch
BB_IGNORE_UI_SETTINGS.
"""
import argparse, os, sys
from xml.sax.saxutils import quoteattr, escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "rootfs", "usr", "local", "lib", "bb-monitor"))
sys.dont_write_bytecode = True
import bbcontainer as C

OUT = os.path.join(ROOT, "templates", "backblaze-personal.xml")
SITE = "https://github.com/aon082910/backblaze-personal"
RAW = "https://raw.githubusercontent.com/aon082910/AoN-Unraid-Apps/main/Backblaze-Personal"

OVERVIEW = """\
Backblaze Personal Backup running under Wine in a container, with a browser desktop (no VNC client needed) and a
monitor, status and settings dashboard beside it. This is a fork of iamfoz/backblaze-64-personal-wine-container with a
Container tab that changes every setting below from the browser, update notices for the image, Wine and the
Backblaze client, and a template that exposes every option.

Not affiliated with or supported by Backblaze. The container needs read-write access to what it backs up: Backblaze writes
a .bzvol folder into the root of every drive. Map each folder you want protected to /drive_d, /drive_e and so on.

Image tags (edit the Repository field to switch): latest = WineHQ stable, pins client 10.0.1.1069; latest-patched = the
fork's patched Wine, fixes on, follows the newest client; beta = patched Wine with the performance patches on, weekly;
ubuntu26 = early access. On a patched or beta tag, clear Pin the Backblaze client.

First start: open the Web UI, sign in to your Backblaze account in the desktop, and let the first backup run. Nothing is
sent to this project; update checks fetch version numbers from Docker Hub, WineHQ and Backblaze only.

Moving from iamfoz's template: point Config at the same appdata folder and the Wine prefix and your Backblaze identity carry
over. Do not run two containers on one folder.
"""

CHANGES = """\
### Fork 10.2.2-aon.1
- Container tab: every environment setting is editable in the browser (user and group, time zone, screen, web login, VNC
  password, client pin, ports, priority...), applied at the next start, with a Restart button.
- Update notices: once a day the container checks for a newer image, Wine and Backblaze client and tells you through the
  notification endpoints in Settings. The repository also files an issue and sends a push when upstream, Wine, the
  Backblaze client or the base image move.
- Template: every setting is a field, with Unraid defaults (99:100, restart policy unless-stopped).
- PUID and PGID are accepted as aliases for USER_ID and GROUP_ID.
- BB_IGNORE_UI_SETTINGS=1 ignores the saved values for one start, the way back in after a bad choice.
"""


def attr(v):
    return quoteattr(v)


def config(name, target, default, desc, typ="Variable", display="advanced", required=False, mask=False,
           mode="", value=None):
    value = default if value is None else value
    if "|" in default:
        value = default.split("|", 1)[0] if value == default else value
    return ("  <Config Name=%s Target=%s Default=%s Mode=%s Description=%s Type=%s Display=%s Required=%s Mask=%s>%s</Config>"
            % (attr(name), attr(target), attr(default), attr(mode), attr(desc), attr(typ), attr(display),
               attr("true" if required else "false"), attr("true" if mask else "false"), escape(value)))


def row_config(row):
    desc = row["summary"]
    if row["warning"]:
        desc += " " + row["warning"]
    kind, dflt = row["kind"], row["unraid"]
    shown = row["shown"]
    if kind == "bool01":
        options = "1|0" if dflt == "1" else "0|1"
        return config(row["title"], row["var"], options, desc + " (1 is on, 0 is off.)", display=shown, value=dflt)
    if kind == "booltf":
        options = "true|false" if dflt == "true" else "false|true"
        return config(row["title"], row["var"], options, desc, display=shown, value=dflt)
    if kind == "enum":
        rest = [c for c in row["choices"] if c != dflt]
        return config(row["title"], row["var"], "|".join([dflt] + rest), desc, display=shown, value=dflt)
    if kind == "int":
        desc += " (%s to %s)" % (row["lo"], row["hi"])
    return config(row["title"], row["var"], dflt, desc, display=shown, mask=(kind == "secret"), value=dflt)


# Settings that live outside the registry. Description text is the template's own.
EXTRA = [
    ("Enable watchdog", "ENABLE_WATCHDOG", "false|true",
     "Recover automatically from the known stalls: clear a stale four-hour lock left by an out-of-memory kill and stop a pass "
     "stuck on a lost upload child. Every action is logged. Off by default because it deletes a lock file and kills processes. "
     "The Settings tab has a switch that overrides this without a restart.", "false"),
    ("Datasets as folders", "MOUNTPOINTS_AS_DIRS", "",
     "Patched and beta tags only. Which drives show the separate filesystems inside them (ZFS datasets, a mounted cache pool) "
     "as ordinary folders so the client backs them up: all, or drive letters such as d,e. Empty for none. Without it the client "
     "skips every dataset inside a drive. A dataset already backed up under its own letter uploads again under the new path.", ""),
    ("Wine: upload writability fix", "WINE_SOCK_SEND_READY", "1|0",
     "Patched and beta tags only. Lifts the stock-Wine cap of about 140 KB/s on a single upload stream (WineHQ bug 59893). "
     "The Settings tab overrides it.", "1"),
    ("Wine: FD_WRITE re-arm", "WINE_SOCK_FDWRITE_REARM", "0|1",
     "Patched and beta tags only. About three times the upload rate on high-latency links; a workaround that differs from Windows "
     "and needs the writability fix. The beta image turns it on; :latest-patched leaves it off.", "0"),
    ("Wine: service token", "WINE_SERVICE_TOKEN", "1|0",
     "Patched and beta tags only. Gives services the LocalSystem group, which client 10.0.3.1075 and later need to complete a "
     "backup pass. Off means the client must stay on 10.0.1.1069.", "1"),
    ("Wine: OFD file locks", "WINE_OFD_LOCKS", "1|0",
     "Patched and beta tags only. Stops the client's database leaking file descriptors until nothing can open a file.", "1"),
    ("Wine: case-sensitivity cache", "WINE_CASE_CACHE", "0|1",
     "Patched and beta tags only. Decides case sensitivity once per device instead of after every failed lookup; large speed-up on "
     "Unraid user shares. The beta image turns it on; :latest-patched leaves it off.", "0"),
    ("Ignore saved Container-tab values", "BB_IGNORE_UI_SETTINGS", "0|1",
     "Set to 1 for one start to ignore everything saved on the Container tab, for example after locking yourself out with a web "
     "login. Set it back to 0 afterwards.", "0"),
]


def build():
    out = []
    w = out.append
    w('<?xml version="1.0"?>')
    w('<Container version="2">')
    w("  <Name>Backblaze-Personal</Name>")
    w("  <Repository>allornothing/backblaze-personal:latest</Repository>")
    w("  <Registry>https://hub.docker.com/r/allornothing/backblaze-personal</Registry>")
    w("  <Network>bridge</Network>")
    w("  <MyIP/>")
    w("  <Shell>bash</Shell>")
    w("  <Privileged>false</Privileged>")
    w("  <Support>%s/issues</Support>" % SITE)
    w("  <Project>%s</Project>" % SITE)
    w("  <TemplateURL>%s/backblaze-personal.xml</TemplateURL>" % RAW)
    w("  <ReadMe>%s/blob/main/docs/UNRAID.md</ReadMe>" % SITE)
    w("  <Icon>https://cdn.jsdelivr.net/gh/selfhst/icons/png/backblaze.png</Icon>")
    w("  <WebUI>http://[IP]:[PORT:5800]/</WebUI>")
    w("  <Overview>\n%s  </Overview>" % escape(OVERVIEW))
    w("  <Category>Backup: Tools:</Category>")
    w("  <ExtraSearchTerms>backblaze backup wine cloud offsite personal b2 vnc novnc</ExtraSearchTerms>")
    w("  <Changes>\n%s  </Changes>" % escape(CHANGES))
    w("  <ExtraParams>--restart=unless-stopped</ExtraParams>")
    w("  <PostArgs/>")
    w("  <CPUset/>")
    w("  <DateInstalled/>")
    w("")
    w("  <!-- Ports -->")
    w(config("Web UI Port", "5800", "5800", "Port for the browser desktop, the monitor and the Container tab. With a custom network "
             "(br0) or host networking set Web port inside the container instead.", typ="Port", display="always",
             required=True, mode="tcp"))
    w(config("VNC Port", "5900", "5900", "Port for a VNC client. Leave unmapped if you only use the browser.", typ="Port",
             display="always", mode="tcp"))
    w("")
    w("  <!-- Paths -->")
    w(config("Config", "/config", "/mnt/user/appdata/backblaze-personal",
             "Persistent storage for the Wine prefix, your Backblaze identity and every setting saved on the Container tab. "
             "Must survive restarts. Back this folder up.", typ="Path", display="always", required=True, mode="rw"))
    for letter, shown in (("d", "always"), ("e", "advanced"), ("f", "advanced"), ("g", "advanced")):
        w(config("Backup Source (Drive %s)" % letter.upper(), "/drive_%s/" % letter, "",
                 "Optional: a host folder to back up as drive %s:. Must be read-write: Backblaze creates a .bzvol folder in the "
                 "drive's root. For more drives add another Path with Container Path /drive_h/ and so on up to /drive_z/." % letter.upper(),
                 typ="Path", display=shown, mode="rw"))
    w("")
    by_group = {}
    for row in C.REGISTRY:
        by_group.setdefault(row["group"], []).append(row)
    for gid, title, _ in C.GROUPS:
        w("  <!-- %s -->" % escape(title))
        for row in by_group.get(gid, []):
            w(row_config(row))
        w("")
    w("  <!-- Recovery, Wine patches and escape hatch -->")
    for name, target, options, desc, value in EXTRA:
        w(config(name, target, options, desc, display="advanced", value=value))
    w("</Container>")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = build()
    if args.check:
        try:
            same = open(args.out, encoding="utf-8").read() == text
        except OSError:
            same = False
        if not same:
            sys.exit("%s is out of date: run python3 tools/gen_template.py" % args.out)
        print("template is current")
        return
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("wrote %s (%d settings from the registry)" % (args.out, len(C.REGISTRY)))


if __name__ == "__main__":
    main()
