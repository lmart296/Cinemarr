# Moving an existing hand-built server onto Cinemarr

A worked example, with example paths and addresses (use your own): a server that already runs Radarr, Sonarr, Prowlarr,
qBittorrent and Seerr from its own `docker-compose.yml`, with Plex installed
natively. It moves onto this repo **without moving any media** and keeps every
app's settings, history and library.

The example server:

| | |
|---|---|
| Old compose file | `/opt/mediaserver/docker-compose.yml` (plus an optional VPN file), API keys pinned through environment variables, values in `/opt/mediaserver/.env` |
| App settings | `/opt/mediaserver/{qbittorrent,prowlarr,radarr,sonarr,seerr,gluetun}` |
| Data | `/srv/media` (its own drive, NTFS), mounted at `/data` in the containers; library in `Videos/Movies` and `Videos/TV Shows`, downloads in `torrents/{radarr,sonarr}` |
| User | the `media` system user (PUID 1001, PGID 1001 in this example), `UMASK=027`; web UIs on 192.168.1.50 |
| Plex | native `plexmediaserver` package, sandboxed with a systemd drop-in |

Read the whole guide once before starting. The cutover itself takes a few
minutes; the apps are down only between steps 5 and 7.

---

## What will change, and what won't

**Unchanged:** your media, the app databases (history, monitored movies and
series, indexers), the paths inside the containers (`/data/...` stays
`/data/...`), and Plex's library and watch history.

**Changed by the `init` step** (runs on every `docker compose up`):

