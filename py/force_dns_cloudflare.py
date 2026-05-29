#!/usr/bin/env python3
"""
Force DNS to Cloudflare 1.1.1.1 (IPv4).

- Windows: no admin. HKCU Internet Settings\\Connections registry patches
  (DefaultConnectionSettings), adapter DNS attempts, optional --persist loop,
  and a 127.0.0.1 forwarder to 1.1.1.1.
- Linux: requires root (sudo).
"""

from __future__ import annotations

import argparse
import datetime
import os
import time
import platform
import shutil
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

CLOUDFLARE_DNS = "1.1.1.1"
UPSTREAM = (CLOUDFLARE_DNS, 53)
RESOLV_CONF = Path("/etc/resolv.conf")
RESOLV_CONTENT = f"""# Force-set by force_dns_cloudflare.py
nameserver {CLOUDFLARE_DNS}
"""


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def log(msg: str) -> None:
    print(f"[*] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}", file=sys.stderr)


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_admin_windows() -> bool:
    if not is_windows():
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# --- Windows (no admin required to run) -------------------------------------

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_CONNECTIONS = INTERNET_SETTINGS + r"\Connections"
PROXY_FLAGS_DIRECT = 0x1


def _winreg():
    import winreg
    return winreg


def patch_connection_settings_blob(value_name: str) -> bool:
    """HKCU Connections blob: bump counter, set direct (0x1) proxy flags — no admin."""
    winreg = _winreg()
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_CONNECTIONS, 0, access) as key:
            raw = winreg.QueryValueEx(key, value_name)[0]
            settings = bytearray(raw)
            if len(settings) < 0xC:
                return False
            settings[0x4:0x8] = (
                int.from_bytes(settings[0x4:0x8], "little") + 1
            ).to_bytes(4, "little")
            settings[0x8:0xC] = PROXY_FLAGS_DIRECT.to_bytes(4, "little")
            winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, bytes(settings))
        return True
    except OSError:
        return False


def patch_all_connection_settings_blobs() -> int:
    winreg = _winreg()
    access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
    count = 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_CONNECTIONS, 0, access) as key:
            i = 0
            while True:
                try:
                    name, data, vtype = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if vtype != winreg.REG_BINARY or not isinstance(data, (bytes, bytearray)):
                    continue
                if len(data) < 0xC:
                    continue
                if patch_connection_settings_blob(name):
                    count += 1
    except OSError:
        pass
    return count


def disable_inet_proxy_dwords() -> None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE
        ) as key:
            for name in ("ProxyEnable", "MigrateProxy"):
                try:
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0)
                except OSError:
                    pass
    except OSError:
        pass


def apply_wininet_hkcu() -> bool:
    n = patch_all_connection_settings_blobs()
    disable_inet_proxy_dwords()
    if n:
        log(f"HKCU connection settings patched ({n} blob(s), direct / no proxy)")
    return n > 0


def flush_dns_cache_windows() -> None:
    run(["ipconfig", "/flushdns"])


def apply_dns_windows_once(forwarder_only: bool) -> bool:
    apply_wininet_hkcu()
    if forwarder_only:
        return False
    return (
        try_set_dns_windows_adapter()
        or try_set_dns_windows_netsh()
        or try_set_dns_windows_registry()
    )


def persist_dns_loop(interval: float, forwarder_only: bool) -> None:
    log(f"Persist mode: re-applying every {interval}s (Ctrl+C to stop)")
    while True:
        apply_dns_windows_once(forwarder_only)
        flush_dns_cache_windows()
        print(
            f"dns -> {CLOUDFLARE_DNS} @ "
            f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        time.sleep(interval)


def powershell(script: str) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    )


def try_set_dns_windows_adapter() -> bool:
    """Try to set adapter DNS without prompting for UAC. Often denied for standard users."""
    ps = rf"""
$dns = "{CLOUDFLARE_DNS}"
$ok = $false
Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
  Where-Object {{ $_.Status -eq 'Up' }} |
  ForEach-Object {{
    try {{
      Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses $dns -ErrorAction Stop
      $ok = $true
      Write-Output "set:$($_.Name)"
    }} catch {{
      Write-Output "denied:$($_.Name):$($_.Exception.Message)"
    }}
  }}
if ($ok) {{ exit 0 }} else {{ exit 1 }}
"""
    r = powershell(ps)
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("set:"):
            log(f"Adapter DNS set: {line[4:]}")
        elif line.startswith("denied:"):
            warn(f"Could not set adapter '{line.split(':', 2)[1]}' (no permission)")
    if r.returncode == 0:
        run(["ipconfig", "/flushdns"])
        return True
    return False


