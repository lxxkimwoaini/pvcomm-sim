"""Physical validity and grid limits are separate from communication labels."""

import math
from typing import Any


def classify_feeder(values: dict[str, float], *, tolerance_kw: float = 0.001) -> dict[str, Any]:
    valid = (
        all(math.isfinite(v) for v in values.values())
        and abs(values["balance_p_kw"]) <= tolerance_kw
        and abs(values["balance_q_kvar"]) <= tolerance_kw
    )
    violations = []
    if values["bus_min_pu"] < 0.9 or values["bus_max_pu"] > 1.1:
        violations.append("bus_voltage")
    if values["line_max_loading_percent"] > 100:
        violations.append("line_loading")
    if values["transformer_loading_percent"] > 100:
        violations.append("transformer_loading")
    return {
        "calculation_valid": valid,
        "electrical_violations": violations,
        "reverse_flow": values["grid_p_kw"] < 0,
        "communication_assessment": "not_inferred_from_electrical_state",
    }
