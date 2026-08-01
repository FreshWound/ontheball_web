"""
radar_source.py — fetch a NEXRAD Level II volume, decode + clutter-filter it
with Py-ART, grid it to a lat/lon raster, and render transparent PNGs
suitable for use as MapLibre GL 'image' source overlays.

Renders reflectivity, base velocity, and correlation coefficient from a
single grid pass so switching products in the UI doesn't require a new
S3 fetch/decode — only re-selecting which already-rendered PNG to show.
"""

from __future__ import annotations

import base64
import io
import json
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

import numpy as np

try:
    from scipy.ndimage import gaussian_filter
except ImportError:
    gaussian_filter = None

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors

import pyart
import boto3
from botocore import UNSIGNED
from botocore.config import Config
from pyproj import Transformer

BUCKET = "unidata-nexrad-level2"

# Full NWS NEXRAD WSR-88D Network (US, Alaska, Hawaii, US Territories)
STATIONS = {
    # --- CONUS ---
    "KABR": {"name": "Aberdeen, SD", "lat": 45.4558, "lon": -98.4131},
    "KABX": {"name": "Albuquerque, NM", "lat": 35.1497, "lon": -106.8239},
    "KAKQ": {"name": "Wakefield, VA", "lat": 36.9839, "lon": -77.0075},
    "KAMA": {"name": "Amarillo, TX", "lat": 35.2334, "lon": -101.7092},
    "KAMX": {"name": "Miami, FL", "lat": 25.6111, "lon": -80.4128},
    "KAPX": {"name": "Gaylord, MI", "lat": 44.9075, "lon": -84.7197},
    "KARX": {"name": "La Crosse, WI", "lat": 43.8228, "lon": -91.1911},
    "KATX": {"name": "Seattle/Tacoma, WA", "lat": 48.1947, "lon": -122.4958},
    "KBBX": {"name": "Beale AFB, CA", "lat": 39.4961, "lon": -121.6317},
    "KBGM": {"name": "Binghamton, NY", "lat": 42.1997, "lon": -75.9847},
    "KBHX": {"name": "Eureka, CA", "lat": 40.4983, "lon": -124.2919},
    "KBIS": {"name": "Bismarck, ND", "lat": 46.7708, "lon": -100.7603},
    "KBLX": {"name": "Billings, MT", "lat": 45.8539, "lon": -108.6067},
    "KBMX": {"name": "Birmingham, AL", "lat": 33.1722, "lon": -86.7697},
    "KBOX": {"name": "Boston, MA", "lat": 41.9558, "lon": -71.1369},
    "KBRO": {"name": "Brownsville, TX", "lat": 25.9161, "lon": -97.4189},
    "KBUF": {"name": "Buffalo, NY", "lat": 42.9489, "lon": -78.7367},
    "KBYX": {"name": "Key West, FL", "lat": 24.5975, "lon": -81.7031},
    "KCAE": {"name": "Columbia, SC", "lat": 33.9486, "lon": -81.1186},
    "KCBW": {"name": "Houlton, ME", "lat": 46.0392, "lon": -67.8064},
    "KCBX": {"name": "Boise, ID", "lat": 43.4900, "lon": -116.2361},
    "KCCX": {"name": "State College, PA", "lat": 40.9228, "lon": -77.0075},
    "KCLE": {"name": "Cleveland, OH", "lat": 41.4131, "lon": -81.8597},
    "KCLX": {"name": "Charleston, SC", "lat": 32.6556, "lon": -81.0422},
    "KCRP": {"name": "Corpus Christi, TX", "lat": 27.7842, "lon": -97.5111},
    "KCXX": {"name": "Burlington, VT", "lat": 44.5111, "lon": -73.1664},
    "KCYS": {"name": "Cheyenne, WY", "lat": 41.1519, "lon": -104.8061},
    "KDAX": {"name": "Sacramento, CA", "lat": 38.5011, "lon": -121.6778},
    "KDDC": {"name": "Dodge City, KS", "lat": 37.7609, "lon": -99.9686},
    "KDFX": {"name": "Laughlin AFB, TX", "lat": 29.2728, "lon": -100.2808},
    "KDGX": {"name": "Jackson, MS", "lat": 32.3178, "lon": -90.0797},
    "KDIX": {"name": "Philadelphia, PA", "lat": 39.9469, "lon": -74.4108},
    "KDLH": {"name": "Duluth, MN", "lat": 46.8369, "lon": -92.2097},
    "KDMX": {"name": "Des Moines, IA", "lat": 41.7311, "lon": -93.7228},
    "KDOX": {"name": "Dover AFB, DE", "lat": 38.8256, "lon": -75.4400},
    "KDTX": {"name": "Detroit/White Lake, MI", "lat": 42.6999, "lon": -83.4717},
    "KDVN": {"name": "Quad Cities, IA", "lat": 41.6115, "lon": -90.5808},
    "KDYX": {"name": "Dyess AFB, TX", "lat": 32.5383, "lon": -99.2542},
    "KEAX": {"name": "Kansas City, MO", "lat": 38.8103, "lon": -94.2644},
    "KEMX": {"name": "Tucson, AZ", "lat": 31.8936, "lon": -110.6303},
    "KENX": {"name": "Albany, NY", "lat": 42.5864, "lon": -74.0639},
    "KEOX": {"name": "Fort Rucker, AL", "lat": 31.4608, "lon": -85.4592},
    "KEPZ": {"name": "El Paso, TX", "lat": 31.8731, "lon": -106.6981},
    "KESX": {"name": "Las Vegas, NV", "lat": 35.7011, "lon": -114.8919},
    "KEVX": {"name": "Eglin AFB, FL", "lat": 30.5644, "lon": -85.9214},
    "KEWX": {"name": "Austin/San Antonio, TX", "lat": 29.7039, "lon": -98.0286},
    "KEYX": {"name": "Edwards AFB, CA", "lat": 35.0978, "lon": -117.5608},
    "KFCX": {"name": "Roanoke, VA", "lat": 37.0242, "lon": -80.2742},
    "KFDR": {"name": "Altus AFB, OK", "lat": 34.3622, "lon": -98.9764},
    "KFDX": {"name": "Cannon AFB, NM", "lat": 34.6339, "lon": -103.6189},
    "KFFC": {"name": "Atlanta, GA", "lat": 33.3631, "lon": -84.5658},
    "KFSD": {"name": "Sioux Falls, SD", "lat": 43.5878, "lon": -96.7294},
    "KFSX": {"name": "Flagstaff, AZ", "lat": 34.5744, "lon": -111.1983},
    "KFTG": {"name": "Denver, CO", "lat": 39.7866, "lon": -104.5458},
    "KFWS": {"name": "Dallas/Fort Worth, TX", "lat": 32.5731, "lon": -97.3031},
    "KGGW": {"name": "Glasgow, MT", "lat": 48.2064, "lon": -106.6247},
    "KGJX": {"name": "Grand Junction, CO", "lat": 39.0622, "lon": -108.2139},
    "KGLD": {"name": "Goodland, KS", "lat": 39.3669, "lon": -101.7003},
    "KGRB": {"name": "Green Bay, WI", "lat": 44.4986, "lon": -88.1111},
    "KGRK": {"name": "Fort Hood, TX", "lat": 30.7219, "lon": -97.3831},
    "KGRR": {"name": "Grand Rapids, MI", "lat": 42.8939, "lon": -85.5450},
    "KGSP": {"name": "Greer, SC", "lat": 34.8833, "lon": -82.2197},
    "KGWX": {"name": "Columbus AFB, MS", "lat": 33.8969, "lon": -88.3292},
    "KGYX": {"name": "Portland, ME", "lat": 43.8914, "lon": -70.2564},
    "KHDX": {"name": "Holloman AFB, NM", "lat": 33.0764, "lon": -106.1200},
    "KHGX": {"name": "Houston/Galveston, TX", "lat": 29.4719, "lon": -95.0792},
    "KHNX": {"name": "San Joaquin Valley, CA", "lat": 36.3142, "lon": -119.6319},
    "KHPX": {"name": "Fort Campbell, KY", "lat": 36.7367, "lon": -87.4150},
    "KHTX": {"name": "Huntsville, AL", "lat": 34.9306, "lon": -86.0833},
    "KICT": {"name": "Wichita, KS", "lat": 37.6546, "lon": -97.4431},
    "KICX": {"name": "Cedar City, UT", "lat": 37.5908, "lon": -112.8622},
    "KILN": {"name": "Cincinnati, OH", "lat": 39.4203, "lon": -83.8217},
    "KILX": {"name": "Springfield, IL", "lat": 40.1506, "lon": -89.3369},
    "KIND": {"name": "Indianapolis, IN", "lat": 39.7075, "lon": -86.2803},
    "KINX": {"name": "Tulsa, OK", "lat": 36.1750, "lon": -95.5644},
    "KIWA": {"name": "Phoenix, AZ", "lat": 33.2892, "lon": -111.6697},
    "KIWX": {"name": "Fort Wayne, IN", "lat": 41.3586, "lon": -85.7000},
    "KJAX": {"name": "Jacksonville, FL", "lat": 30.4847, "lon": -81.7019},
    "KJGX": {"name": "Robins AFB, GA", "lat": 32.6750, "lon": -83.3511},
    "KJKL": {"name": "Jackson, KY", "lat": 37.5908, "lon": -83.3131},
    "KLBB": {"name": "Lubbock, TX", "lat": 33.6542, "lon": -101.8142},
    "KLCH": {"name": "Lake Charles, LA", "lat": 30.1253, "lon": -93.2161},
    "KLGX": {"name": "Langley Hill, WA", "lat": 47.1158, "lon": -124.1069},
    "KLIX": {"name": "New Orleans, LA", "lat": 30.3367, "lon": -89.8256},
    "KLNX": {"name": "North Platte, NE", "lat": 41.9589, "lon": -100.5761},
    "KLOT": {"name": "Chicago, IL", "lat": 41.6044, "lon": -88.0847},
    "KLRX": {"name": "Elko, NV", "lat": 40.7397, "lon": -116.8028},
    "KLSX": {"name": "St. Louis, MO", "lat": 38.6989, "lon": -90.6828},
    "KLTX": {"name": "Wilmington, NC", "lat": 33.9892, "lon": -78.4289},
    "KLVX": {"name": "Louisville, KY", "lat": 37.9753, "lon": -85.9439},
    "KLWX": {"name": "Sterling, VA", "lat": 38.9761, "lon": -77.4875},
    "KLZK": {"name": "Little Rock, AR", "lat": 34.8364, "lon": -92.2622},
    "KMAF": {"name": "Midland/Odessa, TX", "lat": 31.9433, "lon": -102.1889},
    "KMAX": {"name": "Medford, OR", "lat": 42.0811, "lon": -122.7172},
    "KMBX": {"name": "Minot AFB, ND", "lat": 48.3925, "lon": -100.8644},
    "KMHX": {"name": "Morehead City, NC", "lat": 34.7761, "lon": -76.8761},
    "KMKX": {"name": "Milwaukee, WI", "lat": 42.9678, "lon": -88.5506},
    "KMLB": {"name": "Melbourne, FL", "lat": 28.1131, "lon": -80.6540},
    "KMOB": {"name": "Mobile, AL", "lat": 30.6794, "lon": -88.2397},
    "KMXX": {"name": "Maxwell AFB, AL", "lat": 32.5367, "lon": -85.7897},
    "KMQT": {"name": "Marquette, MI", "lat": 46.5311, "lon": -87.5483},
    "KMRX": {"name": "Knoxville/Tri-Cities, TN", "lat": 36.1683, "lon": -83.4019},
    "KMSX": {"name": "Missoula, MT", "lat": 47.0411, "lon": -113.9861},
    "KMTX": {"name": "Salt Lake City, UT", "lat": 41.2628, "lon": -112.4475},
    "KMUX": {"name": "San Francisco, CA", "lat": 37.1553, "lon": -121.8983},
    "KMVX": {"name": "Grand Forks, ND", "lat": 47.5281, "lon": -97.3250},
    "KNKX": {"name": "San Diego, CA", "lat": 32.9189, "lon": -117.0419},
    "KNQA": {"name": "Memphis, TN", "lat": 35.3447, "lon": -89.8733},
    "KOAX": {"name": "Omaha/Valley, NE", "lat": 41.3202, "lon": -96.3667},
    "KOHX": {"name": "Nashville, TN", "lat": 36.2472, "lon": -86.5625},
    "KOKX": {"name": "New York City, NY", "lat": 40.8656, "lon": -72.8625},
    "KOTX": {"name": "Spokane, WA", "lat": 47.6806, "lon": -117.6264},
    "KPAH": {"name": "Paducah, KY", "lat": 37.0683, "lon": -88.7719},
    "KPBZ": {"name": "Pittsburgh, PA", "lat": 40.5317, "lon": -80.2183},
    "KPDT": {"name": "Pendleton, OR", "lat": 45.6906, "lon": -118.8528},
    "KPOE": {"name": "Fort Polk, LA", "lat": 31.1558, "lon": -92.9761},
    "KPUX": {"name": "Pueblo, CO", "lat": 38.4595, "lon": -104.1817},
    "KRAX": {"name": "Raleigh/Durham, NC", "lat": 35.6603, "lon": -78.4897},
    "KRGX": {"name": "Reno, NV", "lat": 39.7542, "lon": -119.4622},
    "KRIW": {"name": "Riverton, WY", "lat": 43.0661, "lon": -108.4772},
    "KRLX": {"name": "Charleston, WV", "lat": 38.3111, "lon": -81.6903},
    "KRTX": {"name": "Portland, OR", "lat": 45.7147, "lon": -122.9644},
    "KSFX": {"name": "Pocatello/Idaho Falls, ID", "lat": 43.1058, "lon": -112.6861},
    "KSGF": {"name": "Springfield, MO", "lat": 37.2353, "lon": -93.4003},
    "KSHV": {"name": "Shreveport, LA", "lat": 32.4508, "lon": -93.8414},
    "KSJT": {"name": "San Angelo, TX", "lat": 31.3711, "lon": -100.4925},
    "KSOX": {"name": "Santa Ana Mountains, CA", "lat": 33.8178, "lon": -117.6358},
    "KSRX": {"name": "Fort Smith, AR", "lat": 35.2906, "lon": -94.3617},
    "KTBW": {"name": "Tampa Bay, FL", "lat": 27.7056, "lon": -82.4019},
    "KTFX": {"name": "Great Falls, MT", "lat": 47.4597, "lon": -111.3853},
    "KTLH": {"name": "Tallahassee, FL", "lat": 30.3975, "lon": -84.3289},
    "KTLX": {"name": "Oklahoma City, OK", "lat": 35.3331, "lon": -97.2778},
    "KTWX": {"name": "Topeka, KS", "lat": 38.9969, "lon": -96.2325},
    "KTYX": {"name": "Fort Drum, NY", "lat": 43.7558, "lon": -75.7800},
    "KUDX": {"name": "Rapid City, SD", "lat": 44.1250, "lon": -102.8294},
    "KUEX": {"name": "Hastings, NE", "lat": 40.3208, "lon": -98.4419},
    "KVAX": {"name": "Moody AFB, GA", "lat": 30.8903, "lon": -83.0019},
    "KVBX": {"name": "Vandenberg AFB, CA", "lat": 34.8383, "lon": -120.3981},
    "KVWX": {"name": "Evansville, IN", "lat": 38.2603, "lon": -87.7247},
    "KYUX": {"name": "Yuma, AZ", "lat": 32.4953, "lon": -114.6561},

    # --- ALASKA ---
    "PABC": {"name": "Bethel, AK", "lat": 60.7919, "lon": -161.8764},
    "PACG": {"name": "Sitka/Biorka Island, AK", "lat": 56.8528, "lon": -135.5292},
    "PAEC": {"name": "Nomar/Nome, AK", "lat": 64.5114, "lon": -165.2950},
    "PAHG": {"name": "Anchorage/Kenai, AK", "lat": 60.7258, "lon": -151.3517},
    "PAIH": {"name": "Middleton Island, AK", "lat": 59.4614, "lon": -146.3031},
    "PAKC": {"name": "King Salmon, AK", "lat": 58.6794, "lon": -156.6294},
    "PAPD": {"name": "Fairbanks/Pedro Dome, AK", "lat": 65.0350, "lon": -147.5014},

    # --- HAWAII & PACIFIC ---
    "PGUA": {"name": "Andersen AFB, Guam", "lat": 13.4561, "lon": 144.8111},
    "PHKI": {"name": "Kauai, HI", "lat": 21.9597, "lon": -159.5622},
    "PHKM": {"name": "Kohala, HI", "lat": 20.1253, "lon": -155.7781},
    "PHMO": {"name": "Molokai, HI", "lat": 21.1328, "lon": -157.1800},
    "PHWA": {"name": "South Point, HI", "lat": 19.0922, "lon": -155.5689},

    # --- PUERTO RICO ---
    "TJUA": {"name": "San Juan, PR", "lat": 18.1156, "lon": -66.0781},
}


