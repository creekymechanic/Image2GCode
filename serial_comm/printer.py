import time
import threading
from typing import Dict, List, Callable, Optional

# ── Port auto-detection ───────────────────────────────────────────────────────
# USB serial chips commonly found on Marlin control boards. These only rank the
# probe order — a match is a hint, never proof, since the same chips appear in
# plenty of unrelated USB-serial adapters.
_KNOWN_CHIPS: Dict[tuple, str] = {
    (0x1A86, 0x7523): "CH340",       # Creality 4.2.x, Melzi, most Ender clones
    (0x1A86, 0x7522): "CH340K",
    (0x1A86, 0x5523): "CH341",
    (0x1A86, 0x55D4): "CH9102",      # newer Creality / BTT boards
    (0x0483, 0x5740): "STM32 CDC",   # BTT SKR, Creality 4.2.7 native USB
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FT232",
    (0x0403, 0x6015): "FT231X",
    (0x067B, 0x2303): "PL2303",
    (0x2341, 0x0010): "Arduino Mega 2560",
    (0x2A03, 0x0010): "Arduino Mega 2560 (clone)",
}

# Vendors worth trying even when the exact product ID is unfamiliar
_KNOWN_VENDORS: Dict[int, str] = {
    0x1A86: "WCH", 0x0483: "STMicroelectronics", 0x10C4: "Silicon Labs",
    0x0403: "FTDI", 0x067B: "Prolific", 0x2341: "Arduino", 0x2A03: "Arduino",
}

# Opening these on Windows can block for many seconds and they are never a
# printer, so they are skipped outright rather than merely ranked low.
_SKIP_KEYWORDS = ("bluetooth", "wireless", "modem", "printer port", "lpt")


def score_port(vid: Optional[int], pid: Optional[int],
               description: str = "", hwid: str = "") -> int:
    """
    Rank how printer-like a serial port looks, without opening it.

      3  exact VID:PID of a known Marlin-board serial chip
      2  known vendor, unfamiliar product
      1  some other real USB device
      0  no USB identity (motherboard COM header, virtual port)
     -1  known-bad (Bluetooth &c.) — never probe

    Pure function: kept free of pyserial so it can be tested anywhere.
    """
    haystack = f"{description} {hwid}".lower()
    if any(word in haystack for word in _SKIP_KEYWORDS):
        return -1
    if vid is None:
        return 0
    if (vid, pid) in _KNOWN_CHIPS:
        return 3
    if vid in _KNOWN_VENDORS:
        return 2
    return 1


def port_candidates() -> List[dict]:
    """
    Enumerate serial ports, best-guess first. Never opens anything.

    Each entry: {device, description, manufacturer, vid, pid, chip, score}.
    Ports scoring -1 are excluded.
    """
    try:
        from serial.tools import list_ports
    except Exception:
        return []

    out = []
    for p in list_ports.comports():
        score = score_port(p.vid, p.pid, p.description or "", p.hwid or "")
        if score < 0:
            continue
        out.append({
            "device":       p.device,
            "description":  p.description or "",
            "manufacturer": p.manufacturer or "",
            "vid":          p.vid,
            "pid":          p.pid,
            "chip":         _KNOWN_CHIPS.get((p.vid, p.pid), ""),
            "score":        score,
        })
    # Stable: score descending, then device name for a predictable order
    out.sort(key=lambda c: (-c["score"], c["device"]))
    return out


def probe_port(device: str, baud: int = 115200, timeout: float = 4.0) -> Optional[str]:
    """
    Ask a port whether it is a Marlin printer, via M115 (report firmware).
    Returns the identifying string on success, None otherwise.

    Opening a serial port toggles DTR, which **reboots most Marlin boards** —
    so this waits for the firmware to come back up, and callers must never probe
    while a print is running.
    """
    try:
        import serial
    except Exception:
        return None

    try:
        with serial.Serial(device, baud, timeout=1.0, write_timeout=2.0) as ser:
            time.sleep(2.0)             # Marlin reboots on connect
            ser.reset_input_buffer()
            ser.write(b"\nM115\n")
            ser.flush()

            saw_ok = False
            deadline = time.time() + timeout
            while time.time() < deadline:
                resp = ser.readline().decode("utf-8", errors="ignore").strip()
                if not resp:
                    continue
                upper = resp.upper()
                if "FIRMWARE_NAME" in upper:
                    return resp[:200]
                if "MARLIN" in upper:
                    return resp[:200]
                if upper.startswith("OK"):
                    saw_ok = True

            # Answered G-code but withheld a banner — still very likely a printer
            return "responded to M115 (no firmware banner)" if saw_ok else None
    except Exception:
        return None


def autodetect_port(baud: int = 115200, probe: bool = True,
                    max_probes: int = 6) -> dict:
    """
    Find the printer's serial port.

    probe=False  → rank by USB identity only. Instant and side-effect free, but
                   a guess: it cannot tell a printer from any other CH340 device.
    probe=True   → additionally handshake each candidate with M115, best-guess
                   first, and return the first that answers. Definitive, but
                   costs ~3-6s per port tried and resets the boards it touches.

    Returns {"port": str|None, "method": str, "firmware": str|None,
             "candidates": [...]}.
    """
    candidates = port_candidates()
    if not candidates:
        return {"port": None, "method": "none", "firmware": None, "candidates": []}

    if not probe:
        return {"port": candidates[0]["device"], "method": "usb-id",
                "firmware": None, "candidates": candidates}

    for cand in candidates[:max_probes]:
        firmware = probe_port(cand["device"], baud)
        if firmware:
            return {"port": cand["device"], "method": "handshake",
                    "firmware": firmware, "candidates": candidates}

    # Nothing answered — fall back to the best USB-identity guess
    return {"port": None, "method": "no-response", "firmware": None,
            "candidates": candidates}


class Printer:
    def __init__(self, port: str, baud: int = 115200):
        import serial
        self._serial_mod = serial
        self.port = port
        self.baud = baud
        self.ser = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        self.ser = self._serial_mod.Serial(self.port, self.baud, timeout=10)
        time.sleep(2)           # wait for Marlin to reset and init
        self.ser.flushInput()   # discard startup echo

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def ensure_connected(self):
        """Reconnect if the serial port has dropped."""
        if not self.is_connected:
            self._connect()

    def send_line(self, line: str):
        """Send one G-code line, wait for Marlin 'ok' acknowledgment."""
        line = line.split(';')[0].strip()  # strip inline comments
        if not line:
            return

        self.ensure_connected()

        with self._lock:
            self.ser.write((line + '\n').encode('utf-8'))

            while True:
                resp = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if not resp:
                    continue
                if resp.lower().startswith('ok'):
                    return
                if resp.lower().startswith('wait'):
                    # Printer buffer full — pause briefly and resend
                    time.sleep(0.1)
                    self.ser.write((line + '\n').encode('utf-8'))
                elif resp.lower().startswith('error'):
                    raise RuntimeError(f"Printer error: {resp}")
                # Ignore temperature reports, echo lines, etc.

    def send_gcode(
        self,
        lines: List[str],
        progress_callback: Optional[Callable[[int, int], None]] = None
    ):
        """Stream all G-code lines with optional progress reporting."""
        executable = [l for l in lines if l.split(';')[0].strip()]
        total = len(executable)
        sent = 0

        for line in lines:
            stripped = line.split(';')[0].strip()
            if not stripped:
                continue
            self.send_line(stripped)
            sent += 1
            if progress_callback:
                progress_callback(sent, total)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def list_ports() -> List[str]:
    """Return a list of available serial port names."""
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []
