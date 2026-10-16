#!/usr/bin/env python3
"""Configure the stack through each app's API. Safe to re-run.

    docker compose run --rm bootstrap

Runs inside a container (python:3.12-alpine), so it works the same on
Linux, macOS and Windows. Standard library only.

Sets up:
  qBittorrent  its own password, save path, never-seed limits, UPnP off, blocked file types
  Radarr       login, root folder, qBittorrent client (remove on import),
               renaming, recycle bin; no Plex token (any old Plex connection
               is removed)
  Sonarr       same as Radarr, for TV
  Prowlarr     login, sync to Radarr + Sonarr
  Seerr        your Plex account as its admin, Plex server and libraries,
               Radarr + Sonarr, auto-request from your Plex Watchlist
  Plex         Movies and TV Shows libraries,
               scans for new files itself (on change + every 15 minutes),
               secure connections preferred, media deletion off, DLNA off,
               home address (SERVER_IP), LAN subnet (Linux), remote access
               off (unless PLEX_REMOTE_ACCESS=true, Linux only)
Left for you: indexers in Prowlarr.

Passwords are typed in each run and never saved: keep them in your password
manager.
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
SEERR = "http://seerr:5055/api/v1"
SEERR_SETTINGS = CFG / "seerr/settings.json"

BLOCKED_FILES = "\n".join(
    # Windows
    ["*.exe", "*.scr", "*.bat", "*.cmd", "*.com", "*.lnk", "*.vbs", "*.vbe", "*.js",
     "*.jse", "*.wsf", "*.hta", "*.pif", "*.cpl", "*.reg", "*.msi", "*.msp", "*.ps1",
     # macOS / Linux / Android
     "*.app", "*.dmg", "*.pkg", "*.command", "*.sh", "*.desktop", "*.run", "*.apk",
     # Archives and disc images (Radarr/Sonarr can't import them anyway)
     "*.jar", "*.iso", "*.zip", "*.zipx", "*.rar", "*.7z"]
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

    def remove_plex_connections(self):
        """Older versions of this script gave Radarr/Sonarr the Plex token."""
        for n in self.get("/notification"):
            if n.get("implementation") == "PlexServer":
                request("DELETE", f"{self.base}/notification/{n['id']}", headers=self.headers)
                ok(f"{self.name}: Plex connection '{n.get('name')}' removed (no Plex token kept)")


# ---------------------------------------------------------------- qBittorrent
def configure_qbittorrent(user, password, arr_password):
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

    # Earlier versions of this script gave qBittorrent the Radarr/Sonarr password.
    if not login(user, password) and not login(user, arr_password):
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
    ok("qBittorrent: own password, save path, never-seed, UPnP off, blocked file types")

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


LIBRARIES = (
    # name, type, agent, scanner, folder (Plex's current defaults)
    ("Movies", "movie", "tv.plex.agents.movie", "Plex Movie", "/data/media/movies"),
    ("TV Shows", "show", "tv.plex.agents.series", "Plex TV Series", "/data/media/tv"),
)


def ensure_plex_libraries(base, token):
    headers = {"X-Plex-Token": token}
    sections = request("GET", f"{base}/library/sections", headers=headers)
    existing = sections["MediaContainer"].get("Directory", [])
    for name, kind, agent, scanner, folder in LIBRARIES:
        # One library per kind: if you made one yourself, keep it.
        same = [d for d in existing if d.get("type") == kind]
        if same:
            paths = [loc["path"].rstrip("/") for d in same for loc in d.get("Location", [])]
            if folder in paths:
                ok(f"Plex: library for {folder} already set")
            else:
                warn(f"Plex: your '{same[0].get('title')}' library uses {', '.join(paths)}, "
                     f"but downloads go to {folder}. In Plex: Manage Library > Edit > "
                     f"Add folders > {folder}, and remove the other folder.")
            continue
        query = urllib.parse.urlencode({
            "name": name, "type": kind, "agent": agent, "scanner": scanner,
            "language": "en-US", "location": folder})
        request("POST", f"{base}/library/sections?{query}", headers=headers)
        ok(f"Plex: library '{name}' added ({folder})")


def configure_plex(token):
    base = f"http://{PLEX_HOST}:32400"
    if not wait_for("Plex", f"{base}/identity", timeout=120):
        return
    step("Plex libraries", ensure_plex_libraries, base, token)
    remote = PLEX_REMOTE
    if remote and PLEX_VIA_RELAY:
        # Behind the relay every connection looks local, including ones from
        # the internet, so remote access stays off.
        warn("PLEX_REMOTE_ACCESS is ignored on macOS/Windows; remote access stays off")
        remote = False
    prefs = {
        # Plex finds new imports itself, so Radarr/Sonarr never need the
        # Plex token: on file changes, plus a full scan every 15 minutes.
        "FSEventLibraryUpdatesEnabled": 1,
        "FSEventLibraryPartialScanEnabled": 1,
        "ScheduledLibraryUpdatesEnabled": 1,
        "ScheduledLibraryUpdateInterval": 900,
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
        # Token in a header, not the URL, so it never lands in access logs.
        step(f"Plex {key}", request, "PUT",
             f"{base}/:/prefs?{urllib.parse.urlencode({key: value})}",
             headers={"X-Plex-Token": token})
    ok(f"Plex: scans on change + every 15 min, secure connections preferred, "
       f"deletion off, DLNA off, "
       f"remote access {'on' if remote else 'off'}"
       + (f", address http://{SERVER_IP}:32400" if SERVER_IP else ""))
    if PLEX_VIA_RELAY and not SERVER_IP:
        warn("SERVER_IP isn't set in .env, so TVs and phones may not find Plex "
             "on your network. Set it and re-run.")


# ---------------------------------------------------------------- Seerr
def pick_profile(profiles, preferred="HD-1080p"):
    """Quality profile for requests: 1080p if the app has it, else the first."""
    return next((p for p in profiles if p["name"] == preferred), profiles[0])


def configure_seerr(token, radarr, sonarr):
    if not wait_for("Seerr", f"{SEERR}/status"):
        return
    public = request("GET", f"{SEERR}/settings/public")
    if not public.get("initialized"):
        # Seerr makes the first Plex account that signs in its admin. This is
        # what its setup page does after you sign in with Plex in the browser.
        request("POST", f"{SEERR}/auth/plex", data={"authToken": token})
        ok("Seerr: your Plex account is the admin")
    key = json.loads(SEERR_SETTINGS.read_text())["main"]["apiKey"]
    headers = {"X-Api-Key": key}

    def api(method, path, data=None):
        return request(method, f"{SEERR}{path}", headers=headers, data=data)

    api("POST", "/settings/plex", {"ip": PLEX_HOST, "port": 32400, "useSsl": False})
    for lib in api("POST", "/settings/plex/library/sync"):
        if not lib.get("enabled"):
            api("PUT", f"/settings/plex/library/{lib['id']}", {"enabled": True})
    ok(f"Seerr: Plex server {PLEX_HOST}:32400, all libraries on")

    for app, kind, port, root in ((radarr, "radarr", 7878, "/data/media/movies"),
                                  (sonarr, "sonarr", 8989, "/data/media/tv")):
        conn = {"hostname": kind, "port": port, "apiKey": app.key,
                "useSsl": False, "baseUrl": ""}
        test = api("POST", f"/settings/{kind}/test", conn)
        profile = pick_profile(test["profiles"])
        item = {**conn, "name": app.name, "activeProfileId": profile["id"],
                "activeProfileName": profile["name"], "activeDirectory": root,
                "tags": [], "is4k": False, "isDefault": True, "externalUrl": "",
                "syncEnabled": False, "preventSearch": False, "tagRequests": False,
                "overrideRule": []}
        if kind == "radarr":
            item["minimumAvailability"] = "released"
        else:
            item.update(seriesType="standard", animeSeriesType="anime",
                        activeAnimeProfileId=profile["id"],
                        activeAnimeProfileName=profile["name"],
                        activeAnimeDirectory=root, animeTags=[],
                        enableSeasonFolders=True, monitorNewItems="all")
        existing = next((x for x in api("GET", f"/settings/{kind}")
                         if x.get("hostname") == kind), None)
        if existing:
            merged = {k: v for k, v in {**existing, **item}.items() if k != "id"}
            api("PUT", f"/settings/{kind}/{existing['id']}", merged)
        else:
            api("POST", f"/settings/{kind}", item)
        ok(f"Seerr: {app.name} at {kind}:{port}, {root}, profile {profile['name']}")

    # Admins pass every permission check (auto-approve included); this turns
    # on requesting whatever you add to your Plex Watchlist.
    user = api("GET", "/user/1/settings/main")
    user.update(watchlistSyncMovies=True, watchlistSyncTv=True)
    api("POST", "/user/1/settings/main", user)
    ok("Seerr: Watchlist adds become requests, approved automatically")

    if not public.get("initialized"):
        api("POST", "/settings/initialize")
        ok("Seerr: setup finished")


def plex_libraries_only():
    """setup.sh runs this the moment you sign in, before Plex's own setup
    reaches its library step, so there's nothing to pick by hand."""
    token = plex_token()
    if not token:
        sys.exit("Plex isn't signed in yet.")
    base = f"http://{PLEX_HOST}:32400"
    if wait_for("Plex", f"{base}/identity", timeout=120):
        step("Plex libraries", ensure_plex_libraries, base, token)