def find_closest_stations(lat: float, lon: float, n: int = 3) -> list:
    """Return the n station codes closest to the given lat/lon, nearest first.

    Used for the settable-home-location feature: rather than a fixed
    dual-radar preset, the user enters coordinates each session and this
    picks whichever stations are actually nearest to them.
    """
    distances = [
        (code, _haversine_km(lat, lon, meta["lat"], meta["lon"]))
        for code, meta in STATIONS.items()
    ]
    distances.sort(key=lambda pair: pair[1])
    return [code for code, _dist in distances[:n]]


REFLECTIVITY_FLOOR = 5.0       # dBZ, values below this are treated as clear air
REFLECTIVITY_MAX = 75.0        # dBZ, colormap ceiling
CORR_COEFF_MIN = 0.80          # threshold for dedicated correlation-coefficient overlay filtering
GRID_RANGE_M = 230_000.0       # +/- range from radar, matches typical NEXRAD reflectivity range
GRID_CELLS = 460               # ~1km pixels over 230km — finer than before, still light
SMOOTH_SIGMA = 0.0             # disabled smoothing to preserve intense core details


REFLECTIVITY_ANCHORS = [
    (REFLECTIVITY_FLOOR, "#6FE0E8"),   # faint drizzle - pale cyan
    (18, "#3FA0E0"),                   # light rain - blue
    (25, "#3FCF3F"),                   # light-moderate - green
    (35, "#F5E642"),                   # moderate - yellow
    (45, "#F5A623"),                   # heavy - orange
    (55, "#E0342A"),                   # very heavy - red
    (65, "#B02EA0"),                   # extreme - magenta
    (REFLECTIVITY_MAX, "#F5F5F5"),     # hail core - white
]

