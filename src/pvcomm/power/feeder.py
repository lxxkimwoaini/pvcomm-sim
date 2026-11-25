"""Three-site balanced quasi-static radial load flow, never a voltage fixture."""

import importlib
import math
from typing import Any

from pvcomm.config.power import FeederConfig


class ThreeSiteFeeder:
    def __init__(self, config: FeederConfig, stations: tuple[str, ...]) -> None:
        self.pp = importlib.import_module("pandapower")
        self.config = config
        self.net = self.pp.create_empty_network()
        hv = self.pp.create_bus(self.net, vn_kv=config.hv_kv)
        lv = self.pp.create_bus(self.net, vn_kv=config.lv_kv)
        self.pp.create_ext_grid(self.net, hv, vm_pu=1.0)
        self.pp.create_transformer_from_parameters(
            self.net,
            hv,
            lv,
            sn_mva=config.transformer_mva,
            vn_hv_kv=config.hv_kv,
            vn_lv_kv=config.lv_kv,
            vk_percent=config.transformer_vk_percent,
            vkr_percent=config.transformer_vkr_percent,
            pfe_kw=config.transformer_pfe_kw,
            i0_percent=config.transformer_i0_percent,
        )
        self.buses: dict[str, int] = {}
        self.generators: dict[str, int] = {}
        previous = lv
        for station in stations:
            bus = self.pp.create_bus(self.net, vn_kv=config.lv_kv, name=station)
            self.pp.create_line_from_parameters(
                self.net,
                previous,
                bus,
                length_km=config.line_length_km,
                r_ohm_per_km=config.line_r_ohm_km,
                x_ohm_per_km=config.line_x_ohm_km,
                c_nf_per_km=0.0,
                max_i_ka=config.line_max_i_ka,
            )
            self.pp.create_load(
                self.net,
                bus,
                p_mw=config.load_kw_per_bus / 1000,
                q_mvar=config.load_kvar_per_bus / 1000,
            )
            self.buses[station] = bus
            self.generators[station] = self.pp.create_sgen(self.net, bus, p_mw=0.0, q_mvar=0.0)
            previous = bus

    def solve(self, states: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
        if set(states) != set(self.buses):
            raise ValueError("feeder requires all three station states")
        for station, index in self.generators.items():
            p, q = states[station]["p_actual_kw"], states[station]["q_actual_kvar"]
            if not all(math.isfinite(v) for v in (p, q)):
                raise ValueError("nonfinite feeder injection")
            self.net.sgen.at[index, "p_mw"] = p / 1000
            self.net.sgen.at[index, "q_mvar"] = q / 1000
        self.pp.runpp(self.net, numba=False, calculate_voltage_angles=True)
        if not self.net.converged:
            raise RuntimeError("pandapower load flow did not converge")
        result = {}
        for station, bus in self.buses.items():
            voltage_pu = float(self.net.res_bus.at[bus, "vm_pu"])
            voltage = voltage_pu * self.config.lv_kv
            if not math.isfinite(voltage) or voltage <= 0:
                raise ValueError("invalid solved voltage")
            state = states[station]
            apparent = math.hypot(state["p_actual_kw"], state["q_actual_kvar"])
            result[station] = {
                "voltage_pu": voltage_pu,
                "voltage_kv": voltage,
                "current_a": apparent / (math.sqrt(3) * voltage),
            }
        return result
