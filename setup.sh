#!/usr/bin/env bash
# cinemarr setup: from a fresh clone to a running server, one question at a time.
#
#   ./setup.sh           set up, or re-check an existing setup (safe to re-run)
#   ./setup.sh update    download new versions and restart
#   -y, --yes            accept every default without asking
#
# Runs on macOS, Linux and Windows (inside WSL's Ubuntu; see README).
# Every change is explained and asked for first. It saves no passwords.
set -euo pipefail
# Anything this script writes (.env and its temporary copy) is private.
umask 077

YES=false
CMD=setup

# ---------------------------------------------------------------- output
say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*"; }
die()  { printf '\nsetup: %s\n' "$*" >&2; exit 1; }

# confirm "Question?"  -> success on yes (the default)
confirm() {
  local reply
  if $YES; then info "$1 yes"; return 0; fi
  read -r -p "    $1 [Y/n] " reply || die "no answer (run with -y to accept the defaults)"
  case $reply in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ask VAR "Question" default  -> VAR is the answer, or the default
ask() {
  local reply=""
  if $YES; then
    info "$2: $3"
  else
    read -r -p "    $2 [$3]: " reply || die "no answer (run with -y to accept the defaults)"
  fi
  printf -v "$1" '%s' "${reply:-$3}"
}

# ---------------------------------------------------------------- platform
OS=$(uname -s)
WSL=false
DESKTOP=false

open_url() {
  if [ "$OS" = Darwin ]; then
    open "$1"
  elif $WSL; then
    powershell.exe -NoProfile -NonInteractive -Command "Start-Process '$1'" >/dev/null 2>&1
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null; then
    xdg-open "$1" >/dev/null 2>&1 &
  else
    return 1
  fi
}

check_docker() {
  [ "$OS" = Linux ] && grep -qi microsoft /proc/version 2>/dev/null && WSL=true
  # Docker Desktop on macOS can leave docker off the PATH.
  if ! command -v docker >/dev/null && [ -x "$HOME/.docker/bin/docker" ]; then
    PATH="$HOME/.docker/bin:$PATH"
  fi
  if ! command -v docker >/dev/null; then
    if $WSL; then
      die "Docker isn't available in Ubuntu. In Docker Desktop: Settings > Resources >
       WSL Integration, turn on Ubuntu, Apply, then reopen Ubuntu."
    fi
    die "Docker isn't installed. Get it from https://docs.docker.com/get-docker/"
  fi
  if ! docker info >/dev/null 2>&1; then
    [ "$OS" = Linux ] && ! $WSL && [ -S /var/run/docker.sock ] && [ ! -w /var/run/docker.sock ] &&
      die "Your user can't use Docker. Run: sudo usermod -aG docker \$USER, then log out and back in."
    die "Docker isn't running. Start Docker Desktop (or the docker service) and try again."
  fi
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is needed (the 'docker compose' command)."
  # Docker Desktop (macOS, Windows, and Linux too) hides device addresses, so
  # it needs the relay setup; plain Docker Engine on Linux uses host networking.
  docker info --format '{{.OperatingSystem}}' | grep -q 'Docker Desktop' && DESKTOP=true
  return 0
}

detect_tz() {
  local tz
  tz=$(readlink /etc/localtime 2>/dev/null | sed -n 's#.*zoneinfo/##p')
  [ -z "$tz" ] && [ -r /etc/timezone ] && tz=$(head -n1 /etc/timezone)
  echo "${tz:-Etc/UTC}"
}

# shellcheck disable=SC2086  # splits the address on dots
ip2int() { local IFS=.; set -- $1; echo $(( ($1 << 24) + ($2 << 16) + ($3 << 8) + $4 )); }
int2ip() { echo "$(( ($1 >> 24) & 255 )).$(( ($1 >> 16) & 255 )).$(( ($1 >> 8) & 255 )).$(( $1 & 255 ))"; }
mask2prefix() {
  local n bits=0
  n=$(ip2int "$1")
  while [ "$n" -gt 0 ]; do bits=$(( bits + (n & 1) )); n=$(( n >> 1 )); done
  echo $bits
}
# network "192.168.1.50" 24 -> 192.168.1.0/24
network() {
  local mask=$(( (0xFFFFFFFF << (32 - $2)) & 0xFFFFFFFF ))
  echo "$(int2ip $(( $(ip2int "$1") & mask )))/$2"
}

# Prints "address prefix" of this computer on the home network, or nothing.
detect_lan() {
  local iface ip prefix
  if [ "$OS" = Darwin ]; then
    iface=$(route -n get default 2>/dev/null | awk '/interface:/ {print $2}')
    [ -n "$iface" ] || return 0
    ip=$(ipconfig getifaddr "$iface" 2>/dev/null) || return 0
    prefix=$(mask2prefix "$(ipconfig getoption "$iface" subnet_mask)")
  elif $WSL; then
    # Inside WSL, Linux sees a private 172.x address; ask Windows instead.
    # shellcheck disable=SC2016  # PowerShell variables, not shell ones
    read -r ip prefix <<EOF || true
$(powershell.exe -NoProfile -NonInteractive -Command \
  '$c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } | Select-Object -First 1; $a = $c.IPv4Address | Select-Object -First 1; "$($a.IPAddress) $($a.PrefixLength)"' \
  2>/dev/null | tr -d '\r')
EOF
  else
    read -r ip iface <<EOF || true
$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) {if ($i == "src") s = $(i+1); if ($i == "dev") d = $(i+1)}} END {print s, d}')
EOF
    [ -n "$iface" ] && prefix=$(ip -o -4 addr show dev "$iface" | awk -v ip="$ip" '{split($4, a, "/"); if (a[1] == ip) print a[2]}')
  fi
  [ -n "${ip:-}" ] && [ -n "${prefix:-}" ] && echo "$ip $prefix"
  return 0
}