VELOCITY_MAX_MPH = 70.0        # display range in mph; ~ NEXRAD's typical Nyquist range
MPS_TO_MPH = 2.23694

VELOCITY_ANCHORS_MPH = [
    (-VELOCITY_MAX_MPH, "#3FF5F5"),        # strong inbound - cyan
    (-VELOCITY_MAX_MPH / 3, "#1E7A4A"),    # light inbound - green
    (0, "#C9C9B8"),                        # near zero - neutral gray
    (VELOCITY_MAX_MPH / 3, "#8A3A1E"),     # light outbound - brown-red
    (VELOCITY_MAX_MPH, "#F53F3F"),         # strong outbound - red
]

CORR_COEFF_ANCHORS = [
    (CORR_COEFF_MIN, "#3F1E5A"),
    (0.90, "#3F6FE0"),
    (0.95, "#3FCF9F"),
    (0.98, "#F5E642"),
    (1.02, "#F5F5F5"),
]

ZDR_MIN = -2.0         # dB, floor of the display range (light rain/drizzle can dip slightly negative)
ZDR_MAX = 6.0          # dB, ceiling — values above this usually mean large drops/hail and clip to the top color

ZDR_ANCHORS = [
    (ZDR_MIN, "#3F1E5A"),   # negative - purple (small/irregular scatterers, e.g. dry snow)
    (0.0, "#3FA0E0"),       # near zero - blue (light rain, small uniform drops)
    (1.0, "#3FCF3F"),       # green - moderate rain
    (2.0, "#F5E642"),       # yellow - heavier rain, larger drops
    (3.5, "#F5A623"),       # orange - big drops
    (ZDR_MAX, "#E0342A"),   # red - very large drops / possible hail
]


