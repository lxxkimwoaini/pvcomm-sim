"""Owned L2 region aggregation and independent per-site access veth links."""

import sys

from pvcomm.config.multisite import V03Config
from pvcomm.network.isolated import IsolatedNetwork


class RegionalNetwork(IsolatedNetwork):
    def __init__(self, config: V03Config) -> None:
        super().__init__()
        self.config = config
        prefix = self.master.removesuffix("-master")
        self.rtus = tuple(f"{prefix}-rtu{i}" for i in range(1, len(config.devices) + 1))
        self.regions = {r.region_id: f"{prefix}-reg{i}" for i, r in enumerate(config.regions, 1)}
        self.addresses = tuple(s.address for s in config.protocol.stations)

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