def try_set_dns_windows_netsh() -> bool:
    r = run(["netsh", "interface", "ipv4", "show", "interfaces"])
    if r.returncode != 0:
        return False
    ok = False
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit() and parts[1] == "connected":
            idx = parts[0]
            rr = run(
                [
                    "netsh",
                    "interface",
                    "ipv4",
                    "set",
                    "dns",
                    f"name={idx}",
                    "source=static",
                    f"address={CLOUDFLARE_DNS}",
                    "register=primary",
                ]
            )
            if rr.returncode == 0:
                log(f"netsh: DNS on interface index {idx}")
                ok = True
    if ok:
        run(["ipconfig", "/flushdns"])
    return ok


def try_set_dns_windows_registry() -> bool:
    """Per-adapter NameServer under HKLM — usually needs admin; try anyway."""
    ps = rf"""
$dns = "{CLOUDFLARE_DNS}"
$path = "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
$ok = $false
if (-not (Test-Path $path)) {{ exit 1 }}
Get-ChildItem $path | ForEach-Object {{
  try {{
    Set-ItemProperty -Path $_.PSPath -Name NameServer -Value $dns -ErrorAction Stop
    $ok = $true
    Write-Output "reg:$($_.PSChildName)"
  }} catch {{ }}
}}
if ($ok) {{ exit 0 }} else {{ exit 1 }}
"""
    r = powershell(ps)
    if r.returncode == 0:
        for line in (r.stdout or "").splitlines():
            if line.strip().startswith("reg:"):
                log(f"Registry DNS: {line.strip()[4:]}")
        run(["ipconfig", "/flushdns"])
        return True
    return False


def verify_windows_adapter() -> bool:
    ps = rf"""
$dns = "{CLOUDFLARE_DNS}"
(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object {{ $_.ServerAddresses -contains $dns }} |
  Measure-Object).Count -gt 0
"""
    r = powershell(ps)
    return r.returncode == 0 and (r.stdout or "").strip().lower() == "true"


