"""Real buffered-pcap shutdown regression in an owned isolated network."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pvcomm.capture.tcpdump import TcpdumpCapture
from pvcomm.network.isolated import IsolatedNetwork
from pvcomm.qc.pcap import read_apdus


@pytest.mark.lab
def test_buffered_capture_keeps_final_packets_without_tail_sleep(tmp_path: Path) -> None:
    if sys.platform != "linux" or os.environ.get("PVCOMM_ALLOW_LAB") != "1":
        pytest.skip("requires explicitly authorized Linux namespaces")
    before = subprocess.check_output(["ip", "netns", "list"], text=True)
    network = IsolatedNetwork()
    network.create()
    try:
        # ICMP is test traffic only, not a simulated IEC 104 experiment.
        for attempt in range(3):
            capture = TcpdumpCapture(
                "p1",
                tmp_path / f"tail-{attempt}.pcap",
                "icmp",
                command_prefix=network.prefix(network.rtus[0]),
                native_spool=True,
                immediate_mode=False,
                drain_on_stop=True,
            )
            capture.start()
            try:
                subprocess.run(
                    [*network.prefix(network.master), "ping", "-c", "1", "-W", "1", "10.203.1.2"],
                    check=True,
                    capture_output=True,
                    timeout=5,
                )
            finally:
                assert capture.stop() == 0
            assert capture.stats["drain_complete"] == 1
            assert capture.stats["packets_captured"] == 2
            assert capture.stats["packets_received_by_filter"] == 2
            assert capture.stats["packets_dropped_by_kernel"] == 0
            assert read_apdus(capture.output_path)["packet_count"] == 2
    finally:
        network.close()
    assert subprocess.check_output(["ip", "netns", "list"], text=True) == before
