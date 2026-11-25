"""Thread-safe endpoint event storage with strict observed/truth separation."""

import json
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from pvcomm.clock import ClockOrigin


class RunRecorder:
    def __init__(
        self,
        run_dir: Path,
        run_id: str,
        *,
        suffix: str = "",
        origin: ClockOrigin | None = None,
    ) -> None:
        self.origin = origin or ClockOrigin.capture()
        self.run_id = run_id
        self._endpoint = suffix or "local"
        self._lock = threading.Lock()
        self._index = 0
        self._closed = False
        self._streams: dict[str, TextIO] = {}
        for key, relative in {
            "master": f"observed/master_events{suffix}.jsonl",
            "terminal": f"truth/terminal_events{suffix}.jsonl",
            "electrical": f"truth/electrical_state{suffix}.jsonl",
            "master_raw": f"observed/master_apdu_events{suffix}.jsonl",
            "terminal_raw": f"truth/terminal_apdu_events{suffix}.jsonl",
        }.items():
            path = run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            self._streams[key] = path.open("x", encoding="utf-8")

    def clock_metadata(self) -> dict[str, int]:
        return asdict(self.origin)

    def _write(self, stream: str, payload: dict[str, object], *, observed: bool) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("event recorder already closed")
            self._index += 1
            mono = time.monotonic_ns()
            utc = time.time_ns()
            record = {
                **payload,
                "run_id": self.run_id,
                "event_id": f"{self._endpoint}-{stream}-{self._index:09d}",
                "ts_event": datetime.fromtimestamp(utc // 1_000_000_000, UTC).isoformat(),
                "ts_event_utc_ns": utc,
                "ts_mono_ns": mono,
                "sim_elapsed_ms": self.origin.elapsed_ms(mono),
            }
            if observed:
                record["available_at"] = utc
            self._streams[stream].write(json.dumps(record, allow_nan=False) + "\n")
            self._streams[stream].flush()

    def record_event(self, event_type: str, payload: dict[str, object]) -> None:
        if event_type.startswith("master_"):
            stream = "master"
        elif event_type == "electrical_state":
            stream = "electrical"
        elif event_type.startswith(("rtu_", "terminal_")):
            stream = "terminal"
        else:
            raise ValueError(f"unclassified event type: {event_type}")
        self._write(stream, {**payload, "event_type": event_type}, observed=stream == "master")

    def record_raw(self, direction: str, station_id: str, data: bytes) -> None:
        if direction.startswith("master_"):
            stream = "master_raw"
        elif direction.startswith("rtu_"):
            stream = "terminal_raw"
        else:
            raise ValueError(f"unclassified raw direction: {direction}")
        self._write(
            stream,
            {
                "station_id": station_id,
                "direction": direction,
                "data_hex": data.hex(),
                "length": len(data),
            },
            observed=stream == "master_raw",
        )

    def close(self) -> None:
        with self._lock:
            for stream in self._streams.values():
                stream.close()
            self._closed = True
