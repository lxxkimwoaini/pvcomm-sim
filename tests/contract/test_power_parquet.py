"""Small synthetic Arrow contract fixtures only, never experiment results."""

import json
from pathlib import Path

import pytest

from pvcomm.storage.power_parquet import (
    ELECTRICAL_FIELDS,
    FLOW_FIELDS,
    export_power_parquet,
    verify_power_parquet,
)


@pytest.mark.data
def test_power_arrow_roundtrip_and_source_integrity(tmp_path: Path) -> None:
    (tmp_path / "truth/weather").mkdir(parents=True)
    for name in ("weather.json", "source.json"):
        (tmp_path / "truth/weather" / name).write_text("{}")
    (tmp_path / "power_config.json").write_text("{}")
    for table, fields, source, event in (
        ("electrical_state", ELECTRICAL_FIELDS, "pvlib_pvwatts_inverter_v02", "electrical_state"),
        ("terminal_events", FLOW_FIELDS, "pandapower_three_site_v02", "terminal_powerflow_solved"),
    ):
        row = {
            key: {"float64": 1.0, "int64": 1, "string": "synthetic_test", "bool": True}[kind]
            for key, kind in fields.items()
        }
        row.update(source=source, event_type=event)
        (tmp_path / f"truth/{table}_test.jsonl").write_text(json.dumps(row) + "\n")
    result = export_power_parquet(tmp_path)
    assert result["scope"] == "Oracle"
    assert verify_power_parquet(tmp_path)
    with pytest.raises(FileExistsError):
        export_power_parquet(tmp_path)
    (tmp_path / "truth/weather/weather.json").write_text('{"changed":true}')
    assert not verify_power_parquet(tmp_path)
