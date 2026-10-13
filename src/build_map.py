"""District polygons for the nine-state cluster, from the India district GeoJSON.

Keyed on (state, district) to match config.DISTRICTS. Produces the web map layer
AND the point-in-polygon lookup used to place EMPRES-i animal outbreaks, which
arrive as coordinates rather than district names.

    python build_map.py <path to INDIA_DISTRICTS.geojson>
"""
import json
import sys
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from config import DISTRICTS, STATES

OUT = Path(__file__).parent.parent / "data" / "interim" / "districts.geojson"

# The source file has mojibake in Himachal and Uttarakhand names, where long
# vowels were mangled. config.py was generated with the same fix applied.
FIXCHAR = str.maketrans({">": "A", "|": "I", "@": "U"})


def clean(name):
    return name.translate(FIXCHAR).title()


def build(src_path, simplify=0.004):
    raw = json.loads(Path(src_path).read_text())
    want = {s.upper() for s in STATES}

    groups = {}
    for f in raw["features"]:
        p = f["properties"]
        st = (p.get("state") or "").upper().strip()
        if st not in want:
            continue  # null-state features are disputed border slivers
        key = ("Delhi" if st == "DELHI" else clean(st), clean((p.get("district") or "").strip().upper()))
        groups.setdefault(key, []).append(shape(f["geometry"]))

    features = []
    for (state, district), geoms in sorted(groups.items()):
        merged = unary_union(geoms)
        if simplify:
            # ~400 m: invisible at district zoom, an order of magnitude smaller.
            merged = merged.simplify(simplify, preserve_topology=True)
        lat, lon = DISTRICTS[(state, district)]
        features.append({
            "type": "Feature",
            "properties": {"state": state, "district": district, "lat": lat, "lon": lon},
            "geometry": mapping(merged),
        })

    return {"type": "FeatureCollection", "features": features}


def district_for_point(fc, lat, lon):
    """(state, district) containing this point, or None if outside the cluster."""
    from shapely.geometry import Point

    p = Point(lon, lat)  # GeoJSON is (x=lon, y=lat) — easy to get backwards
    for f in fc["features"]:
        if shape(f["geometry"]).contains(p):
            return f["properties"]["state"], f["properties"]["district"]
    return None


def _self_check(fc):
    keys = {(f["properties"]["state"], f["properties"]["district"]) for f in fc["features"]}
    expected = set(DISTRICTS)
    assert keys == expected, (
        f"district set mismatch\n  missing: {sorted(expected - keys)}"
        f"\n  extra:   {sorted(keys - expected)}"
    )
    for f in fc["features"]:
        assert shape(f["geometry"]).is_valid, f"invalid geometry: {f['properties']}"

    # Jaipur city must resolve to Jaipur; the Arabian Sea must resolve to nothing.
    assert district_for_point(fc, 26.91, 75.79) == ("Rajasthan", "Jaipur"), "point lookup wrong"
    assert district_for_point(fc, 15.0, 68.0) is None, "point lookup matched outside the cluster"

    states = {f["properties"]["state"] for f in fc["features"]}
    print(f"OK  {len(fc['features'])} districts across {len(states)} states, geometries valid")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python build_map.py <INDIA_DISTRICTS.geojson>")
    fc = build(sys.argv[1])
    _self_check(fc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fc))
    print(f"wrote {OUT.name}  ({OUT.stat().st_size/1e6:.1f} MB)")
