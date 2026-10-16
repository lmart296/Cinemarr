# cinemarr

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
  - macOS / Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A computer that stays on, with space for your media.

Linux gives the best results: Plex sees your devices directly, TVs find it
automatically, and GPU transcoding works. macOS and Windows work too, with the
limits listed under [Platform notes](#platform-notes).

---

## Quick start

### 1. Get the files and create your settings

```bash
git clone https://github.com/lmart296/cinemarr.git
cd cinemarr
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env` in a text editor and set at least:

- `COMPOSE_FILE`: on **Linux**, use `compose.yaml:compose.linux.yaml`
- `TZ`: your time zone
- `PUID` / `PGID`: on **Linux**, the output of `id -u` and `id -g`
- `BIND_IP`: your server's LAN IP if you want to open the web UIs from other
  devices at home, or leave `127.0.0.1` for this computer only
- `LAN_SUBNET`: your home network, usually `192.168.1.0/24`

### 2. Start everything

```bash
docker compose up -d
docker compose ps -a     # everything should say "running" (init says "exited (0)")
```

### 3. Sign in to Plex

On **macOS and Windows**, Plex must be linked to your account with a claim
token: get one at [plex.tv/claim](https://plex.tv/claim), put it in `PLEX_CLAIM`
in `.env`, and run `docker compose up -d` within 4 minutes. (Optional on Linux.)

Open `http://<server-ip>:32400/web` **from the server itself** (or a browser on
the same network) and sign in. Then add two libraries:

- **Movies** → `/data/media/movies`
- **TV Shows** → `/data/media/tv`

### 4. Configure the rest automatically

```bash
docker compose run --rm bootstrap
```

It asks for one username and password to use for qBittorrent, Radarr, Sonarr and
Prowlarr, then configures everything listed under [What the bootstrap sets](#what-the-bootstrap-sets).
It's safe to re-run at any time.

On a brand-new install, qBittorrent has a temporary password. The script will
ask for it; get it in another terminal with:

```bash
docker compose logs qbittorrent | grep -i password
```

### 5. Finish the two manual steps

The bootstrap prints these at the end, with your API keys filled in.

1. **Prowlarr** → Indexers → add your search sources. They sync to Radarr and Sonarr.
   Under each indexer's advanced settings, set **Apps Minimum Seeders** to about `10`;
   well-seeded releases are faster and far less likely to be fake.
2. **Seerr** → sign in with Plex, connect it to Radarr and Sonarr, then turn on
   **Auto-Request** (Users → your user → Settings) and **Auto-Approve**
   (Permissions).

### 6. Test it

Add a public-domain film (for example *Night of the Living Dead*, 1968) to your
Plex Watchlist. Within a few minutes it should appear in Seerr → Radarr →
qBittorrent, stop at 100%, disappear from qBittorrent, and show up in Plex.

---

## What the bootstrap sets

| App | Settings |
|---|---|
| qBittorrent | Login; save path; **stop seeding at 100%**; UPnP off; refuses to download `.exe`, `.bat`, `.scr`, `.lnk`, `.msi` and similar files inside torrents |
| Radarr / Sonarr | Login required; root folder; qBittorrent with **remove after import**; renaming on (`Title (Year)`); 7-day recycle bin; Plex library refresh after each import |
| Prowlarr | Login required; pushes indexers to Radarr and Sonarr |
| Plex | Your LAN marked as local; secure connections **preferred**; media deletion off; DLNA off; remote access off (unless `PLEX_REMOTE_ACCESS=true`) |

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
| Nothing exposed to the internet by default | Only Plex listens beyond `BIND_IP`, and remote access is off. Don't port-forward anything unless you have Plex Pass and want remote streaming, and then only TCP 32400. |
| Web UIs stay private | Bound to `BIND_IP` (default `127.0.0.1`). Docker bypasses host firewalls, so this binding is what keeps them off the network. |
| Plex can't damage your media | The library is mounted **read-only** into Plex. Media deletion is off. |
| The torrent client is boxed in | qBittorrent only sees the downloads folder, never your library or settings. No inbound peer port is opened. |
| Risky files never land on disk | Executables and scripts inside torrents are skipped. Radarr/Sonarr only import video files. |
| No privilege escalation | Containers run as your user (`PUID`) with `no-new-privileges`; Seerr also drops all Linux capabilities. |
| Logins everywhere | Every web UI requires a username and password. |
| Optional VPN for torrents | `compose.vpn.yaml` adds a kill switch: if the VPN drops, qBittorrent has no network at all. |

### Recommended host hardening (Linux)

These live outside Docker, so they're up to you:

```bash
# Automatic security updates
sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades

# Firewall: SSH from your LAN only (adjust the subnet)
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow 32400/tcp
sudo ufw enable
```

Also: use SSH keys and set `PasswordAuthentication no` in `/etc/ssh/sshd_config`,
and turn on two-factor authentication for your Plex account.

---

## Updating

```bash
docker compose pull
docker compose up -d
docker image prune -f
```

Run it monthly, or schedule it (cron on Linux/macOS, Task Scheduler on Windows).

**Back up** the `config/` folder occasionally. It holds all app settings.

---

## Optional: VPN for downloads

1. Pick a WireGuard VPN provider that
   [gluetun supports](https://github.com/qdm12/gluetun-wiki).
2. Fill in the `VPN_*` values in `.env`.
3. Add `:compose.vpn.yaml` to the end of `COMPOSE_FILE`.
4. `docker compose up -d --remove-orphans`
5. Check: `docker compose logs gluetun | grep -i "public ip"` should show the VPN's
   address, not yours.

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
on your TVs and computers.

**Bootstrap can't log into qBittorrent.** After several failed logins qBittorrent
temporarily bans the address. Run `docker compose restart qbittorrent` and try again.

**Radarr says the download folder doesn't exist.** Run `docker compose up -d`
again; the `init` step recreates missing folders.

**Plex shows a movie but it won't play / "permission denied".** On Linux, make
sure your existing media is readable by `PUID`:
`sudo chown -R $(id -u):$(id -g) data/media`.

**Library Import shows empty matches.** Reload the page and scroll slowly; Radarr
matches in batches. Unmatched rows can be fixed by searching in the Movie column.

---

## Platform notes

| | Linux | macOS | Windows |
|---|---|---|---|
| Runs the full stack | Yes | Yes | Yes |
| TVs find Plex automatically | Yes | Usually not; add the server by IP in the TV app if needed | Same as macOS |
| Plex knows devices are local | Yes | No: Docker Desktop hides device addresses, so home devices count as remote | Same as macOS |
| GPU transcoding | Yes (`compose.gpu.yaml`, needs Plex Pass) | No | No |
| Instant hardlink imports | Yes | Yes | Often falls back to copying |

On Windows, keep the project folder on a normal drive like `C:\cinemarr`, and
store media under `DATA_DIR` rather than a network share.

---

## A note on legality

This software is legal. Whether using it is legal depends on what you download.
Downloading copyrighted movies and shows you don't have the rights to is copyright
infringement in most countries, whether or not you seed, and BitTorrent shares
pieces with other peers while downloading. Public-domain and openly licensed
works are fine. You're responsible for what you point it at.

## License

MIT. See [LICENSE](LICENSE).