# ---------------------------------------------------------------- .env
env_get() {
  [ -f .env ] || return 0
  awk -v k="$1" 'index($0, k "=") == 1 {v = substr($0, length(k) + 2)} END {print v}' .env
}

env_set() {
  # ENVIRON keeps the value literal (awk -v would interpret backslashes).
  K="$1" V="$2" awk 'BEGIN {k = ENVIRON["K"]; v = ENVIRON["V"]}
    index($0, k "=") == 1 {print k "=" v; done = 1; next} {print}
    END {if (!done) print k "=" v}' .env > .env.tmp
  cat .env.tmp > .env
  rm -f .env.tmp
}

KEYS=()
VALS=()
# want KEY value: queue a change for .env (only if it differs)
want() {
  [ "$(env_get "$1")" = "$2" ] && return 0
  KEYS+=("$1")
  VALS+=("$2")
}

# propose KEY "Description" detected: keep a matching value, ask otherwise
propose() {
  local cur val
  cur=$(env_get "$1")
  if [ -n "$cur" ] && [ "$cur" = "$3" ]; then info "$2: $cur"; return 0; fi
  [ -n "$cur" ] && info "$2: .env has $cur, but this computer looks like $3"
  ask val "$2" "${3:-$cur}"
  want "$1" "$val"
}

# choose KEY "Description" default: ask only when .env doesn't have it yet
choose() {
  local cur val
  cur=$(env_get "$1")
  if [ -n "$cur" ] && $HAD_ENV; then info "$2: $cur"; return 0; fi
  ask val "$2" "$3"
  want "$1" "$val"
}

configure_env() {
  say "Settings (.env)"
  HAD_ENV=false
  [ -f .env ] && HAD_ENV=true
  $HAD_ENV && info "Found .env; checking it against this computer."

  local files gpu_gid="" lan ip="" prefix="" dir
  if $DESKTOP; then
    files=compose.yaml
    info "Docker Desktop: devices reach Plex through the plex-lan relay."
  else
    files=compose.yaml:compose.linux.yaml
    info "Docker Engine on Linux: Plex runs on the host network."
    if [ -e /dev/dri/renderD128 ] && confirm "Found a GPU. Use it for Plex hardware transcoding (needs Plex Pass)?"; then
      files=$files:compose.gpu.yaml
      gpu_gid=$(stat -c %g /dev/dri/renderD128)
    fi
  fi
  # Keep the optional VPN if it was turned on by hand.
  case $(env_get COMPOSE_FILE) in *compose.vpn.yaml*) files=$files:compose.vpn.yaml ;; esac
  want COMPOSE_FILE "$files"
  want COMPOSE_PATH_SEPARATOR ":"
  [ -n "$gpu_gid" ] && want RENDER_GID "$gpu_gid"

  propose TZ "Time zone" "$(detect_tz)"
  if [ "$OS" = Darwin ]; then
    want PUID 1000; want PGID 1000
  else
    propose PUID "User ID that owns the files" "$(id -u)"
    propose PGID "Group ID that owns the files" "$(id -g)"
  fi

  lan=$(detect_lan)
  if [ -n "$lan" ]; then
    read -r ip prefix <<EOF
