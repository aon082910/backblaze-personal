# Backblaze Personal for Unraid

Runs the Backblaze personal backup client in a Docker container (through Wine), so you can back up your server's files to Backblaze. You control it from your browser or any VNC client.

This is a fork of [iamfoz/backblaze-64-personal-wine-container](https://github.com/iamfoz/backblaze-64-personal-wine-container) (AGPL-3.0), tuned for Unraid. It isn't affiliated with Backblaze Inc.

What the fork adds:

- **A Container tab** where you can change the container's settings from the browser, with a Restart button.
- **Update notices** when there's a newer image, Wine or Backblaze client.
- **A ready-made Unraid template** with Unraid's defaults (user 99, group 100).

Image: [allornothing/backblaze-personal](https://hub.docker.com/r/allornothing/backblaze-personal) · Source: [aon082910/backblaze-personal](https://github.com/aon082910/backblaze-personal)

## Install

**Unraid:** use the template from [AoN-Unraid-Apps](https://github.com/aon082910/AoN-Unraid-Apps), or `templates/backblaze-personal.xml` in this repo. Step-by-step help is in `docs/UNRAID.md`.

| Setting | Default | What it's for |
|---|---|---|
| Port `5800` | required | Web UI |
| Port `5900` | optional | VNC |
| `/config` | `/mnt/user/appdata/backblaze-personal` | Wine and the Backblaze install |
| `/drive_d` to `/drive_g` | none | Folders to back up. They show up as D: to G: in Backblaze |

Tips: map folders that hold files, not a ZFS pool's root, since the client won't cross a mount point. Use `/mnt/cache/appdata/...` for `/config` if you can.

Open `http://<server>:5800` and sign in to your Backblaze account. Setting a web login and password is a good idea.

## Help

Setup and maintenance notes are in `docs/`. For anything else, [open an issue](https://github.com/aon082910/backblaze-personal/issues). The [upstream project](https://github.com/iamfoz/backblaze-64-personal-wine-container) documents everything this fork doesn't change.
