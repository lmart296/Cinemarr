#!/bin/sh
# Runs inside a throwaway container before the stack starts.
# Creates the folder layout and gives it to PUID:PGID. Safe to re-run.
set -eu

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

# The folders below are chowned and chmodded by name; none may be a symlink.
for p in /data/media /data/media/movies /data/media/tv /data/torrents /data/recycle; do
  if [ -L "$p" ]; then
    echo "init: refusing to run: $p is a symlink." >&2
    exit 1
  fi
done

for d in plex qbittorrent prowlarr radarr sonarr seerr gluetun; do
  mkdir -p "/cfg/$d"
done
mkdir -p /data/media/movies /data/media/tv /data/torrents/radarr /data/torrents/sonarr /data/recycle

# App configs and download/recycle folders: owned by the stack user.
# Only touch files that are wrong, so a big Plex config isn't rewritten
# (and re-flagged for backup) on every start.
# Symlinks are skipped and never followed (-h), and -xdev stays on the same
# drive, so a link planted by a compromised app can't redirect the chown.
find /cfg /data/torrents /data/recycle -xdev ! -type l \
  \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} + \
  || echo "init: warning: couldn't change some owners (normal on Docker Desktop)"
# Library: fix the top folders only, so a big existing library isn't rewalked.
chown -h "$PUID:$PGID" /data /data/media /data/media/movies /data/media/tv \
  || echo "init: warning: couldn't change library folder owners"
chmod 775 /data/media /data/media/movies /data/media/tv /data/torrents /data/recycle \
  || echo "init: warning: couldn't change folder permissions"
# The apps keep their own keys in here (API keys, Plex sign-in), so only the
# stack user may open it.
chmod 700 /cfg || echo "init: warning: couldn't restrict the config folder"

echo "init: folders ready (owner $PUID:$PGID)"
