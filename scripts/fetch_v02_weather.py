"""Download the public historical development input; never commit external data."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

URL = (
    "https://power.larc.nasa.gov/api/temporal/hourly/point?"
    "parameters=ALLSKY_SFC_SW_DWN,ALLSKY_SFC_SW_DNI,ALLSKY_SFC_SW_DIFF,T2M,WS10M"
    "&community=RE&longitude=113.8&latitude=35.4&start=20240601&end=20240630"
    "&format=JSON&time-standard=UTC"
)
SOURCES = {
    "api": "https://power.larc.nasa.gov/docs/services/api/temporal/hourly/",
    "referencing": "https://power.larc.nasa.gov/docs/referencing/",
    "policy": (
        "https://www.earthdata.nasa.gov/engage/open-data-services-and-software/"
        "data-and-information-policy"
    ),
}


def main() -> None:
    root = Path("data/nasa-power-huixian-202406-month")
    root.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, url in {"raw.json": URL, **{f"{k}.html": v for k, v in SOURCES.items()}}.items():
        with urlopen(url, timeout=60) as response:
            data = response.read(10_000_001)
        if len(data) > 10_000_000:
            raise ValueError("unexpectedly large source response")
        (root / name).write_bytes(data)
        files[name] = {"url": url, "sha256": hashlib.sha256(data).hexdigest()}
    raw = json.loads((root / "raw.json").read_bytes())
    canonical = json.dumps(
        {k: v for k, v in raw.items() if k != "times"}, sort_keys=True, separators=(",", ":")
    ).encode()
    (root / "weather.json").write_bytes(canonical)
    manifest = {
        "source_id": "NASA_POWER_Hourly_huixian_20240601_30",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "usage_basis": "NASA full and open Earth science data; POWER acknowledgment required",
        "source_kind": "historical_satellite_reanalysis_not_field_measurement",
        "header": raw["header"],
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }
    (root / "source.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
