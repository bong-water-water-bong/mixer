<div align="center">

# mixer

### no nas. no single point of failure. every machine backs up another.

**distributed mesh snapshot system for [halo-ai](https://github.com/bong-water-water-bong/halo-ai) — btrfs snapshots over ssh in a ring**

</div>

---

## what is mixer?

mixer is a distributed backup system where every machine on your network holds a snapshot of another machine. no central NAS, no single point of failure. if any machine dies, its latest snapshot lives on another machine in the mesh.

```
ryzen ──snapshot──→ strixhalo
strixhalo ──snapshot──→ minisforum
minisforum ──snapshot──→ ryzen
```

every machine runs mixer. every machine takes its own local snapshots (normal btrfs/snapper). but mixer also sends one snapshot to the next machine in the ring over ssh. so every machine has:

1. its own local snapshots (normal)
2. one snapshot from another machine (mixer)

if your hard drive dies and you can't recover your local snapshots, the mixer snapshot on the other machine has you covered.

## why not a nas?

| nas | mixer |
|---|---|
| single point of failure | no single point — distributed |
| nas dies, backups gone | one machine dies, snapshot lives elsewhere |
| extra hardware cost | uses existing disk space |
| one physical location | spans your whole network |
| you maintain it | self-managing, self-healing |
| another thing to break | nothing extra to break |

## how it works

1. **ring topology** — machines form a ring. each sends snapshots to the next.
2. **btrfs send/receive** — incremental, efficient, native. only sends changes.
3. **ssh transport** — encrypted, authenticated, uses existing ssh keys.
4. **scheduled** — systemd timer runs daily (or hourly, configurable).
5. **self-healing** — if a machine goes offline, mixer skips it and retries next cycle.
6. **space managed** — keeps only the last N snapshots on each remote machine.

## commands

```bash
# show the ring — who backs up who
mixer ring

# manually send snapshot to your backup partner
mixer send

# receive a snapshot from your source partner
mixer receive

# check status of all machines in the mesh
mixer status

# add a new machine to the ring
mixer join <hostname>

# remove a machine from the ring
mixer leave <hostname>

# restore from a remote snapshot
mixer restore <hostname> <snapshot>

# show all remote snapshots stored on this machine
mixer list
```

## configuration

```ini
# /etc/mixer.conf

[mesh]
# this machine's name
hostname = ryzen

# who do i send my snapshots to?
backup_target = strixhalo

# who sends their snapshots to me?
backup_source = minisforum

# ssh connection details
ssh_user = bcloud
ssh_key = ~/.ssh/id_ed25519

[snapshots]
# what subvolumes to snapshot and send
subvolumes = /,/home

# how many remote snapshots to keep
keep_remote = 3

# how many local snapshots to keep
keep_local = 10

[schedule]
# how often to send (systemd timer)
interval = daily

# time to run
time = 03:00
```

## setup

```bash
# install on every machine
git clone https://github.com/bong-water-water-bong/mixer.git /opt/mixer
cd /opt/mixer && sudo ./install.sh

# on machine A
mixer join strixhalo    # A sends to strixhalo
mixer join --source minisforum  # minisforum sends to A

# repeat for each machine in the ring
# mixer auto-detects btrfs subvolumes and configures snapshotting
```

## the family

| member | role |
|---|---|
| [halo ai](https://github.com/bong-water-water-bong/halo-ai) | the father — bare-metal ai stack |
| [vault](https://github.com/bong-water-water-bong/vault) | backup verification — checks mixer's work |
| [mixer](https://github.com/bong-water-water-bong/mixer) | distributed mesh snapshots — no nas needed |

## license

Apache 2.0
