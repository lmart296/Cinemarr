#!/usr/bin/env python3
"""Configure the stack through each app's API. Safe to re-run.

    docker compose run --rm bootstrap

Runs inside a container (python:3.12-alpine), so it works the same on
Linux, macOS and Windows. Standard library only.

Sets up:
  qBittorrent  login, save path, never-seed limits, UPnP off, blocked file types
  Radarr       login, root folder, qBittorrent client (remove on import),
               renaming, recycle bin, Plex library refresh
  Sonarr       same as Radarr, for TV
  Prowlarr     login, sync to Radarr + Sonarr
  Plex         secure connections preferred, media deletion off, DLNA off,
               home address (SERVER_IP), LAN subnet (Linux), remote access
               off (unless PLEX_REMOTE_ACCESS=true, Linux only)
Left for you: indexers in Prowlarr, Plex libraries, and Seerr's first-run sign-in.
"""
import getpass
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CFG = Path("/cfg")
PLEX_HOST = os.environ.get("PLEX_HOST", "plex")
LAN_SUBNET = os.environ.get("LAN_SUBNET", "192.168.1.0/24")
PLEX_REMOTE = os.environ.get("PLEX_REMOTE_ACCESS", "false").lower() in ("1", "true", "yes")
# macOS/Windows: devices reach Plex through the plex-lan relay (see compose.yaml).
PLEX_VIA_RELAY = os.environ.get("PLEX_VIA_RELAY", "false").lower() in ("1", "true", "yes")
SERVER_IP = os.environ.get("SERVER_IP", "").strip()
PLEX_PREFS = CFG / "plex/Library/Application Support/Plex Media Server/Preferences.xml"

BLOCKED_FILES = "\n".join(
    ["*.exe", "*.scr", "*.bat", "*.cmd", "*.com", "*.lnk", "*.vbs", "*.js",
     "*.msi", "*.ps1", "*.jar", "*.iso", "*.zipx"]
)


def log(msg):
    print(f"\n==> {msg}")


def ok(msg):
    print(f"    ✓ {msg}")


def warn(msg):
    print(f"    ! {msg}")


