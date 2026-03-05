import time
from typing import List, Callable, Optional


class Printer:
    def __init__(self, port: str, baud: int = 115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=10)
        time.sleep(2)           # wait for Marlin to reset and init
        self.ser.flushInput()   # discard startup echo

    def send_line(self, line: str):
        """Send one G-code line, wait for Marlin 'ok' acknowledgment."""
        line = line.split(';')[0].strip()  # strip inline comments
        if not line:
            return

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
        # Count only executable lines for progress tracking
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