def _make_cmap(anchors, vmin: float, vmax: float):
    """Build a smooth colormap from (value, hex_color) anchor points."""
    positions = [(v - vmin) / (vmax - vmin) for v, _ in anchors]
    colors = [c for _, c in anchors]
    return mcolors.LinearSegmentedColormap.from_list("otb_cmap", list(zip(positions, colors)))


def _reflectivity_cmap():
    return _make_cmap(REFLECTIVITY_ANCHORS, REFLECTIVITY_FLOOR, REFLECTIVITY_MAX)


def _velocity_cmap():
    return _make_cmap(VELOCITY_ANCHORS_MPH, -VELOCITY_MAX_MPH, VELOCITY_MAX_MPH)


def _corr_coeff_cmap():
    return _make_cmap(CORR_COEFF_ANCHORS, CORR_COEFF_MIN, 1.02)


def _zdr_cmap():
    return _make_cmap(ZDR_ANCHORS, ZDR_MIN, ZDR_MAX)


PRODUCTS = {
    "reflectivity": dict(
        field="reflectivity", label="Reflectivity", unit="dBZ", scale=1.0,
        vmin=REFLECTIVITY_FLOOR, vmax=REFLECTIVITY_MAX,
        cmap=_reflectivity_cmap, transparent_below=REFLECTIVITY_FLOOR,
        legend_anchors=REFLECTIVITY_ANCHORS,
    ),
    "velocity": dict(
        field="velocity", label="Base Velocity", unit="mph", scale=MPS_TO_MPH,
        vmin=-VELOCITY_MAX_MPH, vmax=VELOCITY_MAX_MPH,
        cmap=_velocity_cmap, transparent_below=None,
        legend_anchors=VELOCITY_ANCHORS_MPH,
    ),
    "correlation_coefficient": dict(
        field="cross_correlation_ratio", label="Correlation Coefficient", unit="", scale=1.0,
        vmin=CORR_COEFF_MIN, vmax=1.02,
        cmap=_corr_coeff_cmap, transparent_below=None,
        legend_anchors=CORR_COEFF_ANCHORS,
    ),
    "differential_reflectivity": dict(
        field="differential_reflectivity", label="Differential Reflectivity (ZDR)", unit="dB", scale=1.0,
        vmin=ZDR_MIN, vmax=ZDR_MAX,
        cmap=_zdr_cmap, transparent_below=None,
        legend_anchors=ZDR_ANCHORS,
    ),
}


