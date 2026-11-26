"""Owned L2 region aggregation and independent per-site access veth links."""

import json
import sys
from typing import Any

from pvcomm.config.faults import FaultScope, RegionScope, StationScope
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

    def interruption_ports(self, scope: FaultScope) -> tuple[tuple[str, str], ...]:
        """Resolve configuration identities, never arbitrary namespace/interface input."""
        if isinstance(scope, StationScope):
            return tuple(
                (self.regions[s.region_id], f"access{i}")
                for i, s in enumerate(self.config.protocol.stations, 1)
                if s.station_id in scope.station_ids
            )
        regions = (scope.region_id,) if isinstance(scope, RegionScope) else tuple(self.regions)
        return tuple((self.regions[r], "uplink") for r in regions)

    def bridge_port_evidence(self, namespace: str, interface: str) -> dict[str, Any]:
        self.prefix(namespace)
        allowed = {
            *((ns, "uplink") for ns in self.regions.values()),
            *(
                (self.regions[s.region_id], f"access{i}")
                for i, s in enumerate(self.config.protocol.stations, 1)
            ),
        }
        if (namespace, interface) not in allowed:
            raise ValueError("bridge port is not a configured owned laboratory link")
        rows = json.loads(self._run("-n", namespace, "-j", "-d", "link", "show", "dev", interface))
        if len(rows) != 1 or rows[0].get("master") != "aggregate":
            raise RuntimeError("laboratory bridge attachment disagrees with configuration")
        row = rows[0]
        return {
            "namespace": namespace,
            "interface": interface,
            "bridge": row["master"],
            "state": row["linkinfo"]["info_slave_data"]["state"],
            "flags": row["flags"],
        }

    def set_bridge_forwarding(
        self, namespace: str, interface: str, *, enabled: bool
    ) -> dict[str, Any]:
        self.bridge_port_evidence(namespace, interface)  # Validate before mutation.
        self._run(
            "netns",
            "exec",
            namespace,
            "bridge",
            "link",
            "set",
            "dev",
            interface,
            "state",
            "3" if enabled else "0",
        )
        actual = self.bridge_port_evidence(namespace, interface)
        if actual["state"] != ("forwarding" if enabled else "disabled"):
            raise RuntimeError("kernel bridge state disagrees with requested fault operation")
        return actual

    def set_site_state(self, station_id: str, up: bool) -> None:
        index = next(
            i for i, s in enumerate(self.config.protocol.stations) if s.station_id == station_id
        )
        namespace = self.rtus[index]
        if namespace not in self._owned:
            raise RuntimeError("site is not owned")
        self._run("-n", namespace, "link", "set", "p1", "up" if up else "down")

    def delay_ports(self, scope: FaultScope, direction: str) -> tuple[tuple[str, str], ...]:
        if direction not in ("master_to_rtu", "rtu_to_master"):
            raise ValueError("unknown delay direction")
        reverse = direction == "rtu_to_master"
        if isinstance(scope, StationScope):
            return tuple(
                (self.rtus[i - 1], "p1") if reverse else (self.regions[s.region_id], f"access{i}")
                for i, s in enumerate(self.config.protocol.stations, 1)
                if s.station_id in scope.station_ids
            )
        return tuple(
            (self.regions[r.region_id], "uplink") if reverse else (self.master, f"p0_{i}")
            for i, r in enumerate(self.config.regions, 1)
            if not isinstance(scope, RegionScope) or r.region_id == scope.region_id
        )

    def delay_evidence(self, namespace: str, interface: str) -> dict[str, Any]:
        self.prefix(namespace)
        owned = [(n, d) for n, d, _, _ in self.shaped]
        if (namespace, interface) not in owned:
            raise ValueError("delay requires a configured owned NetEm egress")
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
        if len(actual) != 1 or actual[0]["kind"] != "netem":
            raise RuntimeError("expected one root NetEm qdisc")
        return {"namespace": namespace, "interface": interface, "actual": actual[0]}

    def change_delay(self, namespace: str, interface: str, additional_ms: int) -> dict[str, Any]:
        if type(additional_ms) is not int or additional_ms not in (0, 200, 1000, 3000):
            raise ValueError("unsupported A02 additional delay")
        before = self.delay_evidence(namespace, interface)
        _, _, link, reverse = next(r for r in self.shaped if r[:2] == (namespace, interface))
        options = before["actual"]["options"]
        if (
            options["limit"] != 10000
            or options["seed"] != link.seed + int(reverse)
            or abs(options["delay"]["jitter"] * 1000 - link.jitter_ms) > 1e-6
        ):
            raise RuntimeError("NetEm baseline parameters changed unexpectedly")
        # change preserves the qdisc and queued packets; never replace/delete it.
        self._run(
            "netns",
            "exec",
            namespace,
            "tc",
            "qdisc",
            "change",
            "dev",
            interface,
            "root",
            "netem",
            "limit",
            str(options["limit"]),
            "delay",
            f"{link.delay_ms + additional_ms}ms",
            f"{link.jitter_ms}ms",
            "seed",
            str(options["seed"]),
        )
        after = self.delay_evidence(namespace, interface)
        expected = {
            **options,
            "delay": {
                **options["delay"],
                "delay": (link.delay_ms + additional_ms) / 1000,
            },
        }
        if after["actual"]["options"] != expected or any(
            after["actual"].get(k) != before["actual"].get(k) for k in ("handle", "parent", "root")
        ):
            raise RuntimeError(
                f"kernel NetEm readback mismatch: expected={expected}, actual={after}"
            )
        return after
