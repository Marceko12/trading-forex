"""Small lifecycle-managed analysis worker. It has no order execution capability."""
from __future__ import annotations

from threading import Event, RLock, Thread, current_thread


class AnalysisRunner:
    def __init__(self, scan, interval_seconds: float = 5.0):
        self.scan = scan
        self.interval = interval_seconds
        self._lock = RLock()
        self._wake = Event()
        self._thread: Thread | None = None
        self._stop = False
        self._paused = False
        self._symbols: list[str] = []
        self._timeframe = "H1"
        self._last_scan = None
        self._last_error = None
        self._scans = 0

    def start(self, symbols: list[str], timeframe: str):
        with self._lock:
            self._symbols, self._timeframe = list(dict.fromkeys(symbols)), timeframe.upper()
            self._paused, self._stop, self._last_error = False, False, None
            if self._thread is None or not self._thread.is_alive():
                self._thread = Thread(target=self._run, name="market-analysis", daemon=True)
                self._thread.start()
            self._wake.set()
            return self.status()

    def pause(self, value: bool = True):
        with self._lock:
            self._paused = value
            if not value: self._wake.set()
            return self.status()

    def stop(self):
        with self._lock:
            self._stop, self._paused = True, False
            self._wake.set()
            thread = self._thread
        if thread and thread is not current_thread():
            thread.join(timeout=2)
        with self._lock:
            if thread is self._thread and (thread is None or not thread.is_alive()): self._thread = None
            return self.status()

    def status(self):
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive() and not self._stop)
            return {"running": alive, "paused": self._paused if alive else False,
                    "symbols": list(self._symbols), "timeframe": self._timeframe,
                    "last_scan": self._last_scan, "last_error": self._last_error, "scan_count": self._scans}

    def _run(self):
        while True:
            with self._lock:
                if self._stop: return
                paused, symbols, timeframe = self._paused, list(self._symbols), self._timeframe
            if not paused:
                try:
                    self.scan(symbols, timeframe)
                    with self._lock:
                        from datetime import datetime, timezone
                        self._last_scan, self._last_error = datetime.now(timezone.utc).isoformat(), None
                        self._scans += 1
                except Exception as exc:
                    with self._lock: self._last_error = str(exc)
            self._wake.wait(self.interval)
            self._wake.clear()
