# Running this on Unraid

This is a fork of [iamfoz/backblaze-64-personal-wine-container](https://github.com/iamfoz/backblaze-64-personal-wine-container)
with a Container tab that changes every setting from the browser, update notices, and an Unraid template that exposes every
option. Everything in the upstream README about Backblaze under Wine, the monitor, the API and troubleshooting still applies.

## Install

1. **Apps** tab, search for **Backblaze-Personal** (or, before it is listed, run this on the server and use
   *Docker → Add Container → pick the template*):

   ```bash
   wget -O /boot/config/plugins/dockerMan/templates-user/my-Backblaze-Personal.xml \
     https://raw.githubusercontent.com/aon082910/AoN-Unraid-Apps/main/Backblaze-Personal/backblaze-personal.xml
   ```

2. Set **Backup Source (Drive D)** to the first folder you want protected, for example `/mnt/user/Photos`. Add more with E, F, G
   in *Show more settings*, or *Add another Path* with container path `/drive_h/` up to `/drive_z/`. The mapping must be
   **read-write**: Backblaze writes a `.bzvol` folder into the root of each drive.
3. Leave **User ID 99** and **Group ID 100** (nobody:users) unless your data is owned by someone else.
4. *Apply*, open the **WebUI**, sign in to Backblaze in the desktop tab, and let the first backup run.

The template sets `--restart=unless-stopped`. The Container tab's **Restart** button relies on it: the container stops itself
cleanly and Docker starts it again.

## Which tag

Edit the **Repository** field to change tag.

| Tag | Wine | Backblaze client | Use it when |
|---|---|---|---|
| `allornothing/backblaze-personal:latest` | WineHQ stable | pinned to 10.0.1.1069 | You want the least surprise. Default. |
| `:latest-patched` | the fork's patched Wine, fixes on | follows Backblaze's newest | You want client 10.0.3+ or the upload fix. Clear **Pin the Backblaze client**. |
| `:beta` | patched Wine, performance patches on, rebuilt weekly | follows the newest | Upload speed is why you are here. |
| `:ubuntu26` | WineHQ stable on Ubuntu 26.04 | pinned | Early access to the next base. |
| `:v10.2.2-aon.1` and so on | as the tag it was cut from | | You want to stay on one release. |

Moving between `:latest` and a patched tag is safe: the Wine prefix upgrades in place. Moving back down usually works but
Wine does not promise it.

## Changing settings

Open the WebUI and the **Container** tab. Everything the template lists is there, grouped, with the image default, what is
running now, and whether the value comes from the template or from the tab.

* A value saved on the tab wins over the same field in the Unraid template. **Follow the container** puts it back.
* Everything is read when the container starts, so changes apply after a restart. The tab says which settings are waiting.
* Passwords are write-only: the tab can set or clear one and never shows it again.
* If you lock yourself out (a web login with a password you mistyped), add the variable `BB_IGNORE_UI_SETTINGS` = `1` in
  the template, apply, fix the setting, then set it back to `0`.

The Wine patch switches, notifications, quiet hours and exclusions stay on the **Settings** tab, as upstream.

## Update notices

Once a day (`UPDATE_CHECK_INTERVAL_HOURS`) the container asks Docker Hub, WineHQ and Backblaze whether a newer image, Wine or
client exists. The answers are on the **Container** tab. To be told rather than have to look, add a notification endpoint on
the **Settings** tab (ntfy, Discord, Slack, Gotify, Pushbullet or any webhook) and leave the events *Container image update*,
*Newer Wine* and *Newer Backblaze client* ticked.

Updating is still yours to do: Unraid's **Docker** tab → *Check for updates* → *apply update*. Unraid shows "update ready" for
this container on its own once a new image is pushed.

## Moving from iamfoz's template

Point **Config** at the same appdata folder. The Wine prefix and your Backblaze identity carry over, because this image uses the
same layout. Stop the old container first: two containers on one folder will corrupt it. Your old settings that were Docker
variables are in the new template under the same names.

## Networks

* **bridge** (default): map port 5800.
* **br0 / custom network**: the container gets its own address; the port mapping is ignored. Port 5800 is used as is.
* **host**: set **Web port inside the container** (and the VNC one) to free ports, since the mapping is ignored.
* Behind a reverse proxy: turn on **Web interface on localhost only** only if the proxy shares the container's network
  namespace; otherwise leave it off and put the web login on.

## Security notes

The monitor can pause the backup, run repairs and, now, change this container's settings. With no web login, anything on your
network that can reach port 5800 can use it. Turn on **Encrypted connection** and **Web login** (Container tab → Security), or keep
the port off the network. The tab will not let you save a web login without a username, a password and a transport that
protects it.
