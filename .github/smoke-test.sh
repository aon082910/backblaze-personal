#!/usr/bin/env bash
# Boot the built image and check it comes up.
#
# A successful docker build says nothing about whether the container starts: base
# image updates, apt/keyring changes and startup script edits have all broken first
# boot while building cleanly. This starts the image the way a user would, waits for
# the parts that must work, and fails the build if they do not.
#
# It does NOT sign in to Backblaze or run a backup, because there are no credentials in CI.
# It checks the container's scaffolding: the supervisor comes up, Wine builds a
# prefix and reports Windows 10, the drive mapping reaches into the prefix, and the
# bundled tools are present and runnable.
#
# Checks are deterministic. Wine is noisy on startup, so this asserts
# specific things are true rather than grepping the log for anything error-shaped,
# which would turn normal Wine chatter into red builds.
set -euo pipefail

IMAGE="${1:?usage: smoke-test.sh <image>}"
NAME="bb-smoke-$$"
CONFIG="$(mktemp -d)"
DRIVE="$(mktemp -d)"
TIMEOUT="${SMOKE_TIMEOUT:-420}"

cleanup() {
    echo "--- container log (tail) ---"
    docker logs "$NAME" 2>&1 | tail -60 || true
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    sudo rm -rf "$CONFIG" "$DRIVE" 2>/dev/null || rm -rf "$CONFIG" "$DRIVE" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

echo "hello from the smoke test" > "$DRIVE/canary.txt"

# A value saved on the Container tab before the first start. The entrypoint must export
# it over the variable Docker passes (TZ below), which is the whole point of the tab.
mkdir -p "$CONFIG/bb-api"
printf '{"version": 1, "settings": {"TZ": "America/Chicago", "DARK_MODE": "1"}}\n' > "$CONFIG/bb-api/container-settings.json"

echo "== starting $IMAGE =="
docker run -d --name "$NAME" \
    -v "$CONFIG:/config" \
    -v "$DRIVE:/drive_d" \
    -e DISABLE_AUTOUPDATE=true \
    -e TZ=Etc/UTC \
    -e PUID=1000 \
    "$IMAGE" >/dev/null

# Wait for the Wine prefix to be built and reporting Windows 10. This is the slowest
# part of first boot and the step most likely to break, so everything else is checked
# only once it succeeds.
echo "== waiting for the Wine prefix (up to ${TIMEOUT}s) =="
deadline=$(( SECONDS + TIMEOUT ))
until docker logs "$NAME" 2>&1 | grep -q "prefix ready and reporting Windows 10"; do
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] \
        || fail "container exited during startup"
    [ "$SECONDS" -lt "$deadline" ] || fail "timed out waiting for the Wine prefix"
    sleep 5
done
echo "   prefix ready"

echo "== checking the drive mapping =="
docker exec "$NAME" test -e "/config/wine/dosdevices/d:" \
    || fail "/drive_d was not mapped to a Wine drive letter"
docker exec "$NAME" sh -c 'cat "/config/wine/dosdevices/d:/canary.txt"' | grep -q "hello from the smoke test" \
    || fail "mapped drive is not readable through the Wine prefix"

echo "== checking the bundled tools =="
docker exec "$NAME" python3 -c "import curses" \
    || fail "python3/curses missing - bb-monitor cannot run"
for tool in bb-monitor bb-health bb-watchdog bb-version bb-doctor bb-report; do
    docker exec "$NAME" test -x "/usr/local/bin/$tool" || fail "$tool missing or not executable"
done
docker exec "$NAME" test -x /etc/services.d/bb-watchdog/run \
    || fail "watchdog service run script missing or not executable"

echo "== checking the web interface =="
# Every image serves the Monitor, Status, Tools, API and Settings tabs through the
# base image's nginx. The dashboard service can take a moment after the prefix,
# so this waits for it, then fetches the Settings page and the Wine patches
# endpoint through nginx the way a browser would.
docker exec "$NAME" test -x /etc/services.d/bb-monitor-web/run \
    || fail "web interface service run script missing or not executable"
deadline=$(( SECONDS + 120 ))
until docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:5800/monitor/settings 2>/dev/null' | grep -q "Wine patches"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "the Settings tab did not come up through nginx at /monitor/settings"
    sleep 5
done
docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:5800/monitor/manage/wine' | grep -q '"wine"' \
    || fail "the Wine patches endpoint did not answer"
echo "   Settings tab served"

echo "== checking the Container tab and the entrypoint =="
docker exec "$NAME" test -x /usr/local/lib/bb-entrypoint.sh || fail "the entrypoint is missing or not executable"
[ "$(docker inspect -f '{{json .Config.Entrypoint}}' "$NAME")" = '["/usr/local/lib/bb-entrypoint.sh"]' ] \
    || fail "the image's entrypoint is not bb-entrypoint.sh"
# Docker said Etc/UTC; the saved value must have won, in the supervisor's environment.
tz="$(docker exec "$NAME" sh -c 'tr "\0" "\n" < /proc/1/environ | sed -n "s/^TZ=//p"')"
[ "$tz" = "America/Chicago" ] || fail "the TZ saved on the Container tab did not override the one Docker passed (got: $tz)"
docker exec "$NAME" sh -c 'cat /etc/bb64-applied.json' | grep -q '"origin": "setting"' \
    || fail "the applied record does not show a setting taken from the store"
