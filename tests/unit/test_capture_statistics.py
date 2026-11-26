from pathlib import Path

from pvcomm.capture.tcpdump import TcpdumpCapture


def test_final_capture_statistics_replace_earlier_live_snapshot(tmp_path: Path) -> None:
    capture = TcpdumpCapture("p1", tmp_path / "test.pcap", "tcp")
    capture._lines = [
        "0 packets captured\n2 packets received by filter\n0 packets dropped by kernel\n",
        "2 packets captured\n2 packets received by filter\n0 packets dropped by kernel\n",
    ]
    assert capture._statistics() == {
        "packets_captured": 2,
        "packets_received_by_filter": 2,
        "packets_dropped_by_kernel": 0,
    }