def dns_query_udp(server: str, qname: str, port: int = 53) -> bool:
    """Send A-record query to server; return True if we get a plausible response."""
    label_parts = qname.strip(".").split(".")
    qname_enc = b"".join(bytes([len(p)]) + p.encode() for p in label_parts) + b"\x00"
    header = struct.pack("!HHHHHH", 0xABCD, 0x0100, 1, 0, 0, 0)
    packet = header + qname_enc + struct.pack("!HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(packet, (server, port))
        data, _ = sock.recvfrom(4096)
        return len(data) > 12 and struct.unpack("!H", data[2:4])[0] & 0x0F == 0
    except OSError:
        return False
    finally:
        sock.close()


class DnsForwarder:
    """UDP DNS proxy: listen locally, forward to Cloudflare 1.1.1.1."""

    def __init__(self, host: str = "127.0.0.1", port: int = 53) -> None:
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> int:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for candidate in (self.port, 5353, 53535):
            try:
                self._sock.bind((self.host, candidate))
                self.port = candidate
                break
            except OSError:
                continue
        else:
            raise OSError(f"Could not bind DNS forwarder on {self.host}")

        t = threading.Thread(target=self._serve, daemon=True)
        t.start()
        return self.port

    def _serve(self) -> None:
        assert self._sock is not None
        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        upstream.settimeout(5)
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except OSError:
                break
            try:
                upstream.sendto(data, UPSTREAM)
                reply, _ = upstream.recvfrom(4096)
                self._sock.sendto(reply, addr)
            except OSError:
                pass
        upstream.close()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()


def force_dns_windows(
    forwarder_only: bool,
    persist: bool,
    interval: float,
) -> int:
    print(f"Forcing DNS to Cloudflare {CLOUDFLARE_DNS} (Windows, no UAC prompt)\n")

    if is_admin_windows():
        log("Running elevated — adapter change is more likely to succeed.")
    else:
        log("Running as standard user — skipping elevation prompts.")

    if persist:
        forwarder = DnsForwarder()
        try:
            port = forwarder.start()
            log(f"Background forwarder: 127.0.0.1:{port} -> {CLOUDFLARE_DNS}")
        except OSError as e:
            warn(f"Forwarder failed to start: {e}")
            forwarder = None
        try:
            persist_dns_loop(interval, forwarder_only)
        except KeyboardInterrupt:
            print()
            if forwarder:
                forwarder.stop()
        return 0

    changed = apply_dns_windows_once(forwarder_only)
    if verify_windows_adapter():
        log("System adapter DNS reports 1.1.1.1")
        if dns_query_udp(CLOUDFLARE_DNS, "cloudflare.com"):
            print(f"\nDone. System DNS is {CLOUDFLARE_DNS}.")
            return 0

    forwarder = DnsForwarder()
    try:
        port = forwarder.start()
    except OSError as e:
        warn(f"Could not start local DNS forwarder: {e}")
        return 1

    log(f"Local DNS forwarder: {forwarder.host}:{port} -> {CLOUDFLARE_DNS}")

    if not changed and port != 53:
        warn(
            "Could not change adapter DNS without admin. "
            "Settings → Network → your connection → DNS → Manual → 127.0.0.1 "
            f"(only works if your PC allows editing DNS without admin). "
            f"Forwarder is on port {port}, not 53."
        )
    elif not changed:
        log("Trying to point adapters at local forwarder (127.0.0.1)...")
        ps = r"""
$dns = "127.0.0.1"
Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
  Where-Object { $_.Status -eq 'Up' } |
  ForEach-Object {
    try {
      Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses $dns -ErrorAction Stop
    } catch { }
  }
"""
        powershell(ps)

    target = forwarder.host if port == 53 else CLOUDFLARE_DNS
    check_port = port if port != 53 else 53
    if dns_query_udp(target, "cloudflare.com", check_port) or dns_query_udp(
        CLOUDFLARE_DNS, "cloudflare.com"
    ):
        print(
            f"\nForwarder running. Queries via 127.0.0.1:{port} use {CLOUDFLARE_DNS}. "
            "Press Ctrl+C to stop."
        )
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print()
        forwarder.stop()
        return 0

    warn("Forwarder started but DNS check failed.")
    forwarder.stop()
    return 1


# --- Linux (root) -----------------------------------------------------------


def need_root_linux() -> None:
    if os.geteuid() != 0:
        print("On Linux this script must run as root. Try:", file=sys.stderr)
        print(f"  sudo {sys.executable} {Path(__file__).resolve()}", file=sys.stderr)
        sys.exit(1)


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
        if line and line[0].isdigit():
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


def flush_caches_linux() -> None:
    if has("resolvectl"):
        run(["resolvectl", "flush-caches"])
    if has("systemctl") and has("nscd"):
        run(["systemctl", "restart", "nscd"])


def verify_linux() -> bool:
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


def force_dns_linux() -> int:
    need_root_linux()
    print(f"Forcing DNS to Cloudflare {CLOUDFLARE_DNS}\n")
    stop_dns_overwriters()
    via_network_manager()
    via_resolvectl()
    write_resolv_conf()
    flush_caches_linux()
    if verify_linux():
        print(f"\nDone. DNS should be {CLOUDFLARE_DNS}.")
        return 0
    warn("\nWrote config but verification was inconclusive.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Force DNS to Cloudflare 1.1.1.1")
    parser.add_argument(
        "--forwarder-only",
        action="store_true",
        help="Windows: skip adapter changes, only run local 1.1.1.1 forwarder",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Windows: loop forever (HKCU registry + DNS), re-applying every --interval",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Seconds between persist loop iterations (default: 0.5)",
    )
    args = parser.parse_args()

    if is_windows():
        return force_dns_windows(args.forwarder_only, args.persist, args.interval)
    return force_dns_linux()


if __name__ == "__main__":
    sys.exit(main())
