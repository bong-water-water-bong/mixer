<div align="center">

# mixer

### no nas. no single point of failure. every machine backs up another.

**Shadow's distributed mesh snapshot system for [halo-ai](https://github.com/bong-water-water-bong/halo-ai)**

*btrfs snapshots over ssh in a ring — the Kansas City Shuffle*

*stamped by the architect*

</div>

---

## what is mixer?

One app that SSH's into every machine on the network, takes snapshots, and distributes them to every other machine. Full ring bus — every machine holds a snapshot of every other machine. While you're looking at one machine, Shadow already moved the data to all the others. No NAS. No single point of failure.

```
                 ryzen
               ╱   |   ╲
             ╱     |     ╲
      sligar ──────+────── strix-halo
             ╲     |     ╱
               ╲   |   ╱
              minisforum
```

Full ring bus. Every arrow goes both ways. Every machine holds a snapshot of every other machine. 4 machines = 3 snapshots each. Add a 5th machine, everyone gets a 4th snapshot. Scales automatically.

**Add a new PC — one command, it joins the mesh instantly:**

```bash
mixer add my-new-pc 192.168.50.100
```

Done. Shadow sends it everyone else's snapshots and takes one of its own.

## install

```bash
git clone https://github.com/bong-water-water-bong/mixer.git
cd mixer
bash install.sh
```

```bash
mixer status        # see the mesh
mixer daemon        # set and forget — Shadow handles the rest
```

## how it works

1. Shadow SSH's into every machine in the mesh
2. Takes a btrfs read-only snapshot (or rsync for non-btrfs like Windows)
3. Sends that snapshot to EVERY other machine — full ring bus
4. Every machine ends up with a snapshot of every other machine
5. Add a new PC — one command, instant mesh member

## the watchdog

Shadow doesn't run on a timer. He watches the network. When traffic drops and the network is quiet, he moves. Snapshots distribute during downtime so they never interfere with your work. Set it and forget it.

- Monitors network load in real-time
- Waits for traffic to drop below threshold
- Minimum 4 hours between runs — won't spam
- If the network never goes quiet, he'll proceed after an hour of waiting
- Runs as a systemd service — survives reboots, starts on boot

## commands

| Command | What it does |
|---------|-------------|
| `mixer status` | Show all machines, snapshot counts, disk space |
| `mixer run` | Full cycle now: snapshot all + distribute to all |
| `mixer daemon` | Watchdog mode — works when network is quiet |
| `mixer add <name> <host>` | Add a new machine to the mesh instantly |
| `mixer remove <name>` | Remove a machine from the mesh |
| `mixer nodes` | List all machines in the mesh |
| `mixer snapshot [machine]` | Take a snapshot on one machine |
| `mixer distribute` | Send all snapshots to all machines now |
| `mixer restore <from>` | Pull a snapshot from another machine |
| `mixer recover <machine>` | Full disaster recovery — PXE boot + mesh restore |
| `mixer pxe-install [machine]` | Install PXE server on any machine in the mesh |
| `mixer pxe-start` | Start the PXE boot server |
| `mixer pxe-stop` | Stop the PXE boot server |
| `mixer update-isos` | Download/update recovery ISOs (Arch, CachyOS, Windows) |
| `mixer offsite [mount]` | Backup to USB drive — auto-detects, copies, ejects |

## the mesh

| Machine | Role | OS | btrfs |
|---------|------|----|-------|
| ryzen | Primary workstation | Arch Linux | yes |
| strix-halo | GPU / AI inference | Arch Linux | yes |
| minisforum | Office PC | Windows 11 | no (rsync) |
| sligar | Compute / training | Arch Linux | yes |

## disaster recovery — PXE built in

Machine dies. No USB drive needed. No panic.

```
mixer recover ryzen
```

Shadow does everything:
1. Checks which machines have snapshots of the dead machine
2. Starts the PXE boot server on the local network
3. Dead machine boots from the network — OS install menu appears
4. Fresh OS installed over the network
5. `mixer restore` pulls the snapshot from the mesh
6. Machine is back. Everything restored.

**Any machine can be the PXE server.** If Strix Halo dies, install PXE on Ryzen:

```
mixer pxe-install ryzen
```

ISOs are downloaded and ready. Arch Linux, CachyOS, Windows 11. Shadow keeps them current. The recovery server is always one command away on any machine in the mesh.

## offsite backup — USB drive

Plug in a USB drive. One command. Shadow copies the latest snapshot from every machine in the mesh, writes a manifest, syncs, ejects. Grab the drive. Take it offsite.

```
mixer offsite
```

- Auto-detects the USB drive — no mount point needed
- Copies latest snapshot from every reachable machine
- Writes a manifest with timestamps and machine list
- Syncs and auto-ejects when done
- House burns down, you've got everything on a drive in your truck

## why "Kansas City Shuffle"?

> *Everyone looks left, Shadow moves the data right.*

No machine holds its own backup on itself. The snapshot is always somewhere else — everywhere else. If a drive dies, if ransomware hits, if an update goes sideways — the other machines have it. All of them.

Shadow doesn't run on a schedule. He doesn't announce himself. He watches the network, waits for quiet, and moves data while you sleep. Set and forget. A weapon that fires itself.

## Windows support (VSS)

Windows machines are full mesh members. Shadow uses **Volume Shadow Copy Service (VSS)** for consistent snapshots on Windows — the same technology Windows uses for System Restore.

- VSS creates a point-in-time shadow copy of the volume
- Shadow mounts the shadow copy, robocopy's key directories
- Snapshots include: user profile, SSH config, Windows Terminal settings
- If VSS fails (permissions, service stopped), falls back to direct robocopy
- Distribution uses scp between Windows and Linux machines

**Windows requirements:**
- OpenSSH Server running (already set up on Minisforum)
- Admin user in `administrators_authorized_keys`
- VSS service running (default on Windows 11)

**No extra software needed on Windows.** It's all built-in.

## completely autonomous

Once mixer is installed and the daemon is running, there is nothing to do. Ever.

- Shadow watches the network — moves data when traffic is quiet
- New machine joins — `mixer add` and it's in the mesh instantly
- Machine goes offline — Shadow skips it, distributes to the rest
- Machine comes back — next cycle picks it up automatically
- Snapshots rotate — old ones get cleaned, fresh ones replace them
- No timers, no cron jobs, no maintenance. Set and forget.

This is a weapon that fires itself.

## Shadow

Shadow is agent #6 in the [halo-ai family](https://github.com/bong-water-water-bong/halo-ai), part of Meek's Reflex security group. He monitors file integrity and manages the SSH mesh. mixer is his primary tool.

He doesn't ask. He doesn't announce. He moves in silence. When everything goes wrong, he's the one who has the data.

## license

Apache 2.0