$lan
EOF
  else
    warn "Couldn't find this computer's home-network address."
  fi
  propose SERVER_IP "This computer's address on your home network" "$ip"
  [ -n "$prefix" ] && propose LAN_SUBNET "Your home network" "$(network "$ip" "$prefix")"
  if $DESKTOP; then
    info "Give this computer a fixed address in your router, so it doesn't change."
  fi

  choose CONFIG_DIR "Folder for app settings" ./config
  choose DATA_DIR "Folder for media and downloads (keep both on one drive)" ./data
  if $WSL; then
    for dir in "$(env_get CONFIG_DIR)" "$(env_get DATA_DIR)" "$PWD"; do
      case $dir in /mnt/*)
        warn "$dir is on a Windows drive: slower, no instant imports, and Plex only"
        warn "notices new files every 15 minutes. Folders inside Ubuntu (~) are better." ;;
      esac
    done
  fi

  if ! $HAD_ENV || [ -z "$(env_get BIND_IP)" ]; then
    info "Who can open the web pages (Radarr, Sonarr, Prowlarr, qBittorrent, Seerr)?"
    info "  1) only this computer (safest)    2) any device on your home network"
    local who
    ask who "Choose 1 or 2" 1
    if [ "$who" = 2 ] && [ -n "$ip" ]; then want BIND_IP "$ip"; else want BIND_IP 127.0.0.1; fi
  else
    info "Web pages open from: $(env_get BIND_IP)"
  fi

  if [ ${#KEYS[@]} -eq 0 ]; then
    info ".env is up to date."
  else
    info "Changes to .env:"
    local i
    for i in "${!KEYS[@]}"; do info "  ${KEYS[$i]}=${VALS[$i]}"; done
    confirm "Write these to .env?" || die "Nothing changed."
    $HAD_ENV || cp .env.example .env
    for i in "${!KEYS[@]}"; do env_set "${KEYS[$i]}" "${VALS[$i]}"; done
  fi
  chmod 600 .env
  info ".env is readable only by you."
  if $DESKTOP && [ -z "$(env_get SERVER_IP)" ]; then
    warn "Without SERVER_IP, only this computer can reach Plex. Set it and re-run."
  fi
}

# ---------------------------------------------------------------- firewall
configure_ufw() {
  $DESKTOP && return 0
  command -v ufw >/dev/null || return 0
  local lan
  lan=$(env_get LAN_SUBNET)
  [ -n "$lan" ] || return 0
  say "Firewall (ufw)"
  info "These let devices at home (and the other containers) reach Plex:"
  info "  sudo ufw allow from $lan to any port 32400 proto tcp"
  info "  sudo ufw allow from $lan to any port 32410:32414 proto udp"
  info "  sudo ufw allow from 172.16.0.0/12 to any port 32400 proto tcp"
  confirm "Add these rules (needs sudo)?" || return 0
  sudo ufw allow from "$lan" to any port 32400 proto tcp
  sudo ufw allow from "$lan" to any port 32410:32414 proto udp
  sudo ufw allow from 172.16.0.0/12 to any port 32400 proto tcp
  info "Rules added. Turning ufw on is up to you (see README, host hardening)."
}

# ---------------------------------------------------------------- start
start_stack() {
  say "Start the server"
  info "Downloads the apps (first time: a few GB) and starts them."
  confirm "Run 'docker compose up -d' now?" || die "Stopped. Run ./setup.sh again when ready."
  # </dev/null: Docker commands must not eat answers typed for later questions.
  if ! docker compose up -d </dev/null; then
    docker compose logs init --no-log-prefix 2>/dev/null | tail -n 5
    die "The server didn't start (see above)."
  fi
}

plex_claimed() {
  docker compose exec -T plex curl -fs http://localhost:32400/identity </dev/null 2>/dev/null |
    grep -o 'claimed="[01]"' | grep -q '"1"'
}

plex_signin() {
  say "Plex sign-in"
  local i url
  for i in $(seq 1 60); do
    docker compose exec -T plex curl -fs -o /dev/null http://localhost:32400/identity </dev/null 2>/dev/null && break
    [ "$i" = 60 ] && die "Plex didn't start. Check: docker compose logs plex"
    sleep 2
  done
  if plex_claimed; then
    info "Plex is already signed in."
  else
    if $DESKTOP; then url="http://$(env_get SERVER_IP):32400/web"; else url="http://localhost:32400/web"; fi
    [ "$url" = "http://:32400/web" ] && url="http://localhost:32400/web"
    info "Sign in now: until you do, any device on your network could claim this"
    info "server. Use your Plex account (a free one is fine) and give the server a"
    info "name. On Plex's \"Media Library\" page, don't add anything: just click"
    info "Next. This script adds Movies and TV Shows as soon as you've signed in;"
    info "that page won't list them, but they're on Plex's home screen afterwards."
    if confirm "Open $url in your browser?" && open_url "$url"; then
      :
    else
      info "Open $url in a browser on this computer."
      if [ "$OS" = Linux ] && ! $WSL; then
        info "No screen here? From another computer: ssh -L 32400:localhost:32400 $USER@<this-server>"
        info "then open http://localhost:32400/web there."
      fi
    fi
    printf '    Waiting for you to sign in (Ctrl-C to stop; re-run later to continue)'
    until plex_claimed; do printf '.'; sleep 3; done
    printf '\n'
    info "Signed in."
  fi
  # Point Plex at the right folders before you'd have to pick them yourself.
  docker compose run --rm -T bootstrap python -u /app/bootstrap.py --plex-libraries </dev/null 2>/dev/null |
    grep -E '✓|!' || warn "Couldn't add the Plex libraries now; the next step tries again."
  if [ -n "$(env_get PLEX_CLAIM)" ]; then
    info "PLEX_CLAIM in .env has done its job (it works once)."
    confirm "Empty it?" && env_set PLEX_CLAIM ""
  fi
  return 0
}

run_bootstrap() {
  say "Configure the apps"
  info "Sets up qBittorrent, Radarr, Sonarr, Prowlarr, Plex (libraries, scans,"
  info "privacy settings) and Seerr (your Plex account as admin, Radarr and Sonarr,"
  info "requests from your Plex Watchlist). It asks for two passwords and saves"
  info "neither: keep them in your password manager."
  confirm "Run it now?" || { info "Later: docker compose run --rm bootstrap"; return 0; }
  docker compose run --rm -e CINEMARR_SETUP=1 bootstrap
}

finish() {
  local bind
  bind=$(env_get BIND_IP)
  say "One thing only you can do"
  info "Prowlarr (http://$bind:9696): Indexers > add your sources; they sync to"
  info "Radarr and Sonarr. In each indexer's advanced settings, set Apps Minimum"
  info "Seeders to about 10."
  if confirm "Open Prowlarr in your browser?"; then
    open_url "http://$bind:9696" || info "Open http://$bind:9696"
  fi
  info "Your requests show up in Seerr: http://$bind:5055"
  info "Then test it: add a public-domain film to your Plex Watchlist (README: 'Test it')."
}

update() {
  say "Update"
  confirm "Download new versions and restart the apps?" || exit 0
  docker compose pull </dev/null
  docker compose up -d </dev/null
  # Compose doesn't notice changes to the relay's inline settings.
  $DESKTOP && docker compose up -d --force-recreate plex-lan </dev/null
  confirm "Delete the old versions' images to free space?" && docker image prune -f </dev/null
  return 0
}

usage() { sed -n '2,9s/^# \{0,1\}//p' "$0"; exit 0; }

main() {
  local a
  for a in "$@"; do
    case $a in
      -y|--yes) YES=true ;;
      update) CMD=update ;;
      -h|--help) usage ;;
      *) die "unknown option: $a (see ./setup.sh --help)" ;;
    esac
  done
  cd "$(dirname "$0")"
  if [ ! -f compose.yaml ] || [ ! -f .env.example ]; then die "run this from the cinemarr folder."; fi
  check_docker
  if $WSL; then
    case $PWD in /mnt/*) warn "This folder is on a Windows drive ($PWD). Clone it inside Ubuntu (cd ~) instead.";; esac
  fi
  if [ "$CMD" = update ]; then update; exit 0; fi
  configure_env
  configure_ufw
  start_stack
  plex_signin
  run_bootstrap
  finish
}

# Run unless sourced (the functions can be loaded for testing).
if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
