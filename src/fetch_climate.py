"""Fill the climate columns from NASA POWER.

Free, no API key, and built for long daily series at a point — unlike
Open-Meteo, whose daily quota rationed us to about 90 districts a day.

One request per district, cached to disk, so a re-run costs nothing and an
interrupted run resumes where it stopped.
"""
import json
import time
from pathlib import Path

import pandas as pd
import requests

from config import DISTRICTS, END_DATE, START_DATE

CACHE = Path(__file__).parent.parent / "data" / "raw" / "climate_power"
API = "https://power.larc.nasa.gov/api/temporal/daily/point"

# POWER's names -> ours. Keep the output columns identical to what join.py expects.
PARAMS = {
    "T2M_MAX": "temp_max",
    "T2M_MIN": "temp_min",
    "PRECTOTCORR": "rain_mm",
    "RH2M": "humidity",
}
FILL = -999.0  # POWER's missing-value sentinel


def fetch_district(state, name, lat, lon, start=START_DATE, end=END_DATE,
                   timeout=120, retries=4, backoff=20):
    """Daily climate for one district. Cached; returns the parsed JSON payload."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{state}_{name}.json".replace(" ", "_").replace("/", "-")
    if path.exists():
        return json.loads(path.read_text())

    params = {
        "parameters": ",".join(PARAMS),
        "community": "AG",
        "latitude": lat,
        "longitude": lon,
        "start": start.replace("-", ""),
        "end": end.replace("-", ""),
        "format": "JSON",
    }

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(API, params=params, timeout=timeout)
            r.raise_for_status()
            payload = r.json()
        except Exception:
            if attempt == retries:
                raise
            print(f"       retrying in {backoff * attempt}s ({attempt}/{retries})")
            time.sleep(backoff * attempt)
            continue

        if "properties" not in payload:
            raise ValueError(f"{name}: unexpected response: {str(payload)[:200]}")
        path.write_text(json.dumps(payload))
        return payload

    raise RuntimeError(f"{name}: exhausted retries")


def to_weekly(state, name, payload):
    """POWER daily records -> one row per ISO week, aggregated per variable."""
    par = payload["properties"]["parameter"]
    d = pd.DataFrame({ours: pd.Series(par[theirs]) for theirs, ours in PARAMS.items()})
    d = d.replace(FILL, pd.NA)

    d["date"] = pd.to_datetime(d.index, format="%Y%m%d")
    iso = d["date"].dt.isocalendar()
    d["iso_year"], d["iso_week"] = iso["year"].values, iso["week"].values

    out = d.groupby(["iso_year", "iso_week"]).agg(
        temp_max=("temp_max", "mean"),
        temp_min=("temp_min", "mean"),
        # Rainfall sums over the week; temperature averages. Summing temps is meaningless.
        rain_mm=("rain_mm", "sum"),
        humidity=("humidity", "mean"),
        days_observed=("date", "count"),
    ).reset_index()

    out.insert(0, "district", name)
    out.insert(0, "state", state)
    return out


def build(districts=None, pause=1.0):
    """All districts -> one weekly climate table. Partial failures are reported, not fatal."""
    districts = districts or DISTRICTS
    frames, failed = [], []

    for i, ((state, name), (lat, lon)) in enumerate(districts.items(), 1):
        try:
            frames.append(to_weekly(state, name, fetch_district(state, name, lat, lon)))
            print(f"  [{i:>3}/{len(districts)}] {state} / {name}")
        except Exception as e:
            failed.append((f"{state}/{name}", str(e)[:120]))
            print(f"  [{i:>3}/{len(districts)}] {state} / {name}  FAILED: {str(e)[:70]}")
        time.sleep(pause)

    if failed:
        print(f"\n{len(failed)} district(s) failed — re-run to retry, cached ones are skipped")
    if not frames:
        raise RuntimeError("no district succeeded; check network access")

    return pd.concat(frames, ignore_index=True)


def _self_check():
    """Exercises parsing and aggregation offline, so a network issue can't mask a logic bug."""
    days = pd.date_range("2024-01-01", periods=14, freq="D").strftime("%Y%m%d")
    fake = {"properties": {"parameter": {
        "T2M_MAX": {d: 30.0 for d in days},
        "T2M_MIN": {d: 18.0 for d in days},
        "PRECTOTCORR": {d: 2.0 for d in days},
        "RH2M": {d: 55.0 for d in days},
    }}}
    # One missing day, to prove the sentinel is dropped rather than averaged in.
    fake["properties"]["parameter"]["T2M_MAX"][days[0]] = FILL
    w = to_weekly("Teststate", "Testville", fake)

    assert len(w) == 2, f"14 days should fold into 2 ISO weeks, got {len(w)}"
    assert abs(w["rain_mm"].iloc[0] - 14.0) < 1e-9, "rain must sum, not average"
    assert abs(w["temp_max"].iloc[0] - 30.0) < 1e-9, "the -999 sentinel leaked into the mean"
    assert abs(w["temp_min"].iloc[0] - 18.0) < 1e-9, "temperature must average, not sum"
    assert list(w.columns)[:4] == ["state", "district", "iso_year", "iso_week"]
    print("OK  parsing and aggregation self-check passed")
    print(w.to_string(index=False))


if __name__ == "__main__":
    import sys

    _self_check()
    if "--self-check" not in sys.argv:
        print(f"\nfetching {len(DISTRICTS)} districts from NASA POWER...")
        df = build()
        df.to_csv("../data/interim/climate_weekly.csv", index=False)
        print(f"\nwrote data/interim/climate_weekly.csv  ({len(df):,} rows)")
