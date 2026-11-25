"""Owned L2 region aggregation and independent per-site access veth links."""

import json
import sys
from typing import Any

from pvcomm.config.multisite import NormalLink, V03Config
from pvcomm.network.isolated import IsolatedNetwork


class RegionalNetwork(IsolatedNetwork):
    def __init__(self, config: V03Config) -> None:
        super().__init__()
        self.config = config
        prefix = self.master.removesuffix("-master")
        self.rtus = tuple(f"{prefix}-rtu{i}" for i in range(1, len(config.devices) + 1))
        self.regions = {r.region_id: f"{prefix}-reg{i}" for i, r in enumerate(config.regions, 1)}
        self.addresses = tuple(s.address for s in config.protocol.stations)
        self.shaped: list[tuple[str, str, NormalLink, bool]] = []

    def _shape(
        self, namespace: str, interface: str, link: NormalLink | None, reverse: bool
    ) -> None:
        if link is None:
            return
        self.prefix(namespace)  # Ownership check before any resource operation.
        self._run(
            "netns",
            "exec",
            namespace,
            "tc",
            "qdisc",
            "add",
            "dev",
            interface,
            "root",
            "netem",
            "limit",
            "10000",
            "delay",
            f"{link.delay_ms}ms",
            f"{link.jitter_ms}ms",
            "seed",
            str(link.seed + int(reverse)),
        )
        self.shaped.append((namespace, interface, link, reverse))

    def shaping_evidence(self) -> list[dict[str, Any]]:
        rows = []
        for namespace, interface, link, reverse in self.shaped:
            self.prefix(namespace)
            actual = json.loads(
                self._run(
                    "netns",
                    "exec",
                    namespace,
                    "tc",
                    "-j",
                    "-s",
                    "-d",
                    "qdisc",
                    "show",
                    "dev",
                    interface,
                )
            )
            netem = next(r for r in actual if r["kind"] == "netem")
            options = netem["options"]
            if (
                abs(options["delay"]["delay"] * 1000 - link.delay_ms) > 1e-6
                or abs(options["delay"]["jitter"] * 1000 - link.jitter_ms) > 1e-6
                or options["seed"] != link.seed + int(reverse)
            ):
                raise RuntimeError("kernel NetEm parameters disagree with configuration")
            rows.append(
                {
                    "namespace": namespace,
                    "interface": interface,
                    "configured": link.model_dump(),
                    "reverse": reverse,
                    "actual": netem,
                }
            )
        return rows

    def create(self) -> None:
        if sys.platform != "linux" or self._owned:
            raise RuntimeError("regional network requires a fresh owned Linux lab")
        try:
            for namespace in (self.master, *self.regions.values(), *self.rtus):
                self._run("netns", "add", namespace)
                self._owned.append(namespace)
                self._run("-n", namespace, "link", "set", "lo", "up")
            for i, region in enumerate(self.config.regions, 1):
                namespace = self.regions[region.region_id]
                self._run(
                    "-n", namespace, "link", "add", "aggregate", "type", "bridge", "stp_state", "0"
                )
                self._run("-n", namespace, "link", "set", "aggregate", "up")
                self._run(
                    "-n",
                    self.master,
                    "link",
                    "add",
                    f"p0_{i}",
                    "type",
                    "veth",
                    "peer",
                    "name",
                    "uplink",
                    "netns",
                    namespace,
                )
                self._run("-n", self.master, "addr", "add", f"10.204.{i}.1/24", "dev", f"p0_{i}")
                self._run("-n", self.master, "link", "set", f"p0_{i}", "up")
                self._run("-n", namespace, "link", "set", "uplink", "master", "aggregate")
                self._run("-n", namespace, "link", "set", "uplink", "up")
                self._shape(self.master, f"p0_{i}", region.uplink, False)
                self._shape(namespace, "uplink", region.uplink, True)
            for i, (station, namespace) in enumerate(
                zip(self.config.protocol.stations, self.rtus, strict=True), 1
            ):
                region_ns = self.regions[station.region_id]
                self._run(
                    "-n",
                    region_ns,
                    "link",
                    "add",
                    f"access{i}",
                    "type",
                    "veth",
                    "peer",
                    "name",
                    "p1",
                    "netns",
                    namespace,
                )
                self._run("-n", region_ns, "link", "set", f"access{i}", "master", "aggregate")
                self._run("-n", region_ns, "link", "set", f"access{i}", "up")
                self._run("-n", namespace, "addr", "add", f"{station.address}/24", "dev", "p1")
                self._run("-n", namespace, "link", "set", "p1", "up")
                self._shape(region_ns, f"access{i}", station.access_link, False)
                self._shape(namespace, "p1", station.access_link, True)
        except BaseException:
            self.close()
            raise

    def set_region_state(self, region_id: str, up: bool) -> None:
        namespace = self.regions[region_id]
        if namespace not in self._owned:
            raise RuntimeError("region is not owned")
        self._run("-n", namespace, "link", "set", "uplink", "up" if up else "down")

    def set_site_state(self, station_id: str, up: bool) -> None:
        index = next(
            i for i, s in enumerate(self.config.protocol.stations) if s.station_id == station_id
        )
        namespace = self.rtus[index]
        if namespace not in self._owned:
            raise RuntimeError("site is not owned")
        self._run("-n", namespace, "link", "set", "p1", "up" if up else "down")
