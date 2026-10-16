#!/bin/sh
# Runs inside a throwaway container before the stack starts.
# Creates the folder layout and gives it to PUID:PGID. Safe to re-run.
set -eu

for d in plex qbittorrent prowlarr radarr sonarr seerr gluetun; do
  mkdir -p "/cfg/$d"
done
mkdir -p /data/media/movies /data/media/tv /data/torrents/radarr /data/torrents/sonarr /data/recycle

# App configs and download/recycle folders: owned by the stack user.
# Only touch files that are wrong, so a big Plex config isn't rewritten
# (and re-flagged for backup) on every start.
find /cfg /data/torrents /data/recycle \( ! -user "$PUID" -o ! -group "$PGID" \) \
  -exec chown "$PUID:$PGID" {} + \
  || echo "init: warning: couldn't change some owners (normal on Docker Desktop)"
# Library: fix the top folders only, so a big existing library isn't rewalked.
chown "$PUID:$PGID" /data /data/media /data/media/movies /data/media/tv \
  || echo "init: warning: couldn't change library folder owners"
chmod 775 /data/media /data/media/movies /data/media/tv /data/torrents /data/recycle \
  || echo "init: warning: couldn't change folder permissions"

echo "init: folders ready (owner $PUID:$PGID)"
