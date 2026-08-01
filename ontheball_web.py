#!/usr/bin/env python3
"""
ontheball_web.py — MapLibre GL basemap + radar rendered as georeferenced
PNG overlays, supporting single or multi-station composite radar views,
product switching, clickable station markers, radar opacity slider, and
play/pause history playback.

Run: python3 ontheball_web.py
"""

import functools
import http.server
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QDoubleValidator
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSlider, QStatusBar, QVBoxLayout, QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtWebChannel import QWebChannel

import radar_source

__version__ = "0.9.2"

ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "img" / "logo.png"
HTTP_PORT = 8765

MAX_HISTORY = 12          # cap on cached in-session frames
PLAYBACK_FRAME_MS = 600   # time each frame stays on screen during playback
HOME_STATION_COUNT = 3    # how many closest stations to load when Home Location is set
DEFAULT_REFRESH_INTERVAL_SEC = 300   # used until we've measured a station's actual cadence
MIN_REFRESH_INTERVAL_SEC = 60        # floor, so a fast-cycling station can't cause hammering
MAX_REFRESH_INTERVAL_SEC = 900       # ceiling, in case of a bad/stale reading
REFRESH_BUFFER_SEC = 30              # extra margin added to the measured cadence — volumes
                                      # take a little time to actually land in S3 after the
                                      # scan finishes, so refreshing at exactly the measured
                                      # interval can still catch the previous (stale) volume

_VOLUME_TIME_RE = re.compile(r"(\d{8})_(\d{6})")


def _parse_volume_datetime(volume_time: str):
    """Extract the actual scan datetime (UTC) from a volume_time string like
    'KGRR20260727_113656_V06'. Returns None for anything that doesn't match
    (e.g. synthetic-demo placeholders), so callers can just skip those."""
    m = _VOLUME_TIME_RE.search(volume_time)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def start_local_server(directory: Path, port: int):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


