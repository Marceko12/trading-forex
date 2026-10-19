from threading import Event

from app.core.runner import AnalysisRunner


def test_runner_runs_pauses_resumes_and_stops_real_worker():
    scanned = Event()
    calls = []

    def scan(symbols, timeframe):
        calls.append((symbols, timeframe))
        scanned.set()

    runner = AnalysisRunner(scan, interval_seconds=0.02)
    assert runner.start(["EURUSD"], "M15")["running"]
    assert scanned.wait(1)
    assert calls[0] == (["EURUSD"], "M15")
    assert runner.pause(True)["paused"]
    assert runner.pause(False)["paused"] is False
    assert runner.stop()["running"] is False
