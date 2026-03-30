"""Wi-Fi access point management via hostapd and dnsmasq.

Satisfies: FR-006 to FR-010 (5 GHz AP, WPA2, DHCP).
Templates per PiAuto-IG-001 §6–7.

Supports two modes:
- **AP+STA mode:** If ``uap0`` exists (created by udev rule), the AP runs on
  ``uap0`` (192.168.50.1/24) while ``wlan0`` stays connected to infrastructure
  WiFi. Both share one radio on the same channel.
- **Standalone AP mode:** ``wlan0`` is used directly (192.168.1.1/24).

The mode is auto-detected at construction time by checking for ``uap0``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from piauto.config import WifiConfig
from piauto.log import get_logger

log = get_logger("wifi")

_RUN_DIR = Path("/run/piauto")
HOSTAPD_CONF = _RUN_DIR / "hostapd.conf"
DNSMASQ_CONF = _RUN_DIR / "dnsmasq.conf"

# Standalone AP mode (wlan0)
_STANDALONE_INTERFACE = "wlan0"
_STANDALONE_IP = "192.168.1.1"
_STANDALONE_DHCP_START = "192.168.1.100"
_STANDALONE_DHCP_END = "192.168.1.199"

# AP+STA mode (uap0 virtual interface alongside wlan0 station)
_AP_STA_INTERFACE = "uap0"
_AP_STA_IP = "192.168.50.1"
_AP_STA_DHCP_START = "192.168.50.100"
_AP_STA_DHCP_END = "192.168.50.199"

AP_NETMASK = "24"

HOSTAPD_TEMPLATE = """\
interface={interface}
driver=nl80211
ssid={ssid}
hw_mode=a
channel={channel}
ieee80211n=1
ieee80211ac=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
country_code={country}
max_num_sta=1
"""

DNSMASQ_TEMPLATE = """\
interface={interface}
dhcp-range={dhcp_start},{dhcp_end},255.255.255.0,1h
bind-interfaces
no-resolv
no-daemon
log-dhcp
"""


def _detect_ap_sta_mode() -> bool:
    """Return True if uap0 interface exists (AP+STA mode)."""
    return Path("/sys/class/net/uap0").exists()


class WifiManager:
    """Manages the Wi-Fi AP lifecycle (hostapd + dnsmasq).

    Auto-detects AP+STA mode (uap0 present) vs standalone mode.
    """

    def __init__(self, config: WifiConfig) -> None:
        self._config = config
        self._hostapd_proc: asyncio.subprocess.Process | None = None
        self._dnsmasq_proc: asyncio.subprocess.Process | None = None
        self._nm_managed = False  # True if NetworkManager is managing the AP

        # Detect AP mode
        self._ap_sta = _detect_ap_sta_mode()
        if self._ap_sta:
            self._interface = _AP_STA_INTERFACE
            self._ip = _AP_STA_IP
            self._dhcp_start = _AP_STA_DHCP_START
            self._dhcp_end = _AP_STA_DHCP_END
            log.info("AP+STA mode detected (uap0 present) — AP on %s at %s",
                     self._interface, self._ip)
        else:
            self._interface = _STANDALONE_INTERFACE
            self._ip = _STANDALONE_IP
            self._dhcp_start = _STANDALONE_DHCP_START
            self._dhcp_end = _STANDALONE_DHCP_END
            log.info("Standalone AP mode — AP on %s at %s",
                     self._interface, self._ip)

    @property
    def ap_ip(self) -> str:
        """IP address of the AP interface (used by BLE credential exchange)."""
        return self._ip

    @property
    def ap_interface(self) -> str:
        """Name of the AP network interface."""
        return self._interface

    def _write_configs(self) -> None:
        """Generate hostapd and dnsmasq configuration files."""
        _RUN_DIR.mkdir(parents=True, exist_ok=True)
        hostapd_conf = HOSTAPD_TEMPLATE.format(
            interface=self._interface,
            ssid=self._config.ssid,
            channel=self._config.channel,
            password=self._config.password,
            country=self._config.country,
        )
        HOSTAPD_CONF.write_text(hostapd_conf)
        log.debug("Wrote %s", HOSTAPD_CONF)

        dnsmasq_conf = DNSMASQ_TEMPLATE.format(
            interface=self._interface,
            dhcp_start=self._dhcp_start,
            dhcp_end=self._dhcp_end,
        )
        DNSMASQ_CONF.write_text(dnsmasq_conf)
        log.debug("Wrote %s", DNSMASQ_CONF)

    async def _setup_interface(self) -> bool:
        """Configure the wireless interface with a static IP and regulatory domain."""
        cmds = [
            ["ip", "link", "set", self._interface, "up"],
            ["ip", "addr", "flush", "dev", self._interface],
            ["ip", "addr", "add", f"{self._ip}/{AP_NETMASK}", "dev", self._interface],
            ["iw", "reg", "set", self._config.country],
        ]
        for cmd in cmds:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    log.warning("Command %s failed: %s", cmd, stderr.decode().strip())
                    return False
            except FileNotFoundError:
                log.warning("Command not found: %s", cmd[0])
                return False
        return True

    async def _check_nm_managed_ap(self) -> bool:
        """Check if NetworkManager is already managing an AP on our interface."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == self._interface and parts[2] == "connected":
                    return True
        except (FileNotFoundError, TimeoutError):
            pass
        return False

    async def start_ap(self) -> bool:
        """Start the WiFi access point. Returns True on success.

        If PIAUTO_NO_AP is set, skip entirely (dev mode).
        If NetworkManager already manages an AP on our interface, use that.
        Otherwise, start hostapd + dnsmasq manually.
        """
        if os.environ.get("PIAUTO_NO_AP"):
            log.warning(
                "PIAUTO_NO_AP set — skipping AP start (mock mode). "
                "WiFi client connection preserved."
            )
            return True

        # Check if NetworkManager is already running the AP
        if await self._check_nm_managed_ap():
            self._nm_managed = True
            log.info("AP already managed by NetworkManager on %s — skipping hostapd/dnsmasq",
                     self._interface)
            return True

        self._write_configs()

        if not await self._setup_interface():
            log.error("Failed to configure %s", self._interface)
            return False

        try:
            self._hostapd_proc = await asyncio.create_subprocess_exec(
                "hostapd", str(HOSTAPD_CONF),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info("hostapd started (pid %d)", self._hostapd_proc.pid)
        except FileNotFoundError:
            log.error("hostapd binary not found")
            return False

        # Brief delay for hostapd to bind the interface
        await asyncio.sleep(0.5)

        if self._hostapd_proc.returncode is not None:
            stderr = (await self._hostapd_proc.stderr.read()).decode()
            log.error("hostapd exited immediately: %s", stderr.strip())
            self._hostapd_proc = None
            return False

        try:
            self._dnsmasq_proc = await asyncio.create_subprocess_exec(
                "dnsmasq", f"--conf-file={DNSMASQ_CONF}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info("dnsmasq started (pid %d)", self._dnsmasq_proc.pid)
        except FileNotFoundError:
            log.error("dnsmasq binary not found")
            await self.stop_ap()
            return False

        return True

    async def wait_for_client(self, timeout: float = 30.0) -> bool:
        """Wait for a DHCP lease assignment (phone joined AP).

        When NetworkManager manages the AP, polls the NM lease file.
        Otherwise, monitors our dnsmasq stderr for DHCPACK messages.
        Returns True if a client joined, False on timeout.
        """
        if self._nm_managed:
            return await self._wait_for_client_nm(timeout)

        if not self._dnsmasq_proc or not self._dnsmasq_proc.stderr:
            log.warning("dnsmasq not running — cannot wait for client")
            return False

        try:
            async with asyncio.timeout(timeout):
                while True:
                    line = await self._dnsmasq_proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").strip()
                    log.debug("dnsmasq: %s", text)
                    if "DHCPACK" in text or "DHCPOFFER" in text:
                        log.info("Phone joined AP: %s", text)
                        return True
        except TimeoutError:
            log.warning("WiFi timeout: no client joined within %.0f s", timeout)
            return False

        return False

    async def _wait_for_client_nm(self, timeout: float) -> bool:
        """Wait for a NEW client by polling the NetworkManager dnsmasq lease file.

        Tracks the file's modification time to detect new/renewed leases.
        Also checks ARP table as a fallback to confirm the client is reachable.
        """
        lease_file = Path(f"/var/lib/NetworkManager/dnsmasq-{self._interface}.leases")
        log.info("Waiting for client on %s (NM lease file: %s)", self._interface, lease_file)

        # Record initial mtime so we detect any update (even same-IP renewals)
        try:
            initial_mtime = lease_file.stat().st_mtime if lease_file.exists() else 0.0
        except OSError:
            initial_mtime = 0.0

        try:
            async with asyncio.timeout(timeout):
                while True:
                    # Check if lease file was modified since we started waiting
                    if lease_file.exists():
                        try:
                            current_mtime = lease_file.stat().st_mtime
                        except OSError:
                            current_mtime = 0.0

                        if current_mtime > initial_mtime:
                            content = lease_file.read_text().strip()
                            if content:
                                first_line = content.splitlines()[0]
                                log.info("Phone joined AP (NM lease): %s", first_line)
                                return True

                    # Fallback: check ARP table for any REACHABLE client on the AP interface
                    if await self._check_arp_client():
                        log.info("Phone joined AP (ARP reachable on %s)", self._interface)
                        return True

                    await asyncio.sleep(1.0)
        except TimeoutError:
            log.warning("WiFi timeout: no client joined within %.0f s", timeout)
            return False

    async def _check_arp_client(self) -> bool:
        """Check if any client is REACHABLE in the ARP table on the AP interface."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "neigh", "show", "dev", self._interface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().strip().splitlines():
                if "REACHABLE" in line or "STALE" in line:
                    return True
        except (FileNotFoundError, OSError):
            pass
        return False

    async def stop_ap(self) -> None:
        """Stop hostapd and dnsmasq, release interface."""
        was_running = False
        for name, proc in [("hostapd", self._hostapd_proc), ("dnsmasq", self._dnsmasq_proc)]:
            if proc and proc.returncode is None:
                was_running = True
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                    log.info("%s stopped", name)
                except TimeoutError:
                    proc.kill()
                    log.warning("%s killed (did not stop in 5 s)", name)

        self._hostapd_proc = None
        self._dnsmasq_proc = None

        # Only flush the interface if we actually started the AP —
        # otherwise we'd wipe the user's existing WiFi connection
        if was_running:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ip", "addr", "flush", "dev", self._interface,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except FileNotFoundError:
                pass
            log.info("AP stopped")
        else:
            log.debug("AP was not running — nothing to stop")