| Path | What happens |
|---|---|
| `/srv/media`, `Videos`, `Videos/Movies`, `Videos/TV Shows` | These **folders** (not what's in them) get owner `media:media` (PUID:PGID), and owner and group get read/write/enter. Nothing is taken away, and nothing is added for other users. |
| Files and folders inside the library | **Never touched.** `init` doesn't walk into the library. |
| `torrents/` and `recycle/` | Everything inside is given to `media:media` (already the case on your server). |
| `incoming/`, `recycle/` | Created if missing. |
| `/opt/mediaserver` (CONFIG_DIR) | Everything inside is given to `media:media`, and the folder becomes `700`. **Move the old `docker-compose.yml` and `.env` out of it first** (step 6): otherwise they'd become owned by `media`, and the bootstrap, which can read all of CONFIG_DIR, would see the API keys in that `.env`. |

Two things to check about that table:

- **Group ownership of the library folders.** If Plex currently reads the
  library through a *different* group (say the folders are `root:plex 750`),
  changing their group to `media` would lock Plex out. Check in step 0, and
  make sure `plex` is in `media`'s group.
- **NTFS.** On an NTFS drive, owners and permissions come from the mount
  options, so `chown`/`chmod` may not stick. `init` then only prints a warning;
  nothing breaks. (Moving the data to ext4 later avoids this and the "dirty
  volume" problem; see the README.)

**Changed by the bootstrap** (it configures every app; review this list):

- **Logins:** it asks for a username and two passwords and **sets** them: one
  for Radarr, Sonarr and Prowlarr, and a different one for qBittorrent.
  Type your current ones to keep them, but qBittorrent's must differ from the
  others; if yours is the same today, it changes.
- **Radarr and Sonarr:**
  - the **HD-1080p** profile is overwritten (1080p only, no remux), with
    1080p file sizes;
  - renaming on, recycle bin `/data/recycle` (7 days), `.srt` subtitles
    imported, and folders rescanned only after a manual refresh;
  - any Plex connection is **removed**: Plex finds new files on its own;
  - the download client named **qBittorrent** is created or updated. If yours
    has a different name, you'll end up with two; delete the old one.
- **qBittorrent:** never seeds, UPnP off, programs and archives in torrents
  skipped, save path `/data/torrents`, categories `radarr`/`sonarr`.
- **Prowlarr:**
  - every indexer needs 10+ seeders;
  - the apps named **Radarr** and **Sonarr** are created or updated (same
    caveat about other names).
- **Seerr:**
  - Plex server `host.docker.internal:32400`;
  - its Radarr/Sonarr entries (matched by host name `radarr`/`sonarr`) switch
    to HD-1080p;
  - your Watchlist auto-requests;
  - **Auto-Request is removed from every other user.**
- **Your native Plex:**
  - libraries kept (it only adds Movies/TV if missing);
  - scans only on change (no scheduled scans);
  - DLNA off, secure connections preferred;
  - **remote access off unless `PLEX_REMOTE_ACCESS=true`** in `.env`.

**Other differences from the old setup:**

- **Non-root containers:** the apps now run as `media` from the start, with a
  read-only system and no extra Linux permissions.
- **New files are `644` instead of `640`:** in that mode the images ignore
  `UMASK`, so new files are readable by every user on the server, not just
  `media`'s group. Plex can read them either way.
- **Database upgrades are one-way:** the images are current versions. If yours
  are much older, Radarr, Sonarr, Prowlarr or Seerr may upgrade their
  databases on first start, which the old images can't read. The backup in
  step 1 covers that.

---

## Step 0: checks (nothing changes yet)

```bash
id media                     # confirm PUID/PGID (note the uid and gid)
id plex                      # is plex in media's group? (needed to read 640 files)
sudo stat -c '%U:%G %a %n' /srv/media /srv/media/Videos \
  "/srv/media/Videos/Movies" "/srv/media/Videos/TV Shows" \
  /srv/media/torrents        # note these: they're what init may change
findmnt /srv/media       # the drive's filesystem and mount options
```

If `plex` isn't in `media`'s group and the library folders are group-only:
`sudo usermod -aG media plex && sudo systemctl restart plexmediaserver`.

**Pinned API keys.** Your old compose passes each app's API key as an
environment variable (for example `RADARR__AUTH__APIKEY`). Cinemarr doesn't;
each app then uses the key in its own `config.xml`. If the two differ, the key
changes at cutover. The bootstrap updates Prowlarr and Seerr to match, but
anything else using the old key (scripts, dashboards) would break. Compare
them without printing either key:

```bash
for app in radarr sonarr prowlarr; do
  if ! sudo grep -q '<ApiKey>' /opt/mediaserver/$app/config.xml; then
    echo "$app: no key in config.xml (it gets the pinned one in step 5)"
  elif sudo sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' /opt/mediaserver/$app/config.xml \
       | sudo grep -qFf - /opt/mediaserver/.env; then
    echo "$app: same key"
  else
    echo "$app: DIFFERENT"
  fi
done
```

For any app showing `DIFFERENT`, after step 5 (apps stopped) put the pinned key
into `<ApiKey>…</ApiKey>` in that app's `config.xml` with `sudo nano`.

**Remote access.** If you stream Plex away from home, you'll set
`PLEX_REMOTE_ACCESS=true` in step 2.

## Step 1: back up

```bash
sudo tar -C /opt -czpf /root/mediaserver-$(date +%F).tar.gz mediaserver
sudo cp -p "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml" \
  /root/plex-Preferences-$(date +%F).xml
```

The first backup includes the old compose file and `.env`, so rollback can use
them. The second is Plex's settings, for undoing the bootstrap's Plex changes.

## Step 2: get the repo and write `.env`

```bash
git clone https://github.com/lmart296/Cinemarr.git ~/cinemarr
cd ~/cinemarr
cp .env.example .env && chmod 600 .env
nano .env
```

Set these (everything else can stay as in `.env.example`):

```bash
COMPOSE_FILE=compose.yaml:compose.linux.yaml:compose.external-plex.yaml
COMPOSE_PATH_SEPARATOR=:
TZ=America/New_York
PUID=1001                      # yours, from 'id media'
PGID=1001
CONFIG_DIR=/opt/mediaserver
DATA_DIR=/srv/media
MOVIES_DIR=Videos/Movies
TV_DIR=Videos/TV Shows
REQUIRE_DATA_MARKER=true      # separate drive: refuse to start without it
BIND_IP=192.168.1.50
SERVER_IP=192.168.1.50
LAN_SUBNET=192.168.1.0/24
PLEX_REMOTE_ACCESS=false      # true if you stream away from home (Plex Pass)
```

If the old setup uses the VPN file, also add `:compose.vpn.yaml` to the end of
`COMPOSE_FILE` and copy the `VPN_*` and `WIREGUARD_*` values from
`/opt/mediaserver/.env`. The gluetun settings in `/opt/mediaserver/gluetun` are
reused.

## Step 3: create the drive marker

```bash
sudo touch /srv/media/.cinemarr-data
```

With `REQUIRE_DATA_MARKER=true`, the stack refuses to start when this file is
missing, i.e. when the drive isn't mounted. For reboots, also add
`RequiresMountsFor=/srv/media` to `docker.service` (README, "Media on its
own drive").

## Step 4: check the result before stopping anything

```bash
cd ~/cinemarr
docker compose config --services       # expect: init prowlarr qbittorrent radarr seerr sonarr (no plex)
docker compose config | grep -E 'source: /|target: /data|published'
```

(Use `sudo docker …` throughout if your user isn't in the `docker` group.)

## Step 5: stop the old stack

```bash
sudo docker compose -f /opt/mediaserver/docker-compose.yml down
# with the VPN file:
# sudo docker compose -f /opt/mediaserver/docker-compose.yml -f /opt/mediaserver/<vpn file> down
```

Now, if step 0 showed a `DIFFERENT` API key, fix that `config.xml`.

## Step 6: move the old compose files out of CONFIG_DIR

```bash
sudo mkdir -p /root/mediaserver-old
sudo mv /opt/mediaserver/docker-compose.yml /opt/mediaserver/.env /root/mediaserver-old/
# and the VPN compose file, if any
ls -A /opt/mediaserver   # should be only the app folders
```

## Step 7: start Cinemarr

```bash
cd ~/cinemarr
docker compose up -d
docker compose ps -a                   # all "running"/"healthy"; init "exited (0)"
docker compose logs init               # warnings about chown on NTFS are harmless
```

## Step 8: run the bootstrap

```bash
docker compose run --rm bootstrap
```

Safe to re-run. It reads each app's API key from its `config.xml`. When it
can't read Plex's `Preferences.xml` (the native package keeps it private), it
asks you to paste the token and shows the `sudo grep` command that prints it;
the token is used for that run only.

## Step 9: verify

- **The web UIs:** `http://192.168.1.50:7878` (Radarr), `:8989` (Sonarr),
  `:9696` (Prowlarr), `:8080` (qBittorrent), `:5055` (Seerr). Each should open
  with its usual data. In Radarr and Sonarr, **System → Status** should show no
  new errors.
- **Connections:**
  - Radarr/Sonarr → **Settings → Download Clients** → test qBittorrent;
  - Prowlarr → **Settings → Apps** → test Radarr and Sonarr;
  - Seerr → **Settings → Services** → test both.
- **A test download:** add a public-domain film (e.g. *Night of the Living
  Dead*, 1968) to your Plex Watchlist. It should go through Seerr → Radarr →
  qBittorrent, then land in `/srv/media/Videos/Movies/...`.
- **The Plex refresh:** the film should appear in Plex within seconds of the
  import, with no manual scan. That's Plex noticing the new file; there's no
  longer a Radarr → Plex connection. If it doesn't, check that the folder is
  readable by `plex` (`sudo -u plex ls "/srv/media/Videos/Movies"`) and
  that the systemd sandbox lets Plex see it.

---

## Rollback

```bash
# 1. Stop Cinemarr
cd ~/cinemarr && docker compose down

# 2. Put the old configs back (keeps the tried ones aside, in case)
sudo mv /opt/mediaserver /opt/mediaserver.cinemarr-try
sudo tar -C /opt -xzpf /root/mediaserver-<date>.tar.gz

# 3. Start the old stack
sudo docker compose -f /opt/mediaserver/docker-compose.yml up -d

# 4. Undo the bootstrap's Plex settings
sudo systemctl stop plexmediaserver
sudo cp -p /root/plex-Preferences-<date>.xml \
  "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml"
sudo systemctl start plexmediaserver
```

Your media is never changed, so there's nothing to restore there. The only
traces on the data drive are the folder owners and permissions from the table
above (put them back from your step 0 notes with `sudo chown` / `sudo chmod` if
you want), plus the new `incoming/`, `recycle/` and `.cinemarr-data`, which you
can delete.