# ---------------------------------------------------------------- main
def ask_password(prompt, differs_from=None):
    while True:
        password = getpass.getpass(f"{prompt} (8+ characters): ")
        if len(password) < 8 or password != getpass.getpass("Confirm: "):
            print("Passwords didn't match or were under 8 characters. Try again.")
        elif password == differs_from:
            print("Use a different password from the one above. Try again.")
        else:
            return password


def main():
    print("This script saves no passwords: keep them in your password manager.")
    while True:
        user = input("Username [admin]: ").strip() or "admin"
        # qBittorrent rejects usernames under 3 characters or with a colon.
        if len(user) >= 3 and ":" not in user:
            break
        print("Use at least 3 characters and no colon. Try again.")
    password = ask_password("Password for Radarr, Sonarr and Prowlarr")
    # Radarr and Sonarr send qBittorrent its password on every login, so a
    # compromised torrent client must not learn the one that opens them.
    print("qBittorrent needs a different password (Radarr and Sonarr hand it to")
    print("qBittorrent on every login, so it must not also open them).")
    qbit_pw = ask_password("Password for qBittorrent", differs_from=password)

    log("Reading API keys and waiting for services")
    radarr = Arr("Radarr", 7878, "v3")
    sonarr = Arr("Sonarr", 8989, "v3")
    prowlarr = Arr("Prowlarr", 9696, "v1")
    wait_for("qBittorrent", "http://qbittorrent:8080/api/v2/app/version")
    for app in (radarr, sonarr, prowlarr):
        app.wait()

    token = plex_token()

    log("qBittorrent")
    step("qBittorrent setup", configure_qbittorrent, user, qbit_pw, password)

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
             {"host": "qbittorrent", "port": 8080, "username": user, "password": qbit_pw,
              cat_field: category},
             enable=True, protocol="torrent", priority=1,
             removeCompletedDownloads=True, removeFailedDownloads=True)
        step("Plex token removal", app.remove_plex_connections)

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
        log("Seerr")
        step("Seerr setup", configure_seerr, token, radarr, sonarr)
    else:
        warn(f"Plex isn't signed in yet. Open http://{SERVER_IP or 'localhost'}:32400/web, "
             "sign in, then re-run this script to finish Plex (including automatic "
             "library scans) and Seerr.")

    if os.environ.get("CINEMARR_SETUP"):
        print("\nDone.")  # setup.sh shows the remaining steps itself
        return
    print("""
Done. Left for you:

  Prowlarr  http://<BIND_IP>:9696 -> Indexers -> add your sources.
            They sync to Radarr and Sonarr automatically. In each indexer's
            advanced settings, set Apps Minimum Seeders to about 10.
""")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--plex-libraries"]:
            plex_libraries_only()
        else:
            main()
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