def get_legend(product_key: str) -> dict:
    cfg = PRODUCTS[product_key]
    vmin, vmax = cfg["vmin"], cfg["vmax"]
    stops = [
        {"pct": round((v - vmin) / (vmax - vmin) * 100, 2), "value": v, "color": c}
        for v, c in cfg["legend_anchors"]
    ]
    return {"label": cfg["label"], "unit": cfg["unit"], "vmin": vmin, "vmax": vmax, "stops": stops}


@dataclass
class RadarOverlay:
    products: dict             # product_key -> png_base64
    available_products: list
    product_notes: dict        # product_key -> human-readable reason it's unavailable/empty
    coordinates: list          # [[lon,lat] nw, ne, se, sw] for MapLibre image source
    station: str
    volume_time: str
    source: str                # "live" or "synthetic-demo"
    grid_fields: dict = None   # product_key -> raw masked ndarray (already in display units), for hover lookups
    origin_lat: float = None   # radar site lat, used to re-project cursor lat/lon back to a grid pixel
    origin_lon: float = None
    available_tilts: list = None   # [{"sweep": int, "angle": float}, ...] for this volume, low-to-high; None if unknown
    current_tilt_sweep: int = None  # which sweep index this overlay was actually gridded from (0 = default composite)


def sample_value(overlay: "RadarOverlay", product_key: str, lat: float, lon: float):
    """Look up the raw field value at a given lat/lon within this overlay's grid.

    Used to drive the legend hover-highlight: as the cursor moves over the map,
    the caller re-projects (lat, lon) back onto the same aeqd grid the radar
    was rendered on and reads the real value at that pixel — no separate
    lookup table, just the same data already used for the PNG.

    Returns None if the product wasn't rendered for this overlay, the point
    falls outside the grid extent, or that pixel has no valid return
    (masked/clear-air).
    """
    if not overlay.grid_fields or product_key not in overlay.grid_fields:
        return None
    if overlay.origin_lat is None or overlay.origin_lon is None:
        return None

    data = overlay.grid_fields[product_key]
    n = data.shape[0]  # square grid, GRID_CELLS x GRID_CELLS

    transformer = Transformer.from_crs(
        "EPSG:4326",
        {"proj": "aeqd", "lat_0": overlay.origin_lat, "lon_0": overlay.origin_lon, "datum": "WGS84"},
        always_xy=True,
    )
    x, y = transformer.transform(lon, lat)
    if abs(x) > GRID_RANGE_M or abs(y) > GRID_RANGE_M:
        return None

    # row 0 = south (matches the origin='lower' PNG export convention used elsewhere)
    col = int(round((x + GRID_RANGE_M) / (2 * GRID_RANGE_M) * (n - 1)))
    row = int(round((y + GRID_RANGE_M) / (2 * GRID_RANGE_M) * (n - 1)))
    col = max(0, min(n - 1, col))
    row = max(0, min(n - 1, row))

    val = data[row, col]
    if np.ma.is_masked(val):
        return None
    return float(val)


_S3_CLIENT = None

# Raw decoded Py-ART Radar objects, one per station — the full multi-sweep
# volume, kept around after the initial fetch so switching Tilt can just
# re-grid a single sweep from what's already in memory instead of hitting
# S3 again. Only the most recent volume per station is kept (not history),
# and only for stations that have actually been loaded — memory cost scales
# with however many distinct stations are on screen, not with time.
# Each entry: {"radar": Radar, "volume_time": str}
_RAW_RADAR_CACHE: dict = {}


def _s3_client():
    # Reused across calls (and across stations) so we're not paying a fresh
    # TCP/TLS handshake + connection-pool setup twice per station load
    # (once for the key listing, once for the download) — that overhead
    # showed up clearly once we added timing instrumentation.
    global _S3_CLIENT
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    return _S3_CLIENT


def _latest_key(station: str):
    s3 = _s3_client()
    now = datetime.now(timezone.utc)
    for day_offset in (0, 1):
        day = now if day_offset == 0 else now.fromtimestamp(now.timestamp() - 86400, tz=timezone.utc)
        prefix = f"{day.year:04d}/{day.month:02d}/{day.day:02d}/{station}/"
        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
        contents = resp.get("Contents", [])
        keys = sorted(o["Key"] for o in contents if not o["Key"].endswith("_MDM"))
        if keys:
            return keys[-1]
    return None


def _corners_latlon(origin_lat: float, origin_lon: float, half_extent_m: float):
    transformer = Transformer.from_crs(
        {"proj": "aeqd", "lat_0": origin_lat, "lon_0": origin_lon, "datum": "WGS84"},
        "EPSG:4326",
        always_xy=True,
    )
    e = half_extent_m
    corners_xy = [(-e, e), (e, e), (e, -e), (-e, -e)]  # nw, ne, se, sw
    return [list(transformer.transform(x, y)) for x, y in corners_xy]


def _smooth(field: np.ndarray) -> np.ndarray:
    if gaussian_filter is None or SMOOTH_SIGMA <= 0:
        return field
    mask = np.ma.getmaskarray(field) if np.ma.is_masked(field) else np.zeros(field.shape, dtype=bool)
    filled = np.ma.filled(field, 0.0)
    weight = (~mask).astype(float)
    smoothed_vals = gaussian_filter(filled * weight, sigma=SMOOTH_SIGMA)
    smoothed_weight = gaussian_filter(weight, sigma=SMOOTH_SIGMA)
    with np.errstate(invalid="ignore", divide="ignore"):
        result = smoothed_vals / smoothed_weight
    result = np.ma.masked_where(smoothed_weight < 0.3, result)
    return result


