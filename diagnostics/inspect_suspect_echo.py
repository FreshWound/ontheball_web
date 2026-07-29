#!/usr/bin/env python3
"""
inspect_suspect_echo.py — one-off diagnostic to characterize the suspicious
echo band north of KTBW (Tampa) that shows on ontheball but NOT on NWS's own
radar.weather.gov rendering of the same station.

Run this from the same venv as the main app:
    python3 diagnostics/inspect_suspect_echo.py [STATION] [lat_min] [lat_max] [lon_min] [lon_max]

Defaults to the KTBW / Ocala-Cedar Key box if no args given.

This does NOT touch the main app or change any behavior — it's purely for
looking at what's actually in the raw Level II data for the suspect area,
so we fix the right thing instead of guessing again.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `import radar_source` works regardless of cwd

import numpy as np
import pyart

import radar_source as rs


def main():
    station = sys.argv[1] if len(sys.argv) > 1 else "KTBW"
    lat_min = float(sys.argv[2]) if len(sys.argv) > 2 else 28.9
    lat_max = float(sys.argv[3]) if len(sys.argv) > 3 else 29.4
    lon_min = float(sys.argv[4]) if len(sys.argv) > 4 else -83.2
    lon_max = float(sys.argv[5]) if len(sys.argv) > 5 else -81.9

    print(f"Fetching latest volume for {station}...")
    key = rs._latest_key(station)
    if key is None:
        print("No recent volume found — aborting.")
        return
    print(f"Volume: {key}")

    import tempfile
    s3 = rs._s3_client()
    with tempfile.NamedTemporaryFile(suffix=".ar2v") as tmp:
        s3.download_fileobj(rs.BUCKET, key, tmp)
        tmp.flush()
        radar = pyart.io.read_nexrad_archive(tmp.name)

    print("\n--- Volume metadata ---")
    print("Instrument:", radar.metadata.get("instrument_name"))
    print("VCP:", radar.metadata.get("vcp_pattern", "not exposed by pyart metadata"))
    print("Number of sweeps:", radar.nsweeps)
    print("Fields present:", list(radar.fields.keys()))

    # Nyquist velocity / unambiguous range, per sweep, if available
    if "nyquist_velocity" in radar.instrument_parameters:
        nyq = radar.instrument_parameters["nyquist_velocity"]["data"]
        print("\nNyquist velocity by ray (first few, m/s):", nyq[:5], "...")
        print("Unique nyquist values across volume:", np.unique(np.round(nyq, 1)))
    if "unambiguous_range" in radar.instrument_parameters:
        uran = radar.instrument_parameters["unambiguous_range"]["data"]
        print("Unambiguous range by ray (first few, km):", uran[:5] / 1000.0, "...")
        print("Unique unambiguous ranges across volume (km):", np.unique(np.round(uran / 1000.0, 1)))

    # Compute lat/lon for every gate in the LOWEST sweep, then look at
    # whatever fields are available specifically inside the suspect box.
    sweep0 = radar.get_slice(0)
    lats, lons, _ = radar.get_gate_lat_lon_alt(0) if hasattr(radar, "get_gate_lat_lon_alt") else (None, None, None)
    if lats is None:
        # older/newer pyart API fallback
        gate_lat, gate_lon, gate_alt = radar.gate_latitude, radar.gate_longitude, radar.gate_altitude
        lats = gate_lat["data"][sweep0]
        lons = gate_lon["data"][sweep0]

    box_mask = (lats >= lat_min) & (lats <= lat_max) & (lons >= lon_min) & (lons <= lon_max)
    n_gates = box_mask.sum()
    print(f"\n--- Suspect box: lat [{lat_min},{lat_max}] lon [{lon_min},{lon_max}] ---")
    print(f"Gates in lowest sweep falling in this box: {n_gates}")

    if n_gates == 0:
        print("No gates in this box at the lowest sweep — try widening it or check the station's actual sweep 0 coverage.")
        return

    def summarize(field_name):
        if field_name not in radar.fields:
            print(f"  {field_name}: not present in this volume")
            return
        data_full = radar.fields[field_name]["data"][sweep0]
        vals = data_full[box_mask]
        valid = vals.compressed() if np.ma.is_masked(vals) else vals[~np.isnan(vals)]
        if len(valid) == 0:
            print(f"  {field_name}: no valid (unmasked) gates in box")
            return
        print(f"  {field_name}: n={len(valid)}  min={valid.min():.2f}  max={valid.max():.2f}  "
              f"mean={valid.mean():.2f}  std={valid.std():.2f}")

    print("\nField stats within the suspect box (lowest sweep only):")
    for f in ("reflectivity", "velocity", "spectrum_width", "cross_correlation_ratio", "differential_reflectivity"):
        summarize(f)

    print("""
--- How to read this ---
- High std/erratic velocity or spectrum_width alongside seemingly-normal
  reflectivity is a classic signature of range-folded (second-trip) echo:
  the reflectivity value is "real" (it's genuine energy, just mislocated
  in range), but velocity/spectrum_width from a folded pulse volume tend to
  be garbage/inconsistent because they're mixing two different range gates'
  worth of returns.
- If correlation_coefficient is unusually low/erratic in the box too, that
  further supports non-meteorological/range-ambiguous contamination rather
  than real precipitation.
- If everything looks clean and physically consistent (velocity coherent,
  CC near 1.0), this may genuinely be real echo that NWS's rendering is
  filtering out for a different reason — in which case we should look at
  what makes NWS's official rendering different, not assume our decode is
  wrong.
""")


if __name__ == "__main__":
    main()
