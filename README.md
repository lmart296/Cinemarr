# Cinemarr

A self-hosted Plex server with a hands-off request pipeline, packaged so it runs
the same on Linux, macOS and Windows. The only thing you install is Docker.

**What you get:** add a movie or show to your Plex Watchlist from your phone,
and it downloads, gets renamed and filed, and shows up in Plex on its own.

```
Plex Watchlist ─▶ Seerr ─▶ Radarr / Sonarr ─▶ Prowlarr (search)
                                │
                                ▼
                          qBittorrent ─▶ import into library ─▶ Plex
                     (stops at 100%, never seeds,
                      torrent deleted after import)
```

| App | What it does | Address |
|---|---|---|
| Plex | Streams your library | `http://<server-ip>:32400/web` |
| Seerr | Turns Watchlist adds into requests | `http://<BIND_IP>:5055` |
| Radarr / Sonarr | Find, download and file movies / TV | `:7878` / `:8989` |
| Prowlarr | Manages search sources (indexers) | `:9696` |
| qBittorrent | Downloads | `:8080` |

---

## Requirements

- **Docker**
  - Linux: [Docker Engine](https://docs.docker.com/engine/install/) with the Compose plugin
  - macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  - Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/) plus
    Ubuntu in WSL (step 0 below)
- A computer that stays on, with space for your media.