def _field_to_png_base64(field: np.ndarray, vmin: float, vmax: float, cmap, transparent_below) -> str:
    field = _smooth(field)
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = cmap(norm(field))

    masked = np.ma.getmaskarray(field) if np.ma.is_masked(field) else np.zeros(field.shape, dtype=bool)
    if transparent_below is not None:
        below = np.ma.filled(field, transparent_below - 1) < transparent_below
        hide = masked | below
    else:
        hide = masked
    rgba[..., 3] = np.where(hide, 0.0, 0.85)

    buf = io.BytesIO()
    matplotlib.image.imsave(buf, rgba, format="png", origin="lower")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _grid_and_render(radar, origin_lat: float, origin_lon: float):
    # Reflectivity, Velocity, and ZDR all share identical gate criteria (just
    # the reflectivity floor), so they're gridded together in a single
    # grid_from_radars() pass below — that's the expensive step, and doing
    # it once for all three instead of three times is the main win here.
    #
    # Correlation Coefficient additionally excludes its own low-quality gates
    # (ρHV < CORR_COEFF_MIN), and grid_from_radars applies one gatefilter to
    # every field in a given call — so if CC were merged into the same pass,
    # its extra exclusion would incorrectly blank out valid reflectivity/
    # velocity/ZDR data at gates where only CC happened to be noisy. CC keeps
    # its own dedicated gatefilter and grid pass to avoid that cross-
    # contamination.
    shared_gf = pyart.filters.GateFilter(radar)
    shared_gf.exclude_below("reflectivity", REFLECTIVITY_FLOOR)

    cc_gf = pyart.filters.GateFilter(radar)
    cc_gf.exclude_below("reflectivity", REFLECTIVITY_FLOOR)
    if "cross_correlation_ratio" in radar.fields:
        cc_gf.exclude_below("cross_correlation_ratio", CORR_COEFF_MIN)

    # Dealias Base Velocity if present
    if "velocity" in radar.fields:
        try:
            dealiased_dict = pyart.correct.dealias_region_based(
                radar,
                vel_field="velocity",
                gatefilter=False,
                keep_original=True,
                centered=True,
            )
            radar.add_field("velocity", dealiased_dict, replace_existing=True)
        except Exception:  # noqa: BLE001
            pass

    products = {}
    available = []
    notes = {}
    grid_fields = {}   # product_key -> raw masked ndarray (post-scale), for cursor hover lookups

    # --- Pass A: Reflectivity + Base Velocity + ZDR (one shared grid pass) ---
    shared_field_names = ["reflectivity"]
    if "velocity" in radar.fields:
        shared_field_names.append("velocity")
    if "differential_reflectivity" in radar.fields:
        shared_field_names.append("differential_reflectivity")

    shared_grid = pyart.map.grid_from_radars(
        (radar,), gatefilters=(shared_gf,),
        grid_shape=(1, GRID_CELLS, GRID_CELLS),
        grid_limits=((0, 1000), (-GRID_RANGE_M, GRID_RANGE_M), (-GRID_RANGE_M, GRID_RANGE_M)),
        fields=shared_field_names,
    )

    cfg_refl = PRODUCTS["reflectivity"]
    refl_data = shared_grid.fields["reflectivity"]["data"][0]
    products["reflectivity"] = _field_to_png_base64(
        refl_data, cfg_refl["vmin"], cfg_refl["vmax"], cfg_refl["cmap"](), cfg_refl["transparent_below"]
    )
    available.append("reflectivity")
    grid_fields["reflectivity"] = refl_data

    if "velocity" in shared_field_names:
        cfg_vel = PRODUCTS["velocity"]
        vel_data = shared_grid.fields["velocity"]["data"][0]
        vel_mask = np.ma.getmaskarray(vel_data) if np.ma.is_masked(vel_data) else np.zeros(vel_data.shape, dtype=bool)

        if not vel_mask.all():
            scale = cfg_vel.get("scale", 1.0)
            if scale != 1.0:
                vel_data = vel_data * scale
            products["velocity"] = _field_to_png_base64(
                vel_data, cfg_vel["vmin"], cfg_vel["vmax"], cfg_vel["cmap"](), cfg_vel["transparent_below"]
            )
            available.append("velocity")
            grid_fields["velocity"] = vel_data
        else:
            notes["velocity"] = "Velocity data masked out across entire grid."
    else:
        notes["velocity"] = "not present in this volume (radar may have been in a reflectivity-only/clear-air scan)"

    if "differential_reflectivity" in shared_field_names:
        cfg_zdr = PRODUCTS["differential_reflectivity"]
        zdr_data = shared_grid.fields["differential_reflectivity"]["data"][0]
        zdr_mask = np.ma.getmaskarray(zdr_data) if np.ma.is_masked(zdr_data) else np.zeros(zdr_data.shape, dtype=bool)

        if not zdr_mask.all():
            products["differential_reflectivity"] = _field_to_png_base64(
                zdr_data, cfg_zdr["vmin"], cfg_zdr["vmax"], cfg_zdr["cmap"](), cfg_zdr["transparent_below"]
            )
            available.append("differential_reflectivity")
            grid_fields["differential_reflectivity"] = zdr_data
        else:
            notes["differential_reflectivity"] = "no gates passed quality filtering this scan"
    else:
        notes["differential_reflectivity"] = "not present in this volume (radar may have been in a legacy/non-dual-pol scan)"

    # --- Pass B: Correlation Coefficient (separate pass — see note above) ---
    if "cross_correlation_ratio" in radar.fields:
        cc_grid = pyart.map.grid_from_radars(
            (radar,), gatefilters=(cc_gf,),
            grid_shape=(1, GRID_CELLS, GRID_CELLS),
            grid_limits=((0, 1000), (-GRID_RANGE_M, GRID_RANGE_M), (-GRID_RANGE_M, GRID_RANGE_M)),
            fields=["cross_correlation_ratio"],
        )
        cfg_cc = PRODUCTS["correlation_coefficient"]
        cc_data = cc_grid.fields["cross_correlation_ratio"]["data"][0]
        cc_mask = np.ma.getmaskarray(cc_data) if np.ma.is_masked(cc_data) else np.zeros(cc_data.shape, dtype=bool)

        if not cc_mask.all():
            products["correlation_coefficient"] = _field_to_png_base64(
                cc_data, cfg_cc["vmin"], cfg_cc["vmax"], cfg_cc["cmap"](), cfg_cc["transparent_below"]
            )
            available.append("correlation_coefficient")
            grid_fields["correlation_coefficient"] = cc_data
        else:
            notes["correlation_coefficient"] = "no gates passed quality filtering this scan"
    else:
        notes["correlation_coefficient"] = "not present in this volume"

    coords = _corners_latlon(origin_lat, origin_lon, GRID_RANGE_M)
    return products, available, notes, coords, grid_fields