# ---------------------------------------------------------------- HTTP helpers
def request(method, url, *, headers=None, data=None, form=None, opener=None):
    hdrs = {"Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with (opener or urllib.request.build_opener()).open(req, timeout=30) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return raw


def step(desc, fn, *args, **kwargs):
    """Run one setup step; report errors and keep going."""
    try:
        fn(*args, **kwargs)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        warn(f"{desc} failed (HTTP {e.code}): {detail}")
    except Exception as e:  # noqa: BLE001
        warn(f"{desc} failed: {e}")


def wait_for(name, url, headers=None, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            request("GET", url, headers=headers)
            ok(f"{name} is up")
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):  # responding, just wants auth
                ok(f"{name} is up")
                return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(3)
    warn(f"{name} didn't respond at {url}. Is it running? (docker compose ps)")
    return False


def read_api_key(app):
    """The *arr apps write their API key to config.xml on first start."""
    path = CFG / app / "config.xml"
    deadline = time.time() + 120
    while time.time() < deadline:
        if path.exists():
            m = re.search(r"<ApiKey>([^<]+)</ApiKey>", path.read_text(errors="replace"))
            if m:
                return m.group(1)
        time.sleep(3)
    sys.exit(f"No API key in {path}. Start the stack first: docker compose up -d")


# ---------------------------------------------------------------- *arr apps
class Arr:
    def __init__(self, name, port, api_version):
        self.name = name
        self.key = read_api_key(name.lower())
        self.base = f"http://{name.lower()}:{port}/api/{api_version}"
        self.headers = {"X-Api-Key": self.key}

    def get(self, path):
        return request("GET", self.base + path, headers=self.headers)

    def post(self, path, data):
        return request("POST", self.base + path, headers=self.headers, data=data)

    def put(self, path, data):
        return request("PUT", self.base + path, headers=self.headers, data=data)

    def wait(self):
        return wait_for(self.name, self.base + "/system/status", self.headers)

    def set_login(self, user, password):
        cfg = self.get("/config/host")
        cfg.update(authenticationMethod="forms", authenticationRequired="enabled",
                   username=user, password=password, passwordConfirmation=password)
        self.put(f"/config/host/{cfg['id']}", cfg)
        ok(f"{self.name}: login set")

    def patch_config(self, section, label, **changes):
        cfg = self.get(f"/config/{section}")
        missing = [k for k in changes if k not in cfg]
        cfg.update({k: v for k, v in changes.items() if k in cfg})
        self.put(f"/config/{section}/{cfg['id']}", cfg)
        ok(f"{self.name}: {label}")
        for k in missing:
            warn(f"{self.name}: setting '{k}' not found in this version, skipped")

    def ensure_root_folder(self, path):
        if any(r["path"].rstrip("/") == path for r in self.get("/rootfolder")):
            ok(f"{self.name}: root folder {path} already set")
            return
        self.post("/rootfolder", {"path": path})
        ok(f"{self.name}: root folder {path} added")

    def upsert(self, kind, implementation, name, fields, **top_level):
        """Create or update a provider (download client, notification, app)."""
        existing = next((x for x in self.get(f"/{kind}") if x.get("name") == name), None)
        item = existing or next(
            s for s in self.get(f"/{kind}/schema") if s["implementation"] == implementation)
        known = {f["name"] for f in item["fields"]}
        for f in item["fields"]:
            if f["name"] in fields:
                f["value"] = fields[f["name"]]
        for missing in set(fields) - known:
            warn(f"{self.name}: field '{missing}' not found on {implementation}, skipped")
        item.update(name=name, **top_level)
        if existing:
            self.put(f"/{kind}/{existing['id']}", item)
            ok(f"{self.name}: {name} updated")
        else:
            item.pop("id", None)
            self.post(f"/{kind}", item)
            ok(f"{self.name}: {name} added")


# ---------------------------------------------------------------- qBittorrent
def configure_qbittorrent(user, password):
    base = "http://qbittorrent:8080/api/v2"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def login(u, p, verbose=False):
        # Success = a session cookie. Works whether qBittorrent replies
        # 200 "Ok." (older) or 204 with no body (newer).
        jar.clear()
        try:
            request("POST", base + "/auth/login", form={"username": u, "password": p},
                    opener=opener)
        except urllib.error.HTTPError as e:
            if verbose:
                warn("qBittorrent banned this address after failed logins. Run "
                     "'docker compose restart qbittorrent' and try again."
                     if e.code == 403 else f"qBittorrent login returned HTTP {e.code}")
            return False
        return any("SID" in c.name for c in jar)

    if not login(user, password):
        print("    qBittorrent doesn't accept that login yet. On first run it prints a")
        print("    temporary password. In another terminal, run:")
        print("      docker compose logs qbittorrent | grep -i password")
        cur_user = input("    Current qBittorrent username [admin]: ").strip() or "admin"
        cur_pw = getpass.getpass("    Current qBittorrent password: ")
        if not login(cur_user, cur_pw, verbose=True):
            warn("Couldn't log into qBittorrent, skipping it")
            return
    ok("qBittorrent: logged in")

    prefs = {
        "web_ui_username": user,
        "web_ui_password": password,
        "save_path": "/data/torrents",
        "temp_path_enabled": False,
        # Category save paths (/data/torrents/radarr, .../sonarr) only apply in
        # automatic torrent management mode.
        "auto_tmm_enabled": True,
        # Never seed: stop the moment a download completes.
        "max_ratio_enabled": True,
        "max_ratio": 0,
        "max_seeding_time_enabled": True,
        "max_seeding_time": 0,
        "max_ratio_act": 0,  # 0 = stop torrent
        "upnp": False,
        # Never download executables and other risky files inside torrents.
        "excluded_file_names_enabled": True,
        "excluded_file_names": BLOCKED_FILES,
    }
    request("POST", base + "/app/setPreferences", form={"json": json.dumps(prefs)},
            opener=opener)
    ok("qBittorrent: login, save path, never-seed, UPnP off, blocked file types")

    for cat in ("radarr", "sonarr"):
        try:
            request("POST", base + "/torrents/createCategory",
                    form={"category": cat, "savePath": f"/data/torrents/{cat}"},
                    opener=opener)
            ok(f"qBittorrent: category '{cat}' created")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                ok(f"qBittorrent: category '{cat}' already exists")
            else:
                raise


# ---------------------------------------------------------------- Plex
def plex_token():
    if not PLEX_PREFS.exists():
        return None
    m = re.search(r'PlexOnlineToken="([^"]+)"', PLEX_PREFS.read_text(errors="replace"))
    return m.group(1) if m else None


def configure_plex(token):
    base = f"http://{PLEX_HOST}:32400"
    if not wait_for("Plex", f"{base}/identity", timeout=120):
        return
    remote = PLEX_REMOTE
    if remote and PLEX_VIA_RELAY:
        # Behind the relay every connection looks local, including ones from
        # the internet, so remote access stays off.
        warn("PLEX_REMOTE_ACCESS is ignored on macOS/Windows; remote access stays off")
        remote = False
    prefs = {
        "secureConnections": 1,              # 1 = Preferred
        "allowMediaDeletion": 0,
        "DlnaEnabled": 0,                    # DLNA has no login; keep it off
        "PublishServerOnPlexOnlineKey": 1 if remote else 0,  # remote access
    }
    if not PLEX_VIA_RELAY:
        # Which addresses count as "local" (Plex Pass only). Behind the relay
        # Plex's default, its own network, is already right.
        prefs["LanNetworksBandwidth"] = LAN_SUBNET
    if SERVER_IP:
        # Tell apps where to find the server on your home network.
        prefs["customConnections"] = f"http://{SERVER_IP}:32400"
    for key, value in prefs.items():
        step(f"Plex {key}", request, "PUT",
             f"{base}/:/prefs?{urllib.parse.urlencode({key: value, 'X-Plex-Token': token})}")
    ok(f"Plex: secure connections preferred, deletion off, DLNA off, "
       f"remote access {'on' if remote else 'off'}"
       + (f", address http://{SERVER_IP}:32400" if SERVER_IP else ""))
    if PLEX_VIA_RELAY and not SERVER_IP:
        warn("SERVER_IP isn't set in .env, so TVs and phones may not find Plex "
             "on your network. Set it and re-run.")


# ---------------------------------------------------------------- main
def main():
    print("One login is used for qBittorrent, Radarr, Sonarr and Prowlarr.")
    while True:
        user = input("Username [admin]: ").strip() or "admin"
        # qBittorrent rejects usernames under 3 characters or with a colon.
        if len(user) >= 3 and ":" not in user:
            break
        print("Use at least 3 characters and no colon. Try again.")
    while True:
        password = getpass.getpass("Password (8+ characters): ")
        if len(password) >= 8 and password == getpass.getpass("Confirm: "):
            break
        print("Passwords didn't match or were under 8 characters. Try again.")

    log("Reading API keys and waiting for services")
    radarr = Arr("Radarr", 7878, "v3")
    sonarr = Arr("Sonarr", 8989, "v3")
    prowlarr = Arr("Prowlarr", 9696, "v1")
    wait_for("qBittorrent", "http://qbittorrent:8080/api/v2/app/version")
    for app in (radarr, sonarr, prowlarr):
        app.wait()

    token = plex_token()

    log("qBittorrent")
    step("qBittorrent setup", configure_qbittorrent, user, password)

    for app, root, cat_field, category in (
        (radarr, "/data/media/movies", "movieCategory", "radarr"),
        (sonarr, "/data/media/tv", "tvCategory", "sonarr"),
    ):
        log(app.name)
        step("login", app.set_login, user, password)
        step("root folder", app.ensure_root_folder, root)
        rename = "renameMovies" if app is radarr else "renameEpisodes"
        step("renaming", app.patch_config, "naming", "renaming on", **{rename: True})
        step("recycle bin", app.patch_config, "mediamanagement",
             "recycle bin /data/recycle (7 days)",
             recycleBin="/data/recycle", recycleBinCleanupDays=7)
        step("download client", app.upsert, "downloadclient", "QBittorrent", "qBittorrent",
             {"host": "qbittorrent", "port": 8080, "username": user, "password": password,
              cat_field: category},
             enable=True, protocol="torrent", priority=1,
             removeCompletedDownloads=True, removeFailedDownloads=True)
        if token:
            step("Plex connection", app.upsert, "notification", "PlexServer", "Plex",
                 {"host": PLEX_HOST, "port": 32400, "authToken": token, "updateLibrary": True},
                 onDownload=True, onUpgrade=True, onRename=True)

    log("Prowlarr")
    step("login", prowlarr.set_login, user, password)
    for app, port in ((radarr, 7878), (sonarr, 8989)):
        step(f"{app.name} sync", prowlarr.upsert, "applications", app.name, app.name,
             {"prowlarrUrl": "http://prowlarr:9696",
              "baseUrl": f"http://{app.name.lower()}:{port}",
              "apiKey": app.key},
             syncLevel="fullSync")

    log("Plex")
    if token:
        configure_plex(token)
    else:
        warn("Plex isn't signed in yet. Open http://localhost:32400/web on the server, "
             "sign in, then re-run this script to finish Plex settings and Radarr/Sonarr "
             "refresh.")

    plex_addr = PLEX_HOST
    print(f"""
Done. Left for you:

  1. Plex      http://<server-ip>:32400/web
               Add libraries: Movies -> /data/media/movies, TV Shows -> /data/media/tv

  2. Prowlarr  http://<BIND_IP>:9696 -> Indexers -> add your sources.
               They sync to Radarr and Sonarr automatically.

  3. Seerr     http://<BIND_IP>:5055 -> sign in with Plex. Plex server: host
               {plex_addr}, port 32400, SSL off. Then add:
                 Radarr: host radarr, port 7878, API key {radarr.key},
                         root folder /data/media/movies, default server
                 Sonarr: host sonarr, port 8989, API key {sonarr.key},
                         root folder /data/media/tv, default server
               Users -> your user -> Settings: turn on Auto-Request Movies/Series
               (and in Permissions: Auto-Request + Auto-Approve).
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
