# Optional: saving power on a Linux host

**Host-level, not part of Cinemarr.** Nothing here is in the compose files or
run by `setup.sh`. These are changes to the Linux machine itself that a home
server running 24/7 benefits from. Apply only what fits your hardware, and
replace the example names (`sdb`, `enp3s0`, the drive model) with yours.

Two things work against each other: a server that's asleep can't serve Plex or
pick up your Watchlist. So this guide doesn't suspend the machine. It lets the
media drive spin down, keeps the CPU and other parts in their deepest idle
states, and runs without a desktop unless you want one. The server then uses
little power while nothing is happening, and responds immediately when
something is.

Cinemarr's own settings already avoid waking the drive: Plex scans only when
files change (no scheduled scans), and Radarr/Sonarr only rescan folders after a
manual refresh.

---

## 1. Spin down the media drive

`hdparm -S` sets how long the drive waits, idle, before spinning down. Values
241–251 count in 30-minute steps, so **241 = 30 minutes**
([hdparm(8)](https://linux.die.net/man/8/hdparm)).

Setting it from a udev rule re-applies it whenever the drive appears, including
after a reboot or reconnect. Match on the drive's model so the rule follows the
drive, not a `/dev/sdX` name that can change:

```bash
cat /sys/block/sdb/device/model          # e.g. "WDC WD40EFRX-68N"
```

`/etc/udev/rules.d/69-hdparm-spindown.rules`:

```
ACTION=="add|change", KERNEL=="sd[a-z]", ATTRS{model}=="WDC WD40EFRX*", RUN+="/usr/sbin/hdparm -S 241 /dev/%k"
```

```bash
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=block
```

Some USB enclosures don't pass `hdparm` commands through to the drive, and some
drives have their own idle timers; if the drive never reports `standby` (see
[Checking it works](#6-checking-it-works)), that's usually why.

### Stop smartd from waking it

`smartd` polls drives for health data, which spins them back up. Tell it to skip
drives that are in standby, quietly (`q` suppresses the "skipped" log message;
[smartd.conf(5)](https://manpages.ubuntu.com/manpages/noble/en/man5/smartd.conf.5.html)).
In `/etc/smartd.conf`, change the `DEVICESCAN` line to include `-n standby,q`,
for example:

```
DEVICESCAN -a -n standby,q -m root -M exec /usr/share/smartmontools/smartd-runner
```

```bash
sudo systemctl restart smartmontools      # the service may be named smartd
```

---

## 2. powertop auto-tune, keeping Wake-on-LAN

`powertop --auto-tune` sets every tunable it knows to its power-saving value
([powertop(8)](https://manpages.ubuntu.com/manpages/noble/en/man8/powertop.8.html)).
One of those turns **Wake-on-LAN off**, so the unit below switches it back on
afterwards (`wol g` = wake on a magic packet;
[ethtool(8)](https://manpages.ubuntu.com/manpages/noble/en/man8/ethtool.8.html)).
Find your network interface with `ip -br link`.

`/etc/systemd/system/powertop.service`:

```ini
[Unit]
Description=powertop auto-tune
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/powertop --auto-tune
ExecStartPost=/usr/sbin/ethtool -s enp3s0 wol g

[Install]
WantedBy=multi-user.target
```

```bash
sudo apt install powertop ethtool
sudo systemctl daemon-reload && sudo systemctl enable --now powertop.service
```

Auto-tune also lets USB devices suspend themselves. If a USB keyboard lags, or
a USB-attached drive misbehaves, find that device in `powertop`'s **Tunables**
tab and set it back to "Bad", or drop auto-tune and set only the tunables you
want.

---

## 3. Audio codec power save

On a machine that's usually headless, the onboard audio can power down when
idle.

`/etc/modprobe.d/audio-powersave.conf`:

```
options snd_hda_intel power_save=1
```

`/etc/udev/rules.d/70-hdaudio-pm.rules`, letting the audio device use runtime
power management:

```
ACTION=="add", SUBSYSTEM=="hdaudio", ATTR{power/control}="auto"
```

Both take effect after a reboot.

---

## 4. Headless by default, desktop when you want it

Boot to the text console (no display server or desktop running in the
background):

```bash
sudo systemctl set-default multi-user.target
```

Two small helper scripts switch modes on demand, for example when you want to
use the machine as a TV. Run them as your desktop user (they use `sudo` for the
system part).

`/usr/local/bin/tv-mode`:

```bash
#!/bin/sh
# Start the desktop and audio.
set -e
sudo systemctl isolate graphical.target
systemctl --user start pipewire.socket pipewire-pulse.socket wireplumber.service
```

`/usr/local/bin/server-mode`:

```bash
#!/bin/sh
# Stop audio and the desktop; Plex and the rest keep running.
systemctl --user stop wireplumber.service pipewire-pulse.socket pipewire.socket \
  pipewire-pulse.service pipewire.service || true
sudo systemctl isolate multi-user.target
```

```bash
sudo chmod +x /usr/local/bin/tv-mode /usr/local/bin/server-mode
```

`isolate` only switches between targets; Docker and its containers are part of
both, so Cinemarr keeps running either way.

---

## 5. Why not sleep?

Suspend (S3) or hibernate saves the most, but a sleeping server can't stream,
can't download what you add to your Watchlist, and Plex apps don't send the
Wake-on-LAN packet that would wake it. The practical version of "sleep until
needed" is deep CPU idle: with the steps above, an idle server spends nearly all
its time in the CPU's deepest C-states, with the drive spun down, and wakes in
microseconds when a request arrives.

---

## 6. Checking it works

**Is the drive asleep?** `standby` means spun down; `active/idle` means
spinning ([hdparm(8)](https://linux.die.net/man/8/hdparm)). Checking doesn't
wake it.

```bash
sudo hdparm -C /dev/sdb
```

**What keeps waking it?** `fatrace` lists every file access on the current
mount ([fatrace(8)](https://manpages.ubuntu.com/manpages/noble/en/man8/fatrace.8.html)).
Run it from inside the media drive's mount and watch which process touches it:

```bash
sudo apt install fatrace
cd /mnt/media && sudo fatrace -c
```

**Is the CPU idling deeply?** Open `sudo powertop` and look at the **Idle
stats** tab: on an idle server most time should be in the deepest C-state
listed.

**Is a discrete GPU powered down?** Find it with `lspci | grep -iE 'vga|3d'`,
then (with your address):

```bash
cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status   # "suspended" = off
```

If it stays `active` while nothing uses it, its driver may not support runtime
power management, or something (a desktop session, Plex hardware transcoding)
is holding it.