Linux gives the best results: Plex sees your devices directly, TVs find it
automatically, and GPU transcoding works. macOS and Windows work too, with the
limits listed under [Platform notes](#platform-notes).

---

## Quick start

### 0. Windows only: install Ubuntu

Open **PowerShell as administrator** (right-click → Run as administrator) and run:

```powershell
wsl --install
```

Restart the computer, then open **Ubuntu** from the Start menu and create a Linux
username and password when asked. (If `wsl --install` only prints help text, WSL
is already there: run `wsl --install -d Ubuntu` instead.) Docker Desktop connects
to Ubuntu automatically; if `docker` isn't found in Ubuntu, turn on Ubuntu under
Docker Desktop → Settings → Resources → WSL Integration.

Do everything below in the **Ubuntu** window, in your Linux home folder (`cd ~`),
not under `/mnt/c`. Files there are much faster for Docker, imports are instant,
and Plex notices new movies right away. You can browse them from Windows at
`\\wsl$\Ubuntu\home\<your-linux-user>\cinemarr`.

### 1. Get the files and run the setup

```bash
git clone https://github.com/lmart296/Cinemarr.git cinemarr
cd cinemarr
./setup.sh
```

It explains each step and asks before doing it:

1. Checks that Docker is installed and running.
2. Works out your settings: time zone, your home-network address and subnet,
   the user that should own the files (Linux), whether this is Docker Desktop
   (relay setup) or Linux (host networking), and a GPU if you have one (Linux).
   It asks where to keep settings and media, and whether other devices at home
   may open the web pages. Then it writes `.env`, readable only by you.
3. Linux with ufw: offers to let your home network reach Plex.
4. Starts the server (`docker compose up -d`).
5. Opens Plex in your browser and waits while you sign in with your Plex account
   (a free one is fine). Do it right away: until a new server is signed in, any
   device on your network could claim it. You don't pick any folders: as soon
   as you're signed in, the script adds the **Movies** and **TV Shows**
   libraries. Plex's "Media Library" setup page won't list them; just click
   Next, and they're on Plex's home screen afterwards.
6. Configures every app, Seerr included (see
   [What the bootstrap sets](#what-the-bootstrap-sets)).
   It asks for a username and two passwords: one for Radarr, Sonarr and Prowlarr,
   and a different one for qBittorrent (Radarr and Sonarr hand qBittorrent its
   password on every login, so it must not also open them). Nothing is saved, so
   keep both in your password manager. On a brand-new install it also asks for
   qBittorrent's temporary password; get it in another terminal with
   `docker compose logs qbittorrent | grep -i password`.

It's safe to run again at any time: it keeps your `.env`, only asks about what
changed (for example a new home-network address), and skips what's done.
`./setup.sh -y` accepts every default.

### 2. Add your search sources

**Prowlarr** (`http://<BIND_IP>:9696`) → Indexers → add your search sources.
They sync to Radarr and Sonarr. Under each indexer's advanced settings, set
**Apps Minimum Seeders** to about `10`; well-seeded releases are faster and far
less likely to be fake. This is the only step left by hand.

### 3. Test it

Add a public-domain film (for example *Night of the Living Dead*, 1968) to your
Plex Watchlist. Within a few minutes it should appear in Seerr → Radarr →
qBittorrent, stop at 100%, disappear from qBittorrent, and show up in Plex
(usually seconds after the import; at most 15 minutes).

### Setting it up by hand

What `setup.sh` does, if you'd rather do it yourself:

1. `cp .env.example .env && chmod 600 .env`, then set at least `COMPOSE_FILE`
   (`compose.yaml` with Docker Desktop, `compose.yaml:compose.linux.yaml` with
   Docker Engine on Linux), `TZ`, `PUID`/`PGID` (Linux: `id -u`, `id -g`),
   `SERVER_IP` (required with Docker Desktop: Plex listens only on this address),
   and `BIND_IP`. `.env.example` explains each one.
2. `docker compose up -d`
3. Open `http://<SERVER_IP>:32400/web` (Linux: `http://localhost:32400/web`, or
   through `ssh -L 32400:localhost:32400 you@server` without a screen) and sign
   in. To skip the sign-in window entirely, put a token from
   [plex.tv/claim](https://plex.tv/claim) in `PLEX_CLAIM` before the first start
   (it works once and expires after 4 minutes; empty the line afterwards).
4. `docker compose run --rm bootstrap`

---

## What the bootstrap sets

| App | Settings |
|---|---|
| qBittorrent | Its own password (different from the other apps'); save path; **stop seeding at 100%**; UPnP off; skips programs, scripts, installers and archives inside torrents (`.exe`, `.scr`, `.lnk`, `.msi`, `.dmg`, `.pkg`, `.sh`, `.zip`, `.rar` and similar) |
| Radarr / Sonarr | Login required; root folder; qBittorrent with **remove after import**; renaming on (`Title (Year)`); 7-day recycle bin. They get no Plex token |
| Prowlarr | Login required; pushes indexers to Radarr and Sonarr |
| Seerr | Your Plex account as admin; Plex server and all its libraries; Radarr and Sonarr (1080p profile, library folders) as defaults; your Plex Watchlist turns into requests, approved automatically |
| Plex | Scans for new files itself (as soon as a change is detected, and every 15 minutes); secure connections **preferred**; media deletion off; DLNA off; home address `http://SERVER_IP:32400` published to your apps; remote access off (Linux: unless `PLEX_REMOTE_ACCESS=true`); Linux with Plex Pass: `LAN_SUBNET` marked as local |

---

## Folder layout

Everything lives under two folders, set in `.env`:

```
config/             app settings and databases (back this up)
data/
  media/movies/     your movie library      ← Plex reads this (read-only)
  media/tv/         your TV library         ← Plex reads this (read-only)
  torrents/         downloads in progress   ← only qBittorrent writes here
  recycle/          replaced files, kept 7 days
```

**Bringing an existing library:** move it into `data/media/movies` and
`data/media/tv`, or set `DATA_DIR` to a folder on your media drive that contains
`media/movies` and `media/tv`. Then in Radarr/Sonarr use **Library Import** with
**Monitor: None** so they don't re-download what you have.

Keep downloads and media on the **same drive** (both under `DATA_DIR`). Imports
are then instant hardlinks instead of slow copies.

---

## Security model

The guiding rule: **assume any one app could be compromised, and limit what it
could reach.**

| Protection | How |
|---|---|
| Nothing exposed to the internet by default | Only Plex listens beyond `BIND_IP`, and remote access is off. Don't port-forward anything. On Linux, with Plex Pass and remote streaming wanted, forward only TCP 32400. On macOS/Windows never forward it: every connection reaches Plex through the `plex-lan` relay and looks local, so remote access isn't supported there. The relay listens only on `SERVER_IP` (IPv4), not on IPv6 or other networks such as café Wi-Fi. For the same reason, never add anything to Plex's "List of IP addresses and networks that are allowed without auth" on macOS/Windows: it would let every device, internet ones included, in as you. |
| Web UIs stay private | Bound to `BIND_IP` (default `127.0.0.1`). Docker bypasses host firewalls, so this binding is what keeps them off the network. |
| Plex can't damage your media | The library is mounted **read-only** into Plex. Media deletion is off. |
| The torrent client is boxed in | qBittorrent only sees the downloads folder, never your library or settings, and has its own password, so it can't log into the other apps. No inbound peer port is opened. (Imported files are hardlinks: until Radarr/Sonarr remove the finished torrent, qBittorrent still shares the files it downloaded.) |
| Risky files are skipped | Programs, scripts, installers and archives inside torrents aren't downloaded. Radarr/Sonarr only import video files. |
| No privilege escalation | Every app runs as your user (`PUID`) from the start, with a read-only system, no Linux capabilities and `no-new-privileges`. |
| Logins everywhere | Every web UI requires a sign-in: a username and password, or your Plex account for Seerr (the bootstrap finishes Seerr's setup, so nobody else can claim it). |
| Optional VPN for torrents | `compose.vpn.yaml` adds a kill switch: if the VPN drops, qBittorrent can't reach the internet. |

**Keys to your Plex account.** Radarr and Sonarr never get your Plex sign-in
token: instead of them telling Plex to scan after each import, Plex watches the
library itself, so a new movie can take up to 15 minutes to appear. Seerr does
hold your Plex sign-in (the bootstrap gives it the server's, which Seerr needs to
read your Watchlist), so treat `config/seerr` as private. Turn on
two-factor authentication for your Plex account.

### Recommended host hardening (Linux)

These live outside Docker, so they're up to you:

```bash
# Automatic security updates
sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades

# Firewall: SSH and Plex from your LAN only (adjust the subnet)
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 32400 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 32410:32414 proto udp   # TVs finding Plex
sudo ufw allow from 172.16.0.0/12 to any port 32400 proto tcp          # the other containers
sudo ufw enable
```

Only with Plex Pass remote streaming (and TCP 32400 forwarded on your router),
widen the Plex rule to `sudo ufw allow 32400/tcp`.

Also: use SSH keys and set `PasswordAuthentication no` in `/etc/ssh/sshd_config`,
and turn on two-factor authentication for your Plex account.

---

## Updating

```bash
./setup.sh update
```

It pulls new versions, restarts the apps and offers to delete the old images
(by hand: `docker compose pull && docker compose up -d && docker image prune -f`).
Run it monthly, or schedule `./setup.sh update -y` (cron on Linux/macOS; on
Windows, Task Scheduler running `wsl -d Ubuntu -- ~/cinemarr/setup.sh update -y`).
Seerr and gluetun follow their `v3` releases; a new major version is a
deliberate change to `compose.yaml`.

`./setup.sh update` also recreates the `plex-lan` relay, because Compose doesn't
notice changes to its settings on its own (by hand:
`docker compose up -d --force-recreate plex-lan`).

**Back up** the `config/` folder occasionally. It holds all app settings.

---

## Tearing it down

Your media is never inside a container; it's plain files in a folder on your
computer. Tearing down the server leaves them where they are.

**1. Stop and remove the server.** From the project folder:

```bash
docker compose down              # stops and removes all the containers
docker compose down --rmi all    # same, and also deletes the downloaded images
```

Nothing under `DATA_DIR` or `CONFIG_DIR` is touched. To run it again later,
`docker compose up -d` picks up where you left off.

**2. Find your files.** The two folders are set in `.env`:

```bash
grep -E '^(DATA_DIR|CONFIG_DIR)=' .env      # Windows: findstr "DATA_DIR CONFIG_DIR" .env
```

With the defaults they're inside the project folder:

| Folder | What's in it | What to do with it |
|---|---|---|
| `data/media/movies`, `data/media/tv` | **Your library**, already named and sorted (`Movie (Year)/Movie (Year).mkv`) | Keep it. Copy it anywhere, or point any media player at it. |
| `data/torrents` | Unfinished or leftover downloads | Safe to delete. Finished files there are hardlinks to the library copy, so deleting them doesn't delete your movies. |
| `data/recycle` | Files Radarr/Sonarr replaced or removed in the last 7 days | Check it, then delete. |
| `config/` | App settings, Plex watch history and artwork | Delete it, or keep it to restore the server exactly as it was. |

**3. Move your library out before deleting the project folder.** With the
default `DATA_DIR=./data`, your media is *inside* the project folder, so
deleting the folder deletes your movies too. On Linux, if deleting `config/`
says "permission denied", use `sudo rm -rf config`.

**4. Optional: remove the server from your Plex account.** At
[app.plex.tv](https://app.plex.tv) → Settings → **Authorized Devices**, filter
by **Servers**, and remove it. Your TVs and phones then stop listing it.

---

## Optional: VPN for downloads

1. Pick a WireGuard VPN provider that
   [gluetun supports](https://github.com/qdm12/gluetun-wiki).
2. Fill in the `VPN_*` values in `.env`.
3. Add `:compose.vpn.yaml` to the end of `COMPOSE_FILE`.
4. `docker compose up -d --remove-orphans`
5. Check: `docker compose logs gluetun | grep -i "public ip"` should show the VPN's
   address, not yours. Then check qBittorrent itself:
   `docker compose exec qbittorrent wget -qO- https://ipinfo.io/ip` should print
   the same VPN address.
6. Optional second layer: in qBittorrent → Settings → Advanced, set
   **Network interface** to `tun0`, so it can't send anything outside the tunnel
   even if the firewall were changed.

Nothing else changes; Radarr and Sonarr keep using the host name `qbittorrent`.

---

## Troubleshooting

**TV or browser says the server is offline, or a TV at home asks for a "Remote
Watch Pass".**
Many ISP routers (AT&T, Comcast and others) block a DNS trick Plex relies on,
called DNS rebinding protection. Your device can't find the server's local
address, falls back to your public IP, and Plex treats it as remote. Fix it on
the affected device by setting its DNS server to `1.1.1.1` (Cloudflare) or
`8.8.8.8` (Google). Check whether you're affected:

```bash
# Replace with your server's IP (dashes) and Plex ID. Find the ID with:
#   docker compose exec plex sh -c "echo | openssl s_client -connect 127.0.0.1:32400 2>/dev/null | openssl x509 -noout -ext subjectAltName"
nslookup 192-168-1-50.<your-id>.plex.direct            # via your router
nslookup 192-168-1-50.<your-id>.plex.direct 1.1.1.1    # via Cloudflare
```

If the first returns nothing and the second returns your server's IP, change DNS
on your TVs and computers. This also matters for privacy: without it, devices
fall back to plain `http://`, and anyone on your Wi-Fi could read their Plex
sign-in tokens.

**A TV or phone lists the new server but says it's unavailable.** The app is
still using what it learned from your Plex account before the server was set
up. Sign out of the Plex app on that device and sign back in (restarting the
app isn't enough). Also run the bootstrap first: on macOS/Windows it's what
tells your apps the server's home address (`SERVER_IP`).

**Bootstrap can't log into qBittorrent.** After several failed logins qBittorrent
temporarily bans the address. Run `docker compose restart qbittorrent` and try again.

**Radarr says the download folder doesn't exist.** Run `docker compose up -d`
again; the `init` step recreates missing folders.

**`init: refusing to run`.** `CONFIG_DIR` or `DATA_DIR` in `.env` points at a
folder that looks like a system or home folder (it contains `etc`, `bin`, `usr`
or `.ssh`), or one of the stack's folders is a symlink. The setup hands those
folders to `PUID`, so point them at folders used only by this stack.

**A file you copied in doesn't show up in Plex, or won't play.** Plex can only
see files it's allowed to read, and files from cameras, phones or AirDrop are
often private to your user. From the project folder, run:
`chmod -R a+rX data/media` (macOS/Linux), then in Plex use **Scan Library Files**.
On Linux you can instead give the files to `PUID`:
`sudo chown -R $(id -u):$(id -g) data/media`.

**Library Import shows empty matches.** Reload the page and scroll slowly; Radarr
matches in batches. Unmatched rows can be fixed by searching in the Movie column.

---

## Platform notes

| | Linux | macOS | Windows |
|---|---|---|---|
| Runs the full stack | Yes | Yes | Yes |
| TVs find Plex automatically | Yes (with ufw, allow UDP 32410-32414 from your LAN) | Apps signed in to your Plex account find it (set `SERVER_IP`) | Same as macOS |
| Plex knows devices are local | Yes | Yes, through the `plex-lan` relay | Same as macOS |
| Remote streaming (Plex Pass) | Optional | Not supported | Not supported |
| GPU transcoding | Yes (`compose.gpu.yaml` and `RENDER_GID`, needs Plex Pass) | No | No |
| Instant hardlink imports | Yes | Yes | Yes, with files inside Ubuntu (not `/mnt/c`) |

**Why macOS/Windows use a relay:** Docker Desktop runs containers in a small VM
and hides each device's address, so Plex would see every TV and phone as one
outside address. It would then treat them as remote (asking for a Remote Watch
Pass) and refuse the first sign-in. The `plex-lan` service passes connections
through Plex's own Docker network, which Plex counts as local. The catch is that
internet connections would look local too, which is why remote access stays off
on these systems, the relay listens only on `SERVER_IP`, and 32400 must never be
port-forwarded.

On Windows, keep the project and `DATA_DIR` inside Ubuntu (your Linux home
folder). A folder on a Windows drive (`/mnt/c`, `/mnt/d`) works but is slower,
imports become copies, and Plex only notices new files on its 15-minute scan.
Untested so far: whether Windows Firewall lets other devices reach Plex without
a prompt. If TVs can't connect, allow Docker Desktop through the firewall for
private networks.

Docker Desktop on **Linux** behaves like macOS (relay setup, no host
networking); `setup.sh` detects it and picks the right files.

---

## A note on legality

This software is legal. Whether using it is legal depends on what you download.
Downloading copyrighted movies and shows you don't have the rights to is copyright
infringement in most countries, whether or not you seed, and BitTorrent shares
pieces with other peers while downloading. Public-domain and openly licensed
works are fine. You're responsible for what you point it at.

## License

MIT. See [LICENSE](LICENSE).
