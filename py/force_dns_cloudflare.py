#!/usr/bin/env python3
"""
Force system DNS to Cloudflare 1.1.1.1 (IPv4).

Requires root: sudo python3 force_dns_cloudflare.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

CLOUDFLARE_DNS = "1.1.1.1"
RESOLV_CONF = Path("/etc/resolv.conf")
RESOLV_CONTENT = f"""# Force-set by force_dns_cloudflare.py
nameserver {CLOUDFLARE_DNS}
"""


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )


def need_root() -> None:
    if os.geteuid() != 0:
        print("This script must run as root. Try:", file=sys.stderr)
        print(f"  sudo {sys.executable} {Path(__file__).resolve()}", file=sys.stderr)
        sys.exit(1)


def log(msg: str) -> None:
    print(f"[*] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}", file=sys.stderr)


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def unstick_resolv_conf() -> None:
    if not RESOLV_CONF.exists():
        return
    if has("chattr"):
        run(["chattr", "-i", str(RESOLV_CONF)])
    if RESOLV_CONF.is_symlink():
        RESOLV_CONF.unlink()
        log("Removed symlink on /etc/resolv.conf")


def stop_dns_overwriters() -> None:
    if has("systemctl"):
        for unit in ("systemd-resolved",):
            r = run(["systemctl", "is-active", unit])
            if r.returncode == 0 and r.stdout.strip() == "active":
                log(f"Stopping {unit} so it does not rewrite resolv.conf")
                run(["systemctl", "stop", unit])
                run(["systemctl", "disable", unit])


def via_network_manager() -> bool:
    if not has("nmcli"):
        return False
    r = run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
    if r.returncode != 0 or not r.stdout.strip():
        return False
    ok = False
    for line in r.stdout.strip().splitlines():
        name = line.split(":", 1)[0] if ":" in line else line
        if not name:
            continue
        log(f"nmcli: setting DNS on connection '{name}'")
        rr = run(
            [
                "nmcli",
                "connection",
                "modify",
                name,
                "ipv4.dns",
                CLOUDFLARE_DNS,
                "ipv4.ignore-auto-dns",
                "yes",
            ]
        )
        if rr.returncode == 0:
            run(["nmcli", "connection", "up", name])
            ok = True
    return ok


def via_resolvectl() -> bool:
    if not has("resolvectl"):
        return False
    r = run(["resolvectl", "status"])
    if r.returncode != 0:
        return False
    ok = False
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Global") or line.startswith("Link"):
            continue
        if line[0].isdigit():
            continue
        iface = line.split()[0]
        log(f"resolvectl: DNS on {iface}")
        rr = run(["resolvectl", "dns", iface, CLOUDFLARE_DNS])
        rr2 = run(["resolvectl", "domain", iface, "~."])
        ok = ok or rr.returncode == 0 or rr2.returncode == 0
    return ok


def write_resolv_conf() -> None:
    unstick_resolv_conf()
    backup = RESOLV_CONF.with_suffix(".conf.bak")
    if RESOLV_CONF.exists() and not backup.exists():
        shutil.copy2(RESOLV_CONF, backup)
        log(f"Backed up to {backup}")
    RESOLV_CONF.write_text(RESOLV_CONTENT, encoding="utf-8")
    os.chmod(RESOLV_CONF, 0o644)
    log(f"Wrote {RESOLV_CONF} -> nameserver {CLOUDFLARE_DNS}")
    if has("chattr"):
        run(["chattr", "+i", str(RESOLV_CONF)])
        log("Made /etc/resolv.conf immutable (+i) — use chattr -i to undo")


def flush_caches() -> None:
    if has("resolvectl"):
        run(["resolvectl", "flush-caches"])
    if has("systemctl") and has("nscd"):
        run(["systemctl", "restart", "nscd"])


def verify() -> bool:
    if not RESOLV_CONF.exists():
        return False
    text = RESOLV_CONF.read_text(encoding="utf-8", errors="replace")
    if CLOUDFLARE_DNS not in text:
        warn(f"{RESOLV_CONF} does not list {CLOUDFLARE_DNS}")
        return False
    if has("getent"):
        r = run(["getent", "hosts", "cloudflare.com"])
        if r.returncode != 0:
            warn("getent hosts cloudflare.com failed — DNS may not be working yet")
            return False
        log(f"lookup ok: {r.stdout.splitlines()[0]}")
    return True


def main() -> int:
    need_root()
    print(f"Forcing DNS to Cloudflare {CLOUDFLARE_DNS}\n")

    stop_dns_overwriters()
    via_network_manager()
    via_resolvectl()
    write_resolv_conf()
    flush_caches()

    if verify():
        print(f"\nDone. DNS should be {CLOUDFLARE_DNS}.")
        return 0
    warn("\nWrote config but verification was inconclusive.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
