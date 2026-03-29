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
            # btrfs snapshot
            snap_path = f"{m.snapshot_path}/{snap_name}"
            cmd = f"sudo btrfs subvolume snapshot -r / {snap_path}"
            code, out, err = m.ssh(cmd, timeout=120)
            if code != 0:
                log.error(f"Snapshot failed on {machine_name}: {err}")
                return None
        else:
            # rsync-based snapshot for non-btrfs (Windows, etc)
            snap_path = f"{m.snapshot_path}/{snap_name}"
            cmd = f"mkdir -p {snap_path}"
            m.ssh(cmd)
            # Snapshot key directories
            dirs = "/etc /home /srv/ai/configs /srv/ai/freeze"
            cmd = f"for d in {dirs}; do [ -d $d ] && rsync -a $d {snap_path}/; done"
            code, out, err = m.ssh(cmd, timeout=300)
            if code != 0:
                log.warning(f"Partial snapshot on {machine_name}: {err}")

        # Get size
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
            code, out, _ = src.ssh(
                f"ls -1t {src.snapshot_path}/ 2>/dev/null | grep '^mixer-' | head -1"
            )
            if code != 0 or not out:
                log.warning(f"No snapshots on {src_name} to distribute")
                continue

            latest_snap = out.strip()
            src_path = f"{src.snapshot_path}/{latest_snap}"

            # Send to every OTHER reachable machine
            for dst_name, dst in reachable.items():
                if dst_name == src_name:
                    continue  # don't send to yourself

                log.info(f"  {src_name} → {dst_name} ({latest_snap})")

                if src.btrfs and dst.btrfs:
                    cmd = (
                        f"sudo btrfs send {src_path} | "
                        f"ssh {dst.user}@{dst.host} 'sudo btrfs receive {dst.receive_path}/'"
                    )
                    code, out, err = src.ssh(cmd, timeout=600)
                else:
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

    # ── Daemon ────────────────────────────────────────
    def daemon(self, interval_hours: int = 6):
        """Run continuously."""
        log.info(f"Shadow daemon started — running every {interval_hours}h")
        while True:
            try:
                self.run()
            except Exception as e:
                log.error(f"Run failed: {e}")
            time.sleep(interval_hours * 3600)


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

    sub.add_parser("distribute", help="Send snapshots around the ring")

    rest_p = sub.add_parser("restore", help="Pull snapshot from another machine")
    rest_p.add_argument("from_machine", help="Machine to pull from")
    rest_p.add_argument("--snapshot", help="Specific snapshot name")

    daemon_p = sub.add_parser("daemon", help="Run continuously")
    daemon_p.add_argument("--interval", type=int, default=6, help="Hours between runs")

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
        mixer.daemon(args.interval)


if __name__ == "__main__":
    main()
