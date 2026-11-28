import threading
from pathlib import Path
from typing import IO, Any

import pytest

from pvcomm.scenario.lab_worker import publish_business_start


def test_business_ready_is_not_visible_while_its_value_is_being_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.open
    writing = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    class PausedWriter:
        def __init__(self, stream: IO[str]) -> None:
            self.stream = stream

        def __enter__(self) -> "PausedWriter":
            return self

        def write(self, value: str) -> None:
            self.stream.write(value[:1])
            self.stream.flush()
            writing.set()
            assert release.wait(5)
            self.stream.write(value[1:])

        def __exit__(self, *args: object) -> None:
            self.stream.close()

    def open_stage(path: Path, *args: Any, **kwargs: Any) -> Any:
        stream = original(path, *args, **kwargs)
        return PausedWriter(stream) if path.name.endswith(".tmp") else stream

    def publish() -> None:
        try:
            publish_business_start(tmp_path, 1234.56789)
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(Path, "open", open_stage)
    thread = threading.Thread(target=publish)
    thread.start()
    try:
        assert writing.wait(5)
        assert not (tmp_path / "master.ready").exists()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert float((tmp_path / "master.ready").read_text()) == 1234.56789
    assert not (tmp_path / "master.ready.tmp").exists()