deadline=$(( SECONDS + 120 ))
until docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:5800/monitor/container 2>/dev/null' | grep -q "Backblaze 64 Container"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "the Container tab did not come up through nginx at /monitor/container"
    sleep 5
done
state="$(docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:5800/monitor/manage/container')"
printf '%s' "$state" | grep -q '"USER_ID"' || fail "the container settings endpoint did not list the settings"
printf '%s' "$state" | python3 -c 'import json,sys; d=json.load(sys.stdin); s={x["var"]:x for x in d["settings"]}; assert s["TZ"]["source"]=="setting" and s["TZ"]["running"]=="America/Chicago", s["TZ"]; assert s["USER_ID"]["running"]=="1000", s["USER_ID"]' \
    || fail "the Container tab does not report the saved TZ as running, or PUID did not become USER_ID"
# A save through the same route the page uses, then the restart-needed flag.
post() { docker exec "$NAME" sh -c "curl -fsS -X POST -H 'X-Requested-With: bb64' -H 'Content-Type: application/json' -d '$2' http://127.0.0.1:5800/monitor/manage/$1"; }
post container '{"changes": {"UMASK": "0000"}}' | grep -q '"pending": true' \
    || fail "saving a setting did not mark a restart as needed"
docker exec "$NAME" grep -q '"UMASK": "0000"' /config/bb-api/container-settings.json || fail "the saved setting is not in the store"
if post container '{"changes": {"USER_ID": "0"}}' >/dev/null 2>&1; then fail "user ID 0 was accepted"; fi
docker exec "$NAME" sh -c 'curl -fsS http://127.0.0.1:5800/monitor/manage/updates' | grep -q '"interval_hours"' \
    || fail "the update-check endpoint did not answer"
echo "   Container tab served, a saved value won over Docker's, a save round-trips"

echo "== checking the diagnostic tools run =="
docker exec "$NAME" bb-report --list | grep -q "would collect" \
    || fail "bb-report --list did not describe what it collects"
docker exec "$NAME" sh -c 'bb-doctor >/dev/null 2>&1; [ $? -le 1 ]' \
    || fail "bb-doctor crashed (exit >1)"

echo "== checking the build stamp =="
# test -s catches a missing stamp file, and the grep anchors on "built=",
# which the tools' no-stamp fallback text never contains. Anchoring on "version="
# would match the fallback too and pass on an image whose stamp step vanished.
docker exec "$NAME" test -s /etc/bb-build \
    || fail "/etc/bb-build is missing or empty - the build stamp step was lost"
for tool in bb-monitor bb-health bb-version bb-watchdog bb-doctor bb-report; do
    docker exec "$NAME" "$tool" --version | grep -q "^built=" \
        || fail "$tool --version did not report the build stamp"
done

echo "== checking health reporting =="
status="$(docker exec "$NAME" bb-health)" \
    || fail "bb-health exited non-zero on a freshly started container: ${status:-}"
[ "$status" = "OK" ] || fail "bb-health should report OK on a fresh container, got: $status"

# The watchdog service must start (so it is there when someone enables it) but stay
# dormant unless asked for. The state file proves both: that the service ran at all,
# and which branch it took. Asserting on the container log instead would be testing
# the supervisor's log forwarding rather than the watchdog.
echo "== checking the watchdog started and is dormant by default =="
state="$(docker exec "$NAME" cat /tmp/.bb-watchdog-state 2>/dev/null || true)"
[ -n "$state" ] || fail "watchdog service never ran (no state file) - it would not start when enabled either"
[ "$state" = "disabled" ] || fail "watchdog should be dormant without ENABLE_WATCHDOG, state file says: $state"

# Belt and braces: no watchdog process should exist either. The bracketed character
# stops the pattern matching this command's /proc entry.
if docker exec "$NAME" sh -c 'grep -l "bin/b[b]-watchdog" /proc/[0-9]*/cmdline 2>/dev/null | head -1' | grep -q .; then
    fail "a bb-watchdog process is running despite ENABLE_WATCHDOG not being set"
fi

# Docker's health state must not be failing. It may still read
# "starting" here: the first check only runs after the healthcheck interval, so this
# asserts it is not unhealthy rather than waiting minutes for the first probe.
state="$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo none)"
[ "$state" != "unhealthy" ] || fail "container reported unhealthy"
echo "   docker health state: $state"

# Last, because it stops the container: the Container tab's Restart button. The service must
# exit, the supervisor must take the container down, and it must do so cleanly (exit 0), which
# is what makes Docker restart it under any restart policy.
echo "== checking the restart service =="
docker exec "$NAME" test -x /etc/services.d/bb-restart/run || fail "the restart service is missing"
post container/restart '{}' | grep -q '"ok": true' || fail "the restart request was refused"
deadline=$(( SECONDS + 90 ))
while [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; do
    [ "$SECONDS" -lt "$deadline" ] || fail "the container did not stop after a restart request"
    sleep 2
done
code="$(docker inspect -f '{{.State.ExitCode}}' "$NAME")"
[ "$code" = "0" ] || fail "the restart request stopped the container with exit code $code, not 0"
echo "   container stopped cleanly on request"

echo "SMOKE PASS: $IMAGE"