class MultiRadarFetchWorker(QThread):
    finished_ok = pyqtSignal(list)
    finished_err = pyqtSignal(str)

    def __init__(self, stations: list[str]):
        super().__init__()
        self.stations = stations

    def run(self):
        overlays_by_index = {}
        errors = []

        # S3 downloads and Py-ART gridding are both I/O- and CPU-heavy;
        # fetching stations one at a time meant N stations took N times as
        # long as one. A small thread pool lets them overlap instead —
        # while one station's volume is downloading, another can already be
        # gridding, and Py-ART's C extensions release the GIL during the
        # heavy interpolation work so real parallelism is possible here
        # despite Python's GIL.
        max_workers = min(len(self.stations), 4)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_index = {
                pool.submit(radar_source.get_latest_overlay, st): idx
                for idx, st in enumerate(self.stations)
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                st = self.stations[idx]
                try:
                    overlays_by_index[idx] = future.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{st}: {exc.__class__.__name__}: {exc}")

        # Reassemble in the original station order (closest-first for Home
        # mode) regardless of which fetch happened to finish first.
        overlays = [overlays_by_index[i] for i in range(len(self.stations)) if i in overlays_by_index]

        if overlays:
            self.finished_ok.emit(overlays)
        else:
            self.finished_err.emit(" | ".join(errors))


class WarningsFetchWorker(QThread):
    finished_ok = pyqtSignal(dict)

    def __init__(self, lat: float, lon: float):
        super().__init__()
        self.lat = lat
        self.lon = lon

    def run(self):
        result = radar_source.fetch_warnings(self.lat, self.lon)
        self.finished_ok.emit(result)


class Bridge(QObject):
    overlaysReady = pyqtSignal(str, str)          # JSON list of overlays, meta text
    statusChanged = pyqtSignal(str)
    stationsReady = pyqtSignal(str)              # JSON list of station dicts
    readyFromJs = pyqtSignal()                   # fired once JS side connects
    stationSelected = pyqtSignal(str)            # fired when a map marker is clicked
    stationShiftSelected = pyqtSignal(str)        # fired when a map marker is shift-clicked (toggle multi-select)
    shiftReleased = pyqtSignal()                  # fired when the Shift key is released — time to fetch the batch
    selectedStationsChanged = pyqtSignal(str)     # JSON list of station codes, tells JS which markers to highlight
    basemapChanged = pyqtSignal(str)             # "light" | "dark"
    opacityChanged = pyqtSignal(int)             # 0-100
    legendReady = pyqtSignal(str)                # JSON legend spec for current product
    warningsReady = pyqtSignal(str)              # JSON GeoJSON FeatureCollection
    cursorMoved = pyqtSignal(float, float)       # (lat, lon) under the mouse, for the distance-from-home readout
    hoverValueReady = pyqtSignal(str)            # JSON {value, pct, unit} or {value: null} for the legend hover-highlight
    homeMarkerReady = pyqtSignal(str)            # JSON {lat, lon} once a home location is set
    homeMarkerCleared = pyqtSignal()             # fired when Clear Home is clicked
    armHomeSelection = pyqtSignal(bool)          # True = enter "click the map to set home" mode, False = cancel
    homeLocationClicked = pyqtSignal(float, float)  # fired when the map is clicked while armed

    @pyqtSlot()
    def jsReady(self):
        self.readyFromJs.emit()

    @pyqtSlot(str)
    def selectStation(self, code: str):
        self.stationSelected.emit(str(code))

    @pyqtSlot(str)
    def selectStationShift(self, code: str):
        self.stationShiftSelected.emit(str(code))

    @pyqtSlot()
    def reportShiftReleased(self):
        self.shiftReleased.emit()

    @pyqtSlot(float, float)
    def reportCursorPosition(self, lat: float, lon: float):
        self.cursorMoved.emit(lat, lon)

    @pyqtSlot(float, float)
    def reportHomeLocationClick(self, lat: float, lon: float):
        self.homeLocationClicked.emit(lat, lon)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ontheball — web basemap prototype v{__version__}")
        self.resize(1150, 860)
        if LOGO_PATH.exists():
            self.setWindowIcon(QIcon(str(LOGO_PATH)))

        self.bridge = Bridge()
        self.worker = None
        self.warnings_worker = None

        # Home location: entered fresh each session, never persisted to disk.
        # When set, self.home_active_stations overrides whatever's picked in
        # the Station dropdown until the user manually selects a station again.
        self.home_lat: float | None = None
        self.home_lon: float | None = None
        self.home_active_stations: list | None = None

        # Shift-click multi-select: an explicit, ordered list of station
        # codes the user has manually armed by shift-clicking their markers.
        # Overrides home_active_stations while non-empty; a plain (non-shift)
        # station click clears it and goes back to single-station mode.
        self.manual_stations: list = []
        self._pending_manual_cached: dict = {}

        # Auto-refresh timing: measured from the actual gap between
        # consecutive volumes per station, rather than a fixed dropdown
        # interval. Starts at a reasonable guess and self-corrects once
        # real data starts coming in.
        self.last_volume_dt: dict = {}
        self.auto_refresh_interval_sec: int = DEFAULT_REFRESH_INTERVAL_SEC

        self.history: list = []
        self.history_index: int = -1

        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.advance_history_frame)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Station:"))
        self.station_combo = QComboBox()

        for code, meta in radar_source.STATIONS.items():
            self.station_combo.addItem(f"{code} — {meta['name']}", userData=code)

        # Default selection: KIWX
        default_idx = self.station_combo.findData("KIWX")
        if default_idx >= 0:
            self.station_combo.setCurrentIndex(default_idx)

        self.station_combo.currentIndexChanged.connect(self.on_station_changed)
        controls.addWidget(self.station_combo)

        controls.addWidget(QLabel("Product:"))
        self.product_combo = QComboBox()
        for key, cfg in radar_source.PRODUCTS.items():
            self.product_combo.addItem(cfg["label"], userData=key)
        self.product_combo.currentIndexChanged.connect(self.on_product_changed)
        controls.addWidget(self.product_combo)

        controls.addWidget(QLabel("Basemap:"))
        self.basemap_combo = QComboBox()
        self.basemap_combo.addItem("Dark", userData="dark")
        self.basemap_combo.addItem("Light", userData="light")
        self.basemap_combo.currentIndexChanged.connect(self.on_basemap_changed)
        controls.addWidget(self.basemap_combo)

        controls.addWidget(QLabel("Radar opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(30)
        self.opacity_slider.setFixedWidth(110)
        self.opacity_slider.valueChanged.connect(self.on_opacity_changed)
        controls.addWidget(self.opacity_slider)

        self.refresh_btn = QPushButton("Refresh now")
        self.refresh_btn.clicked.connect(self.refresh_now)
        controls.addWidget(self.refresh_btn)

        controls.addWidget(QLabel("Auto-refresh:"))
        self.auto_refresh_checkbox = QCheckBox()
        self.auto_refresh_checkbox.toggled.connect(self.on_auto_refresh_toggled)
        controls.addWidget(self.auto_refresh_checkbox)
        controls.addStretch(1)
        layout.addLayout(controls)

        home_controls = QHBoxLayout()
        home_controls.addWidget(QLabel("Home Location (lat, lon):"))

        coord_validator = QDoubleValidator(-180.0, 180.0, 6)
        coord_validator.setNotation(QDoubleValidator.Notation.StandardNotation)

        self.home_lat_input = QLineEdit()
        self.home_lat_input.setPlaceholderText("e.g. 41.9525")
        self.home_lat_input.setValidator(coord_validator)
        self.home_lat_input.setFixedWidth(90)
        home_controls.addWidget(self.home_lat_input)

        self.home_lon_input = QLineEdit()
        self.home_lon_input.setPlaceholderText("e.g. -85.3163")
        self.home_lon_input.setValidator(coord_validator)
        self.home_lon_input.setFixedWidth(90)
        home_controls.addWidget(self.home_lon_input)

        self.set_home_btn = QPushButton("Set Home")
        self.set_home_btn.clicked.connect(self.on_set_home_clicked)
        home_controls.addWidget(self.set_home_btn)

        self.clear_home_btn = QPushButton("Clear Home")
        self.clear_home_btn.clicked.connect(self.on_clear_home_clicked)
        home_controls.addWidget(self.clear_home_btn)

        self.home_status_label = QLabel("Home not set — type lat/lon, or leave both blank and click Set Home to pick a spot on the map")
        home_controls.addWidget(self.home_status_label)
        home_controls.addStretch(1)
        layout.addLayout(home_controls)

        history_controls = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle_play)
        history_controls.addWidget(self.play_btn)

        self.history_slider = QSlider(Qt.Orientation.Horizontal)
        self.history_slider.setRange(0, 0)
        self.history_slider.setEnabled(False)
        self.history_slider.valueChanged.connect(self.on_history_slider_changed)
        history_controls.addWidget(self.history_slider, 1)

        self.history_label = QLabel("no frames yet")
        self.history_label.setMinimumWidth(220)
        history_controls.addWidget(self.history_label)
        layout.addLayout(history_controls)

        self.view = QWebEngineView()
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl(f"http://127.0.0.1:{HTTP_PORT}/map.html"))
        layout.addWidget(self.view, 1)

        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("loading map…")

        self.distance_label = QLabel("")
        self.status.addPermanentWidget(self.distance_label)

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.refresh_now)

        self.bridge.readyFromJs.connect(self.on_js_ready)
        self.bridge.stationSelected.connect(self.on_station_selected_from_map)
        self.bridge.stationShiftSelected.connect(self.on_station_shift_selected)
        self.bridge.shiftReleased.connect(self.on_shift_released)
        self.bridge.cursorMoved.connect(self.on_cursor_moved)
        self.bridge.homeLocationClicked.connect(self.on_home_location_clicked)

        self._home_selection_armed = False

    def on_js_ready(self):
        stations_payload = [
            {"code": code, "name": meta["name"], "lat": meta["lat"], "lon": meta["lon"]}
            for code, meta in radar_source.STATIONS.items()
        ]
        self.bridge.stationsReady.emit(json.dumps(stations_payload))
        self.bridge.basemapChanged.emit(self.basemap_combo.currentData())
        self.bridge.opacityChanged.emit(self.opacity_slider.value())
        self.refresh_now()

    def _show_static_frame(self, overlays: list):
        """Display a frame built from already-cached overlays (no fetch) —
        used both when clicking an already-loaded station and when a
        shift-click multi-select can be satisfied entirely from cache."""
        self.play_timer.stop()
        self.play_btn.setText("▶ Play")
        self.play_btn.setEnabled(False)
        self.history = [overlays]
        self.history_index = 0
        self.history_slider.blockSignals(True)
        self.history_slider.setRange(0, 0)
        self.history_slider.setEnabled(False)
        self.history_slider.blockSignals(False)
        self._display_current_frame()

    def on_station_selected_from_map(self, code: str):
        idx = self.station_combo.findData(code)
        if idx < 0:
            return

        # If this station is already rendered in the current frame (e.g. one
        # of the up-to-3 Home-mode stations already on screen), reuse that
        # overlay instead of firing a brand-new fetch — it's already been
        # downloaded and gridded, so this is instant instead of repeating
        # the whole S3-download-plus-Py-ART pipeline for data we already have.
        cached_overlay = None
        if self.history:
            current_idx = max(0, min(self.history_index, len(self.history) - 1))
            for ov in self.history[current_idx]:
                if ov.station == code:
                    cached_overlay = ov
                    break

        self.home_active_stations = None
        if self.manual_stations:
            self.manual_stations = []
            self.bridge.selectedStationsChanged.emit(json.dumps([]))
        self.station_combo.blockSignals(True)
        self.station_combo.setCurrentIndex(idx)
        self.station_combo.blockSignals(False)

        if cached_overlay is not None:
            self._show_static_frame([cached_overlay])
            self.status.showMessage(
                f"{code} — showing already-loaded data (click Refresh now for the newest volume)", 5000
            )
        else:
            self.reset_history()
            self.refresh_now()

    def on_station_shift_selected(self, code: str):
        """Shift-click toggles a station in/out of the manual selection and
        updates the marker highlight immediately, but doesn't fetch yet —
        that happens once on_shift_released fires, so clicking several
        markers in a row batches into one fetch instead of each click
        racing the previous one's still-in-flight request."""
        if code in self.manual_stations:
            self.manual_stations.remove(code)
        else:
            self.manual_stations.append(code)

        self.home_active_stations = None
        self.bridge.selectedStationsChanged.emit(json.dumps(self.manual_stations))

        if self.manual_stations:
            self.status.showMessage(f"Selected {' + '.join(self.manual_stations)} — release Shift to load", 0)
        else:
            self.status.showMessage("Selection cleared", 3000)

    def on_shift_released(self):
        """Fetch whatever's missing for the current manual selection, once
        the whole shift-click gesture is done. Stations already cached in
        the current frame are reused instantly with no fetch at all."""
        if not self.manual_stations:
            return

        cached = {}
        if self.history:
            current_idx = max(0, min(self.history_index, len(self.history) - 1))
            for ov in self.history[current_idx]:
                cached[ov.station] = ov

        missing = [st for st in self.manual_stations if st not in cached]

        if not missing:
            ordered = [cached[st] for st in self.manual_stations]
            self._show_static_frame(ordered)
            self.status.showMessage(f"Showing {' + '.join(self.manual_stations)}", 3000)
            return

        if self.worker is not None and self.worker.isRunning():
            # A fetch is already in flight (e.g. auto-refresh landed at the
            # same moment) — this batch will just get picked up next time
            # Shift is released, rather than getting silently dropped.
            self.status.showMessage("Still fetching — release Shift again once it finishes", 3000)
            return

        self._pending_manual_cached = cached
        self.status.showMessage(f"fetching {' + '.join(missing)}…")
        self.worker = MultiRadarFetchWorker(missing)
        self.worker.finished_ok.connect(self._on_manual_overlays_ready)
        self.worker.finished_err.connect(self.on_overlay_error)
        self.worker.start()

    def _on_manual_overlays_ready(self, new_overlays: list):
        cached = dict(self._pending_manual_cached)
        for ov in new_overlays:
            cached[ov.station] = ov
        self._pending_manual_cached = {}

        ordered = [cached[st] for st in self.manual_stations if st in cached]
        if not ordered:
            return
        self._update_measured_refresh_interval(new_overlays)
        self._show_static_frame(ordered)
        self.status.showMessage(f"Showing {' + '.join(ov.station for ov in ordered)}", 3000)

    def current_station(self) -> str:
        return self.station_combo.currentData()

    def current_product(self) -> str:
        return self.product_combo.currentData()

    def on_auto_refresh_toggled(self, checked: bool):
        if checked:
            self.auto_timer.start(self.auto_refresh_interval_sec * 1000)
        else:
            self.auto_timer.stop()

    def on_station_changed(self, _index: int):
        self.home_active_stations = None
        if self.manual_stations:
            self.manual_stations = []
            self.bridge.selectedStationsChanged.emit(json.dumps([]))
        self.reset_history()
        self.refresh_now()

    def on_set_home_clicked(self):
        lat_text = self.home_lat_input.text().strip()
        lon_text = self.home_lon_input.text().strip()

        if not lat_text and not lon_text:
            # Both fields empty: toggle "click the map to set home" mode
            # instead of treating this as a validation error.
            if self._home_selection_armed:
                self._disarm_home_selection()
            else:
                self._arm_home_selection()
            return

        try:
            lat = float(lat_text)
            lon = float(lon_text)
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
                raise ValueError("out of range")
        except ValueError:
            self.home_status_label.setText("Enter valid lat/lon, e.g. 41.9525, -85.3163 — or clear both fields and click Set Home to pick a spot on the map")
            return

        self._disarm_home_selection()
        self._apply_home_location(lat, lon)

    def _arm_home_selection(self):
        self._home_selection_armed = True
        self.set_home_btn.setText("Click the map…")
        self.home_status_label.setText("Click anywhere on the map to set that as home (or click Set Home again to cancel)")
        self.bridge.armHomeSelection.emit(True)

    def _disarm_home_selection(self):
        self._home_selection_armed = False
        self.set_home_btn.setText("Set Home")
        self.bridge.armHomeSelection.emit(False)

    def on_home_location_clicked(self, lat: float, lon: float):
        if not self._home_selection_armed:
            return
        self._disarm_home_selection()
        self.home_lat_input.setText(f"{lat:.4f}")
        self.home_lon_input.setText(f"{lon:.4f}")
        self._apply_home_location(lat, lon)

    def _apply_home_location(self, lat: float, lon: float):
        self.home_lat = lat
        self.home_lon = lon
        self.home_active_stations = radar_source.find_closest_stations(lat, lon, n=HOME_STATION_COUNT)
        if self.manual_stations:
            self.manual_stations = []
            self.bridge.selectedStationsChanged.emit(json.dumps([]))
        self.home_status_label.setText(f"Home set — showing {', '.join(self.home_active_stations)}")
        self.bridge.homeMarkerReady.emit(json.dumps({"lat": lat, "lon": lon}))
        self.reset_history()
        self.refresh_now()

    def on_clear_home_clicked(self):
        self._disarm_home_selection()
        self.home_lat = None
        self.home_lon = None
        self.home_active_stations = None
        self.home_lat_input.clear()
        self.home_lon_input.clear()
        self.distance_label.setText("")
        self.home_status_label.setText("Home not set — type lat/lon, or leave both blank and click Set Home to pick a spot on the map")
        self.bridge.homeMarkerCleared.emit()
        self.reset_history()
        self.refresh_now()

    def on_cursor_moved(self, lat: float, lon: float):
        if self.home_lat is not None and self.home_lon is not None:
            dist_km = radar_source._haversine_km(self.home_lat, self.home_lon, lat, lon)
            dist_mi = dist_km * 0.621371
            self.distance_label.setText(f"{dist_mi:.1f} mi from home")

        self._emit_hover_value(lat, lon)

    def _emit_hover_value(self, lat: float, lon: float):
        """Sample the currently-displayed product's real value under the
        cursor and send it to JS so the legend can highlight where that
        value sits on the color scale — the on-map equivalent of GR2Analyst's
        cursor readout. Tries each station currently on screen (there can be
        up to 3 in Home mode) and uses whichever one's grid actually covers
        that point."""
        if not self.history:
            self.bridge.hoverValueReady.emit(json.dumps({"value": None}))
            return

        idx = max(0, min(self.history_index, len(self.history) - 1))
        overlays = self.history[idx]
        product = self.current_product()
        cfg = radar_source.PRODUCTS[product]

        value = None
        for overlay in overlays:
            value = radar_source.sample_value(overlay, product, lat, lon)
            if value is not None:
                break

        if value is None:
            self.bridge.hoverValueReady.emit(json.dumps({"value": None}))
            return

        vmin, vmax = cfg["vmin"], cfg["vmax"]
        pct = max(0.0, min(100.0, (value - vmin) / (vmax - vmin) * 100.0))
        self.bridge.hoverValueReady.emit(json.dumps({
            "value": round(value, 2),
            "pct": round(pct, 2),
            "unit": cfg["unit"],
        }))

    def reset_history(self):
        self.play_timer.stop()
        self.play_btn.setText("▶ Play")
        self.play_btn.setEnabled(False)
        self.history = []
        self.history_index = -1
        self.history_slider.blockSignals(True)
        self.history_slider.setRange(0, 0)
        self.history_slider.setEnabled(False)
        self.history_slider.blockSignals(False)
        self.history_label.setText("no frames yet")

    def on_product_changed(self, _index: int):
        self._display_current_frame()

    def on_basemap_changed(self, _index: int):
        self.bridge.basemapChanged.emit(self.basemap_combo.currentData())

    def on_opacity_changed(self, value: int):
        self.bridge.opacityChanged.emit(value)

    def refresh_now(self):
        if self.worker is not None and self.worker.isRunning():
            return
        selected_code = self.current_station()
        if self.manual_stations:
            stations = self.manual_stations
        elif self.home_active_stations:
            stations = self.home_active_stations
        else:
            stations = [selected_code]

        self.status.showMessage(f"fetching {' + '.join(stations)}…")
        self.refresh_btn.setEnabled(False)
        self.worker = MultiRadarFetchWorker(stations)
        self.worker.finished_ok.connect(self.on_overlays_ready)
        self.worker.finished_err.connect(self.on_overlay_error)
        self.worker.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self.worker.start()

        if self.warnings_worker is None or not self.warnings_worker.isRunning():
            ref_station = stations[0]
            meta = radar_source.STATIONS[ref_station]
            self.warnings_worker = WarningsFetchWorker(meta["lat"], meta["lon"])
            self.warnings_worker.finished_ok.connect(self.on_warnings_ready)
            self.warnings_worker.start()

    def _update_measured_refresh_interval(self, overlays: list):
        """Update self.auto_refresh_interval_sec from the real gap between this
        fetch's volume(s) and the previous one for the same station(s), so
        auto-refresh follows each radar's actual scan cadence instead of a
        fixed guess. Uses the fastest (minimum) cadence among active stations,
        so a multi-station Home view doesn't miss a quicker-cycling site."""
        deltas = []
        for overlay in overlays:
            new_dt = _parse_volume_datetime(overlay.volume_time)
            if new_dt is None:
                continue
            prev_dt = self.last_volume_dt.get(overlay.station)
            if prev_dt is not None and new_dt > prev_dt:
                deltas.append((new_dt - prev_dt).total_seconds())
            self.last_volume_dt[overlay.station] = new_dt

        if deltas:
            measured = min(deltas) + REFRESH_BUFFER_SEC
            new_interval = int(max(MIN_REFRESH_INTERVAL_SEC, min(MAX_REFRESH_INTERVAL_SEC, measured)))
            if new_interval != self.auto_refresh_interval_sec:
                self.auto_refresh_interval_sec = new_interval
                if self.auto_timer.isActive():
                    self.auto_timer.start(self.auto_refresh_interval_sec * 1000)

    def on_overlays_ready(self, overlays: list):
        self._update_measured_refresh_interval(overlays)

        was_at_live = (self.history_index == -1) or (self.history_index == len(self.history) - 1)

        self.history.append(overlays)
        if len(self.history) > MAX_HISTORY:
            self.history.pop(0)
            if self.history_index > 0:
                self.history_index -= 1

        self.history_slider.blockSignals(True)
        self.history_slider.setRange(0, len(self.history) - 1)
        self.history_slider.setEnabled(len(self.history) > 1)
        self.play_btn.setEnabled(len(self.history) > 1)
        if was_at_live and not self.play_timer.isActive():
            self.history_index = len(self.history) - 1
            self.history_slider.setValue(self.history_index)
        else:
            self.history_slider.setValue(max(0, min(self.history_index, len(self.history) - 1)))
        self.history_slider.blockSignals(False)

        if was_at_live and not self.play_timer.isActive():
            self._display_current_frame()

    def on_history_slider_changed(self, value: int):
        self.history_index = value
        self._display_current_frame()

    def toggle_play(self):
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setText("▶ Play")
        else:
            if len(self.history) < 2:
                return
            self.play_timer.start(PLAYBACK_FRAME_MS)
            self.play_btn.setText("⏸ Pause")

    def advance_history_frame(self):
        if not self.history:
            return
        self.history_index = (self.history_index + 1) % len(self.history)
        self.history_slider.blockSignals(True)
        self.history_slider.setValue(self.history_index)
        self.history_slider.blockSignals(False)
        self._display_current_frame()

    def _display_current_frame(self):
        if not self.history:
            return
        idx = max(0, min(self.history_index, len(self.history) - 1))
        overlays = self.history[idx]
        is_live = idx == len(self.history) - 1
        frame_tag = "live" if is_live else f"frame {idx + 1}/{len(self.history)}"
        time_str = " / ".join([ov.volume_time for ov in overlays])
        self.history_label.setText(f"{frame_tag} — {time_str}")
        self._emit_overlays(overlays)

    def _emit_overlays(self, overlays: list):
        product = self.current_product()
        requested_label = radar_source.PRODUCTS[product]["label"]
        
        payload_items = []
        meta_parts = []

        for overlay in overlays:
            prod_to_use = product
            note = None
            if prod_to_use not in overlay.products:
                note = overlay.product_notes.get(prod_to_use, "not available")
                prod_to_use = overlay.available_products[0]

            shown_label = radar_source.PRODUCTS[prod_to_use]["label"]
            tag = "LIVE" if overlay.source == "live" else "DEMO"

            payload_items.append({
                "png_b64": overlay.products[prod_to_use],
                "coordinates": overlay.coordinates,
                "station": overlay.station
            })

            if note:
                meta_parts.append(f"[{tag}] {overlay.station}: {requested_label} unavail ({note}) -> showing {shown_label}")
            else:
                meta_parts.append(f"[{tag}] {overlay.station} ({shown_label})")

        time_str = overlays[0].volume_time if overlays else ""
        meta = f"{' | '.join(meta_parts)} — {time_str}"

        self.bridge.overlaysReady.emit(json.dumps(payload_items), meta)

        # Get legend specs from radar_source
        legend_data = radar_source.get_legend(product)
        if isinstance(legend_data, dict):
            legend_data["product"] = product
            legend_data["title"] = requested_label
        else:
            legend_data = {"product": product, "title": requested_label}

        self.bridge.legendReady.emit(json.dumps(legend_data))
        self.status.showMessage(meta, 20000)

    def on_overlay_error(self, message: str):
        self.status.showMessage(f"refresh failed: {message}", 15000)

    def on_warnings_ready(self, geojson: dict):
        if "_error" in geojson:
            pass
        self.bridge.warningsReady.emit(json.dumps(geojson))


def main():
    start_local_server(ASSETS_DIR, HTTP_PORT)
    app = QApplication(sys.argv)
    if LOGO_PATH.exists():
        app.setWindowIcon(QIcon(str(LOGO_PATH)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()