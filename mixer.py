#!/usr/bin/env python3
# mixer — stamped by the architect
# Shadow's SSH Mixer: one app to control every machine, distribute snapshots,
# eliminate single points of failure. He moves in the shadows.
"""
mixer — distributed mesh snapshot system

Shadow SSH's into every machine in the ring, takes btrfs snapshots,
and distributes them to the next machine. No NAS. No single point of failure.
If any machine dies, its snapshot lives on another.

Ring topology:
    ryzen → strix-halo → minisforum → ryzen

Usage:
    mixer status          — show all machines, snapshots, health
    mixer snapshot        — take local snapshot
    mixer distribute      — send snapshots around the ring
    mixer restore <from>  — pull a snapshot from another machine
    mixer run             — full cycle: snapshot all, distribute all
    mixer daemon          — run continuously (every 6 hours)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [shadow] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mixer")

# ── Config ────────────────────────────────────────────
CONFIG_PATH = Path(os.environ.get("MIXER_CONFIG", "/etc/mixer/config.json"))
DEFAULT_CONFIG = Path(__file__).parent / "config.json"
STATE_DIR = Path("/var/lib/mixer")
STATE_FILE = STATE_DIR / "state.json"


@dataclass
class Machine:
    name: str
    host: str           # SSH host alias or IP
    user: str
    snapshot_path: str  # Where local snapshots live
    receive_path: str   # Where incoming ring snapshots land
    os_type: str        # linux or windows
    btrfs: bool         # Has btrfs?

    def ssh(self, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
        """Execute a command on this machine via SSH."""
        full_cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                    f"{self.user}@{self.host}", cmd]
        try:
            r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except Exception as e:
            return -1, "", str(e)

    def scp_to(self, local_path: str, remote_path: str, timeout: int = 300) -> bool:
        """Copy a file to this machine."""
        try:
            r = subprocess.run(
                ["scp", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                 local_path, f"{self.user}@{self.host}:{remote_path}"],
                capture_output=True, text=True, timeout=timeout
            )
            return r.returncode == 0
        except Exception:
            return False

    def is_reachable(self) -> bool:
        code, out, _ = self.ssh("echo ok", timeout=10)
        return code == 0 and "ok" in out


@dataclass
class Snapshot:
    machine: str
    name: str
    timestamp: str
    size_mb: int
    path: str
    distributed_to: Optional[str] = None


class Mixer:
    def __init__(self, config_path: Optional[Path] = None):
        self.config = self._load_config(config_path or CONFIG_PATH)
        self.machines: dict[str, Machine] = {}
        self.ring: list[str] = []
        self.state = self._load_state()

        for m in self.config.get("machines", []):
            self.machines[m["name"]] = Machine(**m)
        self.ring = self.config.get("ring", list(self.machines.keys()))

    def _load_config(self, path: Path) -> dict:
        for p in [path, DEFAULT_CONFIG]:
            if p.exists():
                return json.loads(p.read_text())
        log.warning("No config found, using defaults")
        return {"machines": [], "ring": []}

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
        return {"snapshots": {}, "last_run": None, "history": []}

    def _save_state(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.state, indent=2))

    def _log_history(self, action: str, details: str):
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "details": details,
        }
        self.state.setdefault("history", []).append(entry)
        if len(self.state["history"]) > 200:
            self.state["history"] = self.state["history"][-200:]

    # ── Status ────────────────────────────────────────
    def status(self):
        """Show status of all machines in the mesh."""
        print("\n  ╔═══════════════════════════════════════╗")
        print("  ║  shadow — mesh status                 ║")
        print("  ╚═══════════════════════════════════════╝\n")

        ring_display = " → ".join(self.ring + [self.ring[0]])
        print(f"  ring: {ring_display}\n")

        for name in self.ring:
            m = self.machines.get(name)
            if not m:
                print(f"  ✗ {name} — not configured")
                continue

            reachable = m.is_reachable()
            status = "●" if reachable else "○"
            color_code = "\033[92m" if reachable else "\033[91m"
            reset = "\033[0m"

            print(f"  {color_code}{status}{reset} {name}")
            print(f"    host: {m.host}")
            print(f"    os:   {m.os_type}")
            print(f"    btrfs: {'yes' if m.btrfs else 'no (rsync fallback)'}")

            if reachable:
                # Get snapshot count
                code, out, _ = m.ssh(f"ls {m.snapshot_path}/ 2>/dev/null | wc -l")
                snap_count = out.strip() if code == 0 else "?"
                code, out, _ = m.ssh(f"ls {m.receive_path}/ 2>/dev/null | wc -l")
                recv_count = out.strip() if code == 0 else "?"
                print(f"    local snapshots: {snap_count}")
                print(f"    ring snapshots:  {recv_count}")

                # Disk usage
                code, out, _ = m.ssh("df -h / | tail -1 | awk '{print $4}'")
                if code == 0:
                    print(f"    free space: {out}")
            else:
                print("    status: UNREACHABLE")
            print()

        # Ring health
        reachable_count = sum(1 for n in self.ring if self.machines.get(n, Machine("","","","","","",False)).is_reachable())
        total = len(self.ring)
        print(f"  mesh health: {reachable_count}/{total} machines online")
        if self.state.get("last_run"):
            print(f"  last full run: {self.state['last_run']}")
        print()

    # ── Snapshot ──────────────────────────────────────
    def take_snapshot(self, machine_name: str) -> Optional[Snapshot]:
        """Take a snapshot on a specific machine."""
        m = self.machines.get(machine_name)
        if not m:
            log.error(f"Unknown machine: {machine_name}")
            return None

        if not m.is_reachable():
            log.error(f"{machine_name} is unreachable")
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snap_name = f"mixer-{machine_name}-{ts}"

        if m.btrfs:
            # Linux btrfs snapshot
            snap_path = f"{m.snapshot_path}/{snap_name}"
            cmd = f"sudo btrfs subvolume snapshot -r / {snap_path}"
            code, out, err = m.ssh(cmd, timeout=120)
            if code != 0:
                log.error(f"Snapshot failed on {machine_name}: {err}")
                return None

        elif m.os_type == "windows":
            # Windows VSS shadow copy + robocopy to snapshot dir
            snap_path = f"{m.snapshot_path}\\{snap_name}"
            vss_cmds = (
                f'powershell -Command "'
                f"New-Item -Path '{snap_path}' -ItemType Directory -Force | Out-Null; "
                # Create VSS shadow copy
                f"$shadow = (Get-WmiObject -List Win32_ShadowCopy).Create('C:\\', 'ClientAccessible'); "
                f"$id = $shadow.ShadowID; "
                f"$sc = Get-WmiObject Win32_ShadowCopy | Where-Object {{ $_.ID -eq $id }}; "
                f"$device = $sc.DeviceObject; "
                # Create symlink to shadow copy and robocopy key dirs
                f"$link = 'C:\\mixer-shadow-temp'; "
                f"cmd /c mklink /d $link ($device + '\\'); "
                f"robocopy $link\\Users\\bcloud '{snap_path}\\Users' /E /XJ /R:1 /W:1 /NP /NFL /NDL; "
                f"robocopy $link\\ProgramData\\ssh '{snap_path}\\ssh' /E /R:1 /W:1 /NP /NFL /NDL; "
                # Cleanup
                f"cmd /c rmdir $link; "
                f"$sc.Delete(); "
                f"Write-Host 'VSS snapshot complete'"
                f'"'
            )
            code, out, err = m.ssh(vss_cmds, timeout=300)
            if code != 0:
                # Fallback to simple robocopy without VSS
                log.warning(f"VSS failed on {machine_name}, falling back to robocopy: {err[:100]}")
                fallback_cmds = (
                    f'powershell -Command "'
                    f"New-Item -Path '{snap_path}' -ItemType Directory -Force | Out-Null; "
                    f"robocopy C:\\Users\\bcloud '{snap_path}\\Users' /E /XJ /R:1 /W:1 /NP /NFL /NDL; "
                    f"robocopy C:\\ProgramData\\ssh '{snap_path}\\ssh' /E /R:1 /W:1 /NP /NFL /NDL; "
                    f"robocopy C:\\Users\\bcloud\\AppData\\Local\\Packages\\Microsoft.WindowsTerminal_8wekyb3d8bbwe '{snap_path}\\terminal' /E /R:1 /W:1 /NP /NFL /NDL"
                    f'"'
                )
                code, out, err = m.ssh(fallback_cmds, timeout=300)

        else:
            # Linux non-btrfs — rsync key directories
            snap_path = f"{m.snapshot_path}/{snap_name}"
            cmd = f"mkdir -p {snap_path}"
            m.ssh(cmd)
            dirs = "/etc /home /srv/ai/configs /srv/ai/freeze"
            cmd = f"for d in {dirs}; do [ -d $d ] && rsync -a $d {snap_path}/; done"
            code, out, err = m.ssh(cmd, timeout=300)
            if code != 0:
                log.warning(f"Partial snapshot on {machine_name}: {err}")

        # Get size
        if m.os_type == "windows":
            code, out, _ = m.ssh(f'powershell -Command "(Get-ChildItem -Recurse \'{snap_path}\' | Measure-Object -Property Length -Sum).Sum / 1MB"', timeout=30)
            try:
                size_mb = int(float(out.strip()))
            except Exception:
                size_mb = 0
        else:
            code, out, _ = m.ssh(f"du -sm {snap_path} 2>/dev/null | cut -f1")
            size_mb = int(out) if code == 0 and out.isdigit() else 0

        snap = Snapshot(
            machine=machine_name,
            name=snap_name,
            timestamp=ts,
            size_mb=size_mb,
            path=snap_path,
        )

        self.state["snapshots"][snap_name] = asdict(snap)
        self._log_history("snapshot", f"{machine_name}: {snap_name} ({size_mb}MB)")
        self._save_state()
        log.info(f"Snapshot: {snap_name} ({size_mb}MB)")
        return snap

    # ── Distribute ────────────────────────────────────
    def distribute(self):
        """Full mesh — every machine sends its snapshot to every OTHER machine.
        4 machines = each machine holds 3 snapshots (one from each neighbor)."""
        print("\n  shadow is distributing snapshots across the mesh...\n")

        reachable = {n: m for n, m in self.machines.items() if m.is_reachable()}
        log.info(f"Reachable machines: {list(reachable.keys())}")

        for src_name, src in reachable.items():
            # Find latest snapshot on source
            if src.os_type == "windows":
                code, out, _ = src.ssh(
                    f'powershell -Command "(Get-ChildItem \'{src.snapshot_path}\' -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).Name"'
                )
            else:
                code, out, _ = src.ssh(
                    f"ls -1t {src.snapshot_path}/ 2>/dev/null | grep '^mixer-' | head -1"
                )
            if code != 0 or not out.strip():
                log.warning(f"No snapshots on {src_name} to distribute")
                continue

            latest_snap = out.strip()
            if src.os_type == "windows":
                src_path = f"{src.snapshot_path}\\{latest_snap}"
            else:
                src_path = f"{src.snapshot_path}/{latest_snap}"

            # Send to every OTHER reachable machine
            for dst_name, dst in reachable.items():
                if dst_name == src_name:
                    continue

                log.info(f"  {src_name} → {dst_name} ({latest_snap})")

                if src.btrfs and dst.btrfs:
                    # btrfs to btrfs — fastest path
                    cmd = (
                        f"sudo btrfs send {src_path} | "
                        f"ssh {dst.user}@{dst.host} 'sudo btrfs receive {dst.receive_path}/'"
                    )
                    code, out, err = src.ssh(cmd, timeout=600)

                elif src.os_type == "windows" and dst.os_type != "windows":
                    # Windows to Linux — scp the snapshot dir
                    dst_dir = f"{dst.receive_path}/{latest_snap}"
                    cmd = (
                        f'powershell -Command "scp -r \'{src_path}\' '
                        f'{dst.user}@{dst.host}:{dst_dir}"'
                    )
                    code, out, err = src.ssh(cmd, timeout=600)

                elif src.os_type != "windows" and dst.os_type == "windows":
                    # Linux to Windows — scp to Windows receive path
                    dst_dir = f"{dst.receive_path}\\{latest_snap}"
                    cmd = (
                        f"scp -r {src_path} "
                        f"{dst.user}@{dst.host}:'{dst_dir}'"
                    )
                    code, out, err = src.ssh(cmd, timeout=600)

                elif src.os_type == "windows" and dst.os_type == "windows":
                    # Windows to Windows — scp
                    dst_dir = f"{dst.receive_path}\\{latest_snap}"
                    cmd = (
                        f'powershell -Command "scp -r \'{src_path}\' '
                        f'{dst.user}@{dst.host}:\'{dst_dir}\'"'
                    )
                    code, out, err = src.ssh(cmd, timeout=600)

                else:
                    # Linux non-btrfs to any — rsync
                    cmd = (
                        f"rsync -azP --delete {src_path}/ "
                        f"{dst.user}@{dst.host}:{dst.receive_path}/{latest_snap}/"
                    )
                    code, out, err = src.ssh(cmd, timeout=600)

                if code == 0:
                    log.info(f"  done: {src_name} → {dst_name}")
                    self._log_history("distribute", f"{src_name} → {dst_name}: {latest_snap}")
                else:
                    log.error(f"  FAILED: {src_name} → {dst_name}: {err[:100]}")
                    self._log_history("distribute_fail", f"{src_name} → {dst_name}: {err[:100]}")

        self._save_state()
        log.info("Distribution complete. Every machine holds a snapshot of every other.")

    # ── Restore ───────────────────────────────────────
    def restore(self, from_machine: str, snapshot_name: Optional[str] = None):
        """Pull a snapshot from another machine and restore it locally."""
        src = self.machines.get(from_machine)
        if not src:
            log.error(f"Unknown machine: {from_machine}")
            return

        if not src.is_reachable():
            log.error(f"{from_machine} is unreachable")
            return

        if not snapshot_name:
            # Find latest ring snapshot for our machine
            hostname = os.uname().nodename
            code, out, _ = src.ssh(
                f"ls -1t {src.receive_path}/ 2>/dev/null | grep 'mixer-{hostname}' | head -1"
            )
            if code != 0 or not out:
                log.error(f"No ring snapshots for {hostname} on {from_machine}")
                return
            snapshot_name = out.strip()

        log.info(f"Restoring {snapshot_name} from {from_machine}...")
        self._log_history("restore", f"from {from_machine}: {snapshot_name}")
        self._save_state()
        print(f"\n  To restore, run on this machine:")
        print(f"  sudo btrfs receive /mnt/restore/ < <(ssh {src.user}@{src.host} 'sudo btrfs send {src.receive_path}/{snapshot_name}')")
        print()

    # ── Full Run ──────────────────────────────────────
    def run(self):
        """Full cycle: snapshot every reachable machine, then distribute."""
        log.info("Shadow is making his rounds...")
        print()

        for name in self.ring:
            m = self.machines.get(name)
            if m and m.is_reachable():
                self.take_snapshot(name)
            else:
                log.warning(f"Skipping {name} — unreachable")

        self.distribute()

        self.state["last_run"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        log.info("Shadow has completed his rounds. No single point of failure.")

    # ── Add Node ──────────────────────────────────────
    def add_node(self, name: str, host: str, user: str = "bcloud",
                 snapshot_path: str = "/srv/mixer/snapshots",
                 receive_path: str = "/srv/mixer/ring",
                 os_type: str = "linux", btrfs: bool = True):
        """Add a new machine to the mesh. It immediately joins the full ring bus."""
        m = Machine(name=name, host=host, user=user,
                    snapshot_path=snapshot_path, receive_path=receive_path,
                    os_type=os_type, btrfs=btrfs)

        if not m.is_reachable():
            log.error(f"{name} ({host}) is not reachable via SSH")
            return False

        # Create directories on the new node
        m.ssh(f"mkdir -p {snapshot_path} {receive_path}")

        # Add to config
        self.machines[name] = m
        if name not in self.ring:
            self.ring.append(name)

        # Save updated config
        self._save_config()
        self._log_history("add_node", f"{name} ({host}) joined the mesh")
        log.info(f"{name} added to mesh. {len(self.ring)} nodes in the ring bus.")
        return True

    def remove_node(self, name: str):
        """Remove a machine from the mesh."""
        if name in self.machines:
            del self.machines[name]
        if name in self.ring:
            self.ring.remove(name)
        self._save_config()
        self._log_history("remove_node", f"{name} removed from mesh")
        log.info(f"{name} removed. {len(self.ring)} nodes remain.")

    def _save_config(self):
        """Write current mesh state back to config."""
        cfg = {
            "ring": self.ring,
            "machines": [
                {
                    "name": m.name, "host": m.host, "user": m.user,
                    "snapshot_path": m.snapshot_path, "receive_path": m.receive_path,
                    "os_type": m.os_type, "btrfs": m.btrfs,
                }
                for m in self.machines.values()
            ]
        }
        for p in [CONFIG_PATH, DEFAULT_CONFIG]:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(cfg, indent=2))
                break
            except Exception:
                continue

    # ── Network Load Detection ────────────────────────
    def _network_is_quiet(self, threshold_mbps: float = 50.0) -> bool:
        """Check if network is under light load. Shadow waits for downtime."""
        try:
            # Read bytes, wait 2 seconds, read again
            def _get_bytes():
                with open("/proc/net/dev") as f:
                    for line in f:
                        if ":" in line and "lo" not in line:
                            parts = line.split()
                            return int(parts[1]) + int(parts[9])  # rx + tx
                return 0

            b1 = _get_bytes()
            time.sleep(2)
            b2 = _get_bytes()
            mbps = ((b2 - b1) * 8) / (2 * 1_000_000)  # megabits per second
            log.debug(f"Network load: {mbps:.1f} Mbps (threshold: {threshold_mbps})")
            return mbps < threshold_mbps
        except Exception:
            return True  # if we can't read, assume quiet

    def _wait_for_quiet_network(self, max_wait_minutes: int = 60):
        """Wait until network load drops below threshold. Patient. In the shadows."""
        if self._network_is_quiet():
            return True

        log.info("Network is busy. Shadow is waiting for downtime...")
        waited = 0
        while waited < max_wait_minutes * 60:
            time.sleep(30)
            waited += 30
            if self._network_is_quiet():
                log.info(f"Network is quiet after {waited}s. Shadow moves.")
                return True

        log.warning(f"Network still busy after {max_wait_minutes}m. Proceeding anyway.")
        return False

    # ── Daemon (Watchdog Mode) ────────────────────────
    def daemon(self):
        """Shadow's watchdog. No timer. Watches the network, works when it's quiet.
        Set it and forget it. He knows when to move."""
        log.info("Shadow watchdog started. Watching. Waiting. Set and forget.")

        last_snapshot = 0
        min_interval = 4 * 3600  # At least 4 hours between runs
        check_interval = 60      # Check every minute

        while True:
            try:
                now = time.time()
                time_since_last = now - last_snapshot

                # Only consider running if enough time has passed
                if time_since_last >= min_interval:
                    # Wait for network to be quiet
                    if self._network_is_quiet():
                        log.info("Network is quiet. Shadow is making his rounds.")
                        self.run()
                        last_snapshot = time.time()
                        log.info("Shadow has completed his rounds. Going quiet.")
                    # else: stay quiet, check again next cycle

                time.sleep(check_interval)

            except Exception as e:
                log.error(f"Watchdog error: {e}")
                time.sleep(check_interval)


def main():
    parser = argparse.ArgumentParser(
        description="mixer — Shadow's distributed mesh snapshot system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="stamped by the architect",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show mesh status")
    sub.add_parser("run", help="Full cycle: snapshot all + distribute")

    snap_p = sub.add_parser("snapshot", help="Take snapshot on a machine")
    snap_p.add_argument("machine", nargs="?", default=os.uname().nodename)

    sub.add_parser("distribute", help="Send snapshots to all machines")

    rest_p = sub.add_parser("restore", help="Pull snapshot from another machine")
    rest_p.add_argument("from_machine", help="Machine to pull from")
    rest_p.add_argument("--snapshot", help="Specific snapshot name")

    sub.add_parser("daemon", help="Watchdog mode — works when network is quiet")

    add_p = sub.add_parser("add", help="Add a new machine to the mesh")
    add_p.add_argument("name", help="Machine name")
    add_p.add_argument("host", help="SSH host or IP")
    add_p.add_argument("--user", default="bcloud", help="SSH user")
    add_p.add_argument("--os", default="linux", dest="os_type", help="OS type (linux/windows)")
    add_p.add_argument("--no-btrfs", action="store_true", help="Use rsync instead of btrfs")

    rm_p = sub.add_parser("remove", help="Remove a machine from the mesh")
    rm_p.add_argument("name", help="Machine name to remove")

    sub.add_parser("nodes", help="List all nodes in the mesh")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    mixer = Mixer()

    if args.command == "status":
        mixer.status()
    elif args.command == "snapshot":
        mixer.take_snapshot(args.machine)
    elif args.command == "distribute":
        mixer.distribute()
    elif args.command == "restore":
        mixer.restore(args.from_machine, args.snapshot)
    elif args.command == "run":
        mixer.run()
    elif args.command == "daemon":
        mixer.daemon()
    elif args.command == "add":
        mixer.add_node(args.name, args.host, user=args.user,
                       os_type=args.os_type, btrfs=not args.no_btrfs)
    elif args.command == "remove":
        mixer.remove_node(args.name)
    elif args.command == "nodes":
        print(f"\n  Mesh nodes ({len(mixer.ring)}):\n")
        for name in mixer.ring:
            m = mixer.machines.get(name)
            if m:
                status = "online" if m.is_reachable() else "offline"
                print(f"  {name:15} {m.host:20} {m.os_type:10} {status}")
        print()


if __name__ == "__main__":
    main()
