"""Wi-Fi access point management via hostapd and dnsmasq.

Satisfies: FR-006 to FR-010 (5 GHz AP, WPA2, DHCP).
Templates per PiAuto-IG-001 §6–7.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from piauto.config import WifiConfig
from piauto.log import get_logger

log = get_logger("wifi")

HOSTAPD_CONF = Path("/tmp/hostapd.conf")
DNSMASQ_CONF = Path("/tmp/dnsmasq.conf")
AP_INTERFACE = "wlan0"
AP_IP = "192.168.1.1"
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
dhcp-range=192.168.1.100,192.168.1.199,255.255.255.0,1h
bind-interfaces
no-resolv
no-daemon
log-dhcp
"""


class WifiManager:
    """Manages the Wi-Fi AP lifecycle (hostapd + dnsmasq)."""

    def __init__(self, config: WifiConfig) -> None:
        self._config = config
        self._hostapd_proc: asyncio.subprocess.Process | None = None
        self._dnsmasq_proc: asyncio.subprocess.Process | None = None

    def _write_configs(self) -> None:
        """Generate hostapd and dnsmasq configuration files."""
        hostapd_conf = HOSTAPD_TEMPLATE.format(
            interface=AP_INTERFACE,
            ssid=self._config.ssid,
            channel=self._config.channel,
            password=self._config.password,
            country=self._config.country,
        )
        HOSTAPD_CONF.write_text(hostapd_conf)
        log.debug("Wrote %s", HOSTAPD_CONF)

        dnsmasq_conf = DNSMASQ_TEMPLATE.format(interface=AP_INTERFACE)
        DNSMASQ_CONF.write_text(dnsmasq_conf)
        log.debug("Wrote %s", DNSMASQ_CONF)

    async def _setup_interface(self) -> bool:
        """Configure the wireless interface with a static IP and regulatory domain."""
        cmds = [
            ["ip", "link", "set", AP_INTERFACE, "up"],
            ["ip", "addr", "flush", "dev", AP_INTERFACE],
            ["ip", "addr", "add", f"{AP_IP}/{AP_NETMASK}", "dev", AP_INTERFACE],
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

    async def start_ap(self) -> bool:
        """Start hostapd and dnsmasq. Returns True on success."""
        self._write_configs()

        if not await self._setup_interface():
            log.error("Failed to configure %s", AP_INTERFACE)
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

        Monitors dnsmasq stderr for DHCPACK messages. Returns True if
        a client joined, False on timeout.
        """
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
                    "ip", "addr", "flush", "dev", AP_INTERFACE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except FileNotFoundError:
                pass
            log.info("AP stopped")
        else:
            log.debug("AP was not running — nothing to stop")
