#!/bin/sh
# Runs inside a throwaway container before the stack starts.
# Creates the folder layout and gives it to PUID:PGID. Safe to re-run.
#
# What it touches in DATA_DIR: only folders, never files in your library.
#   - the top of DATA_DIR and each library folder (MOVIES_DIR, TV_DIR) and its
#     parents: owner set to PUID:PGID, owner and group given read/write/enter
#     (never anything for other users); nothing inside them
#   - torrents/ and recycle/: everything inside gets PUID:PGID (the apps' own)
#   - incoming/: the folder itself (shared, see README); nothing inside it
set -eu
set -f  # no wildcard expansion: folder names are used as-is

MOVIES_DIR="${MOVIES_DIR:-media/movies}"
TV_DIR="${TV_DIR:-media/tv}"

# Optional: refuse to start on an empty mountpoint. If the media drive isn't
# mounted (say, it was marked dirty after an unclean shutdown), DATA_DIR is just
# an empty folder, and every app would start against it. All services wait for
# this script, so failing here stops the whole stack.
if [ "${REQUIRE_DATA_MARKER:-false}" = true ] && [ ! -e /data/.cinemarr-data ]; then
  echo "init: refusing to run: /data/.cinemarr-data is missing." >&2
  echo "init: DATA_DIR's drive is probably not mounted. Mount it (or create the" >&2
  echo "init: marker on the real drive) and run 'docker compose up -d' again." >&2
  exit 1
fi

# Refuse folders that look like a system or home folder: everything under
# CONFIG_DIR is handed to PUID, so a typo there must not take over the system.
for root in /cfg /data; do
  for marker in etc bin usr .ssh; do
    if [ -e "$root/$marker" ]; then
      echo "init: refusing to run: $root contains '$marker'." >&2
      echo "init: CONFIG_DIR and DATA_DIR in .env must be folders used only by this stack." >&2
      exit 1
    fi
  done
done

# Library folders are relative to DATA_DIR, and never inside the folders that
# are walked below (so a library is never re-owned file by file).
for lib in "$MOVIES_DIR" "$TV_DIR"; do
  case "$lib" in
    "" | /* | *..* | torrents | torrents/* | recycle | recycle/* | incoming | incoming/*)
      echo "init: refusing to run: MOVIES_DIR/TV_DIR must be a folder inside DATA_DIR," >&2
      echo "init: like media/movies, not '$lib'." >&2
      exit 1 ;;
  esac
done

# Every folder this script changes, and each folder on the way to it, must be
# a real folder: a symlink could point the chown somewhere else.
no_symlinks() {  # no_symlinks RELATIVE_PATH
  path=/data
  old_ifs=$IFS; IFS=/
  for part in $1; do
    [ -n "$part" ] || continue
    path="$path/$part"
    if [ -L "$path" ]; then
      echo "init: refusing to run: $path is a symlink." >&2
      exit 1
    fi
  done
  IFS=$old_ifs
}
for rel in "$MOVIES_DIR" "$TV_DIR" torrents/radarr torrents/sonarr recycle incoming; do
  no_symlinks "$rel"
done

# Create the folders, then give the stack user the top of DATA_DIR and every
# folder on the way to each library folder (but nothing inside a library).
own_path() {  # own_path RELATIVE_PATH
  path=/data
  old_ifs=$IFS; IFS=/
  for part in $1; do
    [ -n "$part" ] || continue
    path="$path/$part"
    chown -h "$PUID:$PGID" "$path" && chmod ug+rwx "$path" \
      || echo "init: warning: couldn't set owner/permissions on $path"
  done
  IFS=$old_ifs
}

for d in plex qbittorrent prowlarr radarr sonarr seerr gluetun; do
  mkdir -p "/cfg/$d"
done
mkdir -p "/data/$MOVIES_DIR" "/data/$TV_DIR" /data/torrents/radarr /data/torrents/sonarr \
  /data/recycle /data/incoming

chown -h "$PUID:$PGID" /data && chmod ug+rwx /data \
  || echo "init: warning: couldn't set owner/permissions on DATA_DIR"
own_path "$MOVIES_DIR"
own_path "$TV_DIR"

# App configs and the download/recycle folders belong to the apps: owned by the
# stack user throughout. Only touch files that are wrong, so a big Plex config
# isn't rewritten (and re-flagged for backup) on every start.
# Symlinks are skipped and never followed (-h), and -xdev stays on the same
# drive, so a link planted by a compromised app can't redirect the chown.
find /cfg /data/torrents /data/recycle -xdev ! -type l \
  \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} + \
  || echo "init: warning: couldn't change some owners (normal on Docker Desktop)"
chmod ug+rwx /data/torrents /data/torrents/radarr /data/torrents/sonarr /data/recycle \
  || echo "init: warning: couldn't change folder permissions"

# incoming/: where you copy movies you already own (see README). Group-writable,
# and setgid so files copied in get the stack's group, readable by the apps.
chown -h "$PUID:$PGID" /data/incoming && chmod 2775 /data/incoming \
  || echo "init: warning: couldn't set up the incoming folder"

# The apps keep their own keys in here (API keys, Plex sign-in), so only the
# stack user may open it.
chmod 700 /cfg || echo "init: warning: couldn't restrict the config folder"

echo "init: folders ready (owner $PUID:$PGID)"
