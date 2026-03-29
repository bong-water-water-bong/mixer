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
        ryzen ←──→ strix-halo
          ↕    ╲  ╱    ↕
          ↕     ╲╱     ↕
          ↕     ╱╲     ↕
          ↕    ╱  ╲    ↕
      minisforum ←──→ sligar
```

4 machines. Each holds 3 snapshots. Any machine dies — the other three have its data.

## install

```bash
git clone https://github.com/bong-water-water-bong/mixer.git
cd mixer
bash install.sh
```

Edit `/etc/mixer/config.json` with your machines, then:

```bash
mixer status        # see the mesh
mixer run           # snapshot everything + distribute
mixer daemon        # run every 6 hours (or systemctl start mixer)
```

## how it works

1. Shadow SSH's into each machine in the ring
2. Takes a btrfs read-only snapshot (or rsync for non-btrfs like Windows)
3. Sends that snapshot to the NEXT machine via `btrfs send | ssh btrfs receive`
4. Every machine ends up with:
   - Its own local snapshots (normal)
   - One snapshot from the previous machine in the ring (mixer)

## commands

| Command | What it does |
|---------|-------------|
| `mixer status` | Show all machines, snapshot counts, disk space, reachability |
| `mixer snapshot [machine]` | Take a snapshot on one machine |
| `mixer distribute` | Send snapshots around the ring |
| `mixer restore <from>` | Pull a snapshot from another machine |
| `mixer run` | Full cycle: snapshot all + distribute all |
| `mixer daemon` | Run continuously (default: every 6 hours) |

## the mesh

| Machine | Role | OS | btrfs |
|---------|------|----|-------|
| ryzen | Primary workstation | Arch Linux | yes |
| strix-halo | GPU / AI inference | Arch Linux | yes |
| minisforum | Office PC | Windows 11 | no (rsync) |
| sligar | Compute / training | Arch Linux | yes |

## why "Kansas City Shuffle"?

> *Everyone looks left, Shadow moves the data right.*

The beauty of the ring is that no machine holds its own backup on itself. The snapshot is always somewhere else. If a drive dies, if ransomware hits, if an update goes sideways — the snapshot is on a machine that wasn't affected.

Shadow doesn't ask permission. He doesn't announce himself. He moves in the shadows, and when everything goes wrong, he's the one who has the data.

## Shadow

Shadow is agent #6 in the [halo-ai family](https://github.com/bong-water-water-bong/halo-ai), part of Meek's Reflex security group. He monitors file integrity and manages the SSH mesh. mixer is his primary tool.

## license

Apache 2.0