def _synthetic_demo(station: str) -> RadarOverlay:
    meta = STATIONS.get(station, {"lat": 42.8939, "lon": -85.5450})
    rng = np.random.default_rng(42)
    y, x = np.mgrid[0:GRID_CELLS, 0:GRID_CELLS]
    refl = np.zeros((GRID_CELLS, GRID_CELLS))
    for _ in range(4):
        cy, cx = rng.uniform(90, GRID_CELLS - 90, size=2)
        sigma = rng.uniform(28, 60)
        amp = rng.uniform(30, 65)
        refl += amp * np.exp(-(((y - cy) ** 2 + (x - cx) ** 2) / (2 * sigma ** 2)))
    refl = np.clip(refl + rng.normal(0, 1.5, refl.shape), 0, REFLECTIVITY_MAX)
    refl_masked = np.ma.masked_less(refl, REFLECTIVITY_FLOOR)

    vel = np.clip((refl - 30) * rng.uniform(-1, 1) + rng.normal(0, 3, refl.shape), -VELOCITY_MAX_MPH, VELOCITY_MAX_MPH)
    vel_masked = np.ma.array(vel, mask=np.ma.getmaskarray(refl_masked))

    cc = np.clip(0.92 + refl / 500 + rng.normal(0, 0.02, refl.shape), CORR_COEFF_MIN, 1.02)
    cc_masked = np.ma.array(cc, mask=np.ma.getmaskarray(refl_masked))

    zdr = np.clip((refl - 20) / 15 + rng.normal(0, 0.4, refl.shape), ZDR_MIN, ZDR_MAX)
    zdr_masked = np.ma.array(zdr, mask=np.ma.getmaskarray(refl_masked))

    products = {
        "reflectivity": _field_to_png_base64(refl_masked, REFLECTIVITY_FLOOR, REFLECTIVITY_MAX, _reflectivity_cmap(), REFLECTIVITY_FLOOR),
        "velocity": _field_to_png_base64(vel_masked, -VELOCITY_MAX_MPH, VELOCITY_MAX_MPH, _velocity_cmap(), None),
        "correlation_coefficient": _field_to_png_base64(cc_masked, CORR_COEFF_MIN, 1.02, _corr_coeff_cmap(), None),
        "differential_reflectivity": _field_to_png_base64(zdr_masked, ZDR_MIN, ZDR_MAX, _zdr_cmap(), None),
    }
    grid_fields = {
        "reflectivity": refl_masked,
        "velocity": vel_masked,
        "correlation_coefficient": cc_masked,
        "differential_reflectivity": zdr_masked,
    }
    coords = _corners_latlon(meta["lat"], meta["lon"], GRID_RANGE_M)
    return RadarOverlay(
        products=products,
        available_products=list(products.keys()),
        product_notes={},
        coordinates=coords,
        station=station,
        volume_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + " (synthetic demo)",
        source="synthetic-demo",
        grid_fields=grid_fields,
        origin_lat=meta["lat"],
        origin_lon=meta["lon"],
    )


def _available_tilts(radar) -> list:
    """Deduplicated list of {"sweep": index, "angle": degrees} for a volume,
    lowest elevation first. NEXRAD VCPs commonly scan the lowest tilt(s)
    twice — once at reduced PRF for a cleaner reflectivity return, once at
    higher PRF for velocity/dual-pol — so raw fixed_angle data often has
    repeated angles; we keep the first sweep index seen for each distinct
    angle rather than exposing both as separate picks."""
    angles = radar.fixed_angle["data"]
    seen = {}
    for sweep_idx, angle in enumerate(angles):
        angle = round(float(angle), 1)
        if angle not in seen:
            seen[angle] = sweep_idx
    return [{"sweep": seen[a], "angle": a} for a in sorted(seen)]


def get_latest_overlay(station: str = "KIWX") -> RadarOverlay:
    if station not in STATIONS:
        raise ValueError(f"Unknown station {station!r}; choices are {list(STATIONS)}")

    t_start = time.perf_counter()
    try:
        key = _latest_key(station)
        t_key = time.perf_counter()
        if key is None:
            raise RuntimeError(f"No recent volumes found for {station}")
        s3 = _s3_client()
        with tempfile.NamedTemporaryFile(suffix=".ar2v") as tmp:
            s3.download_fileobj(BUCKET, key, tmp)
            tmp.flush()
            t_download = time.perf_counter()
            file_size_mb = tmp.tell() / (1024 * 1024)
            radar = pyart.io.read_nexrad_archive(tmp.name)
            t_parse = time.perf_counter()

        origin_lat = float(radar.latitude["data"][0])
        origin_lon = float(radar.longitude["data"][0])
        products, available, notes, coords, grid_fields = _grid_and_render(radar, origin_lat, origin_lon)
        t_grid = time.perf_counter()

        volume_time = key.split("/")[-1]
        _RAW_RADAR_CACHE[station] = {"radar": radar, "volume_time": volume_time}
        available_tilts = _available_tilts(radar)

        print(
            f"[timing] {station}: "
            f"find-key {t_key - t_start:.2f}s | "
            f"download {t_download - t_key:.2f}s ({file_size_mb:.1f} MB) | "
            f"parse {t_parse - t_download:.2f}s | "
            f"grid+render {t_grid - t_parse:.2f}s | "
            f"total {t_grid - t_start:.2f}s"
        )

        return RadarOverlay(
            products=products,
            available_products=available,
            product_notes=notes,
            coordinates=coords,
            station=station,
            volume_time=volume_time,
            source="live",
            grid_fields=grid_fields,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            available_tilts=available_tilts,
            current_tilt_sweep=None,
        )
    except Exception as exc:  # noqa: BLE001
        fallback = _synthetic_demo(station)
        fallback.volume_time += f"  [live fetch failed: {exc.__class__.__name__}: {exc}]"
        return fallback


