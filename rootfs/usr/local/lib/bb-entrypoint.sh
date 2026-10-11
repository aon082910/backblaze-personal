#!/bin/sh
# The image's entrypoint. Runs before the base image's /init and does one thing:
# export the values saved on the Container tab over the ones Docker gave the
# container, so a setting changed in the browser is in force from the first
# script /init runs (user and group IDs, time zone, web login, screen size...).
#
# The base image only sets a variable that is unset, so its own /etc/cont-env.d
# mechanism cannot override what Docker passed. Exporting before /init can.
#
# It fails open: if Python or the settings module is missing, or the store is
# unreadable, the container starts with the variables Docker gave it. BB_IGNORE_UI_SETTINGS=1
# skips the store for one start, which is the way back in after a bad choice.
#
# What it does with PUID and PGID, and which stored values it refuses, is in
# bbcontainer.py (emit).
set -u

MOD=/usr/local/lib/bb-monitor/bbcontainer.py
if [ -r "$MOD" ] && command -v python3 >/dev/null 2>&1; then
    # -I: no script directory or user site on the path, and no PYTHON* variables, so
    # nothing in /config can be imported before the container is up.
    _bb_env="$(python3 -I "$MOD" emit)" && eval "$_bb_env"
fi
unset _bb_env

exec "$@"