def render_composite(station: str) -> RadarOverlay:
    """Re-grid the full cached volume (all sweeps blended, the normal
    default view) — same cached raw radar as render_tilt(), just without
    extract_sweeps(). Lets switching back from a specific tilt to the
    composite be just as instant as picking a tilt in the first place."""
    cached = _RAW_RADAR_CACHE.get(station)
    if cached is None:
        raise RuntimeError(f"No cached volume for {station} — load it normally first")
    radar = cached["radar"]

    origin_lat = float(radar.latitude["data"][0])
    origin_lon = float(radar.longitude["data"][0])
    products, available, notes, coords, grid_fields = _grid_and_render(radar, origin_lat, origin_lon)

    return RadarOverlay(
        products=products,
        available_products=available,
        product_notes=notes,
        coordinates=coords,
        station=station,
        volume_time=f"{cached['volume_time']} (composite)",
        source="live",
        grid_fields=grid_fields,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        available_tilts=_available_tilts(radar),
        current_tilt_sweep=None,
    )


def render_tilt(station: str, sweep: int) -> RadarOverlay:
    """Re-grid a single elevation angle from the already-cached raw radar
    object for this station — no S3 fetch. Raises RuntimeError if nothing's
    cached for this station yet (e.g. it was never actually loaded, only
    picked from the dropdown).

    Split-cut VCPs scan the lowest tilt(s) twice: once at low PRF for
    full-range reflectivity, once at high PRF for velocity/dual-pol (whose
    much shorter unambiguous range range-folds/masks reflectivity beyond
    it). Extracting only the first duplicate sweep at a given angle can
    silently grab the range-limited one and make a real storm look nearly
    empty. To avoid that, every sweep sharing this angle gets extracted
    together, so whichever one actually has full-range data for a given
    product is what ends up gridded — same principle the full composite
    already relies on."""
    cached = _RAW_RADAR_CACHE.get(station)
    if cached is None:
        raise RuntimeError(f"No cached volume for {station} — load it normally first")
    radar = cached["radar"]

    angles = radar.fixed_angle["data"]
    target_angle = round(float(angles[sweep]), 1)
    matching_sweeps = [i for i, a in enumerate(angles) if round(float(a), 1) == target_angle]

    single_sweep_radar = radar.extract_sweeps(matching_sweeps)
    origin_lat = float(radar.latitude["data"][0])
    origin_lon = float(radar.longitude["data"][0])
    products, available, notes, coords, grid_fields = _grid_and_render(single_sweep_radar, origin_lat, origin_lon)

    return RadarOverlay(
        products=products,
        available_products=available,
        product_notes=notes,
        coordinates=coords,
        station=station,
        volume_time=f"{cached['volume_time']} ({target_angle:.1f}° tilt)",
        source="live-tilt",
        grid_fields=grid_fields,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        available_tilts=_available_tilts(radar),
        current_tilt_sweep=sweep,
    )


# ---------------------------------------------------------------------------
# NWS active warning polygons
# ---------------------------------------------------------------------------

WARNING_EVENT_COLORS = {
    "Tornado Warning": "#FF1E1E",
    "Severe Thunderstorm Warning": "#FFA500",
    "Flash Flood Warning": "#2ECC71",
    "Special Marine Warning": "#FF3FFF",
    "Snow Squall Warning": "#3FD0FF",
    "Dust Storm Warning": "#C2A24B",
    "Extreme Wind Warning": "#B02EA0",
}
WARNING_EVENTS = set(WARNING_EVENT_COLORS.keys())

_NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"
_NWS_USER_AGENT = "ontheball-radar-app (personal/hobby project; contact: n/a)"


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _flatten_coords(geom: dict):
    pts = []

    def rec(c):
        if isinstance(c[0], (float, int)):
            pts.append((c[0], c[1]))
        else:
            for x in c:
                rec(x)

    rec(geom.get("coordinates", []))
    return pts


def _geom_near(geom: dict, lat: float, lon: float, range_km: float) -> bool:
    coords = _flatten_coords(geom)
    if not coords:
        return False
    buffered = range_km * 1.15
    return any(_haversine_km(lat, lon, c_lat, c_lon) <= buffered for c_lon, c_lat in coords)


def fetch_warnings(center_lat: float, center_lon: float, range_km: float = 230.0) -> dict:
    try:
        req = urllib.request.Request(
            _NWS_ALERTS_URL,
            headers={"User-Agent": _NWS_USER_AGENT, "Accept": "application/geo+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"type": "FeatureCollection", "features": [], "_error": f"{exc.__class__.__name__}: {exc}"}

    features = []
    for feat in data.get("features", []):
        props = feat.get("properties", {}) or {}
        event = props.get("event")
        geom = feat.get("geometry")
        if event not in WARNING_EVENTS or not geom:
            continue
        if not _geom_near(geom, center_lat, center_lon, range_km):
            continue
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "event": event,
                "headline": props.get("headline", "") or "",
                "expires": props.get("expires", "") or "",
                "color": WARNING_EVENT_COLORS.get(event, "#FFFFFF"),
            },
        })

    return {"type": "FeatureCollection", "features": features}