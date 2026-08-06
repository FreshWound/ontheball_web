# ontheball — Changelog

Version-by-version history of what changed and why. See [README.md](README.md) for install/run instructions.

## What's new in v0.10.20 — hotfix: crash in on_history_backfill_ready

- Fixed a crash-on-startup regression from v0.10.19: a leftover duplicate
  status line in `on_history_backfill_ready()` survived the
  `_merge_frames_into_history()` refactor, still referencing the old
  `new_frames` variable name that no longer existed in that function's
  scope — `NameError` the moment a backfill actually completed. Caught
  by `py_compile` alone since that only checks syntax, not whether every
  name resolves at runtime; added `pyflakes` to the pre-ship check
  (confirmed it flags this exact bug pattern immediately) so this class
  of leftover-from-a-refactor bug gets caught before it ships, not after.

## What's new in v0.10.19 — faster tooltips, visible export progress, export frames actually kept

- Tooltip wake-up delay cut from Qt's ~700ms default to 150ms app-wide
  (`FastTooltipStyle`, a small `QProxyStyle` override) — the Shortcuts
  tooltip specifically was too sluggish for something meant to be a
  quick glance.
- Fixed the silent multi-minute gap during export: the transcode step
  (`imageio.get_writer` + `append_data()` per frame) blocks the Qt event
  loop, so any status message shown right before it started didn't
  actually get painted until the whole encode finished — looked exactly
  like the export had hung. Now calls `QApplication.processEvents()`
  after every frame during both the fetch-progress and the encode step,
  with real "Encoding 43%…" / "frame 12/20" text instead of one message
  that appears to just disappear. The fetch phase also got a repeating
  status re-assert every 4s (with elapsed-seconds shown) — a long fetch
  is exactly the kind of thing an unrelated status message (auto-refresh
  ticking elsewhere, a hover readout) could silently bury before.
- Fixed: the 20 volumes export pulls were used for the video and then
  discarded — the playback slider was still stuck at whatever 5-12
  frames it had before, none of the extra history stuck around.
  Extracted the merge-into-`self.history` logic (dedup + prepend +
  slider bookkeeping) that history backfill already did into a shared
  `_merge_frames_into_history()`, and export now calls it too before
  rendering. Bumped `MAX_HISTORY` from 12 to 30 so a full 20-frame export
  batch doesn't immediately get truncated back out by the cap the moment
  it's merged in.

## What's new in v0.10.18 — export playback as MP4, hotkey tooltip

- New **Export…** button next to the history slider. Pulls
  `EXPORT_FRAME_COUNT` (20) volumes — deliberately more than the
  in-session backfill's 5, since the export is meant to show a fuller
  stretch of storm evolution than the live playback slider needs day to
  day — via the same `HistoryBackfillWorker`/`_zip_station_frames()` path
  history backfill already uses, completely independent of
  `self.history`. Then steps through those frames on the actual map
  (basemap, radar overlay, warning polygons, legend — everything you'd
  see live), screenshotting each one via `QWebEngineView.grab()`, and
  encodes them into an MP4 with `imageio` + `imageio-ffmpeg`.
- Deliberately **not GIF**: color banding on radar gradients would look
  bad, and video compresses much better for something with this much
  smooth gradient. Deliberately **not system ffmpeg** either —
  `imageio-ffmpeg` bundles a static ffmpeg binary right in the pip
  wheel, so `requirements.txt` stays the single source of truth and
  nobody testing this needs `apt install ffmpeg` first. Verified the
  full screenshot → numpy → MP4 pipeline end-to-end in a headless Qt
  session before shipping this (odd-dimension trimming for H.264's
  even-dimension requirement, and reading the resulting file back
  frame-by-frame to confirm it's genuinely valid) — the one piece I
  couldn't verify here is `QWebEngineView.grab()` specifically on real
  hardware with a real GPU/compositor, so that's the first thing worth
  checking once this is actually running.
- Saves to `~/Downloads/ontheball-exports/` by default (deliberately
  **not** inside the repo — a stray `git add .` down the road could
  otherwise commit MP4s into version control) via a normal save dialog,
  so it's easy to redirect per-export if wanted. Opens the containing
  folder once the export finishes.
- Added a "⌨ Shortcuts" label (top controls row) with a rich tooltip
  listing every hotkey — they've been fully invisible to anyone who
  doesn't already know they exist since v0.10.6 added the first ones.
- New dependencies: `imageio`, `imageio-ffmpeg` — both added to
  `requirements.txt`.

## What's new in v0.10.17 — Home Location no longer selects stations

- Home Location is now purely a map marker plus the distance-from-cursor
  readout — it no longer loads the 3 closest stations as a multi-station
  view. Shift-click multi-select already covers picking more than one
  station, and having two different features both drive station
  selection (Home's closest-3, plus manual multi-select fighting over
  who takes priority) was redundant complexity now that multi-select
  exists.
- Setting or clearing Home no longer touches the current view at all —
  no history reset, no refetch. It just places/removes the marker and
  starts/stops the "X mi from home" readout as the cursor moves.
- Removed `self.home_active_stations`, `HOME_STATION_COUNT`, and
  `radar_source.find_closest_stations()` (now fully unused) rather than
  leaving them as dead code. `_active_stations()` (shared by
  `refresh_now()`, history backfill, and the live-fetch staleness checks
  added in v0.10.16) simplifies to manual multi-select > single station,
  dropping the home-priority tier entirely.
- Went through every comment/docstring that mentioned "Home mode" or
  "Home/multi-select" as a station-selecting concept and updated them —
  didn't want to leave stale documentation the way the CHANGELOG itself
  went stale earlier in this project.

## What's new in v0.10.16 — the "stuck on default station" bug, and why history backfill was slowing switches down

- Fixed: `refresh_now()`'s "don't start a second fetch while one's
  running" guard was silently *dropping* the request instead of
  remembering it — same class of bug as the v0.10.13 backfill fix, just
  never applied to the primary live-fetch path. With Auto-refresh
  defaulting on and each grid+render taking 10-25s, switching stations
  while a fetch was already in flight meant no fetch ever got dispatched
  for the new station at all — Auto-refresh's own separate busy-guard
  let backfill fire fine and pull the new station's *older* history, but
  nothing asked for its live volume, so live silently stayed on whatever
  station you switched away from until you happened to catch it between
  fetches. `refresh_now()` now sets a pending flag and retries itself
  once the in-flight fetch finishes, reading whatever's actually
  selected *then* (mirrors `_start_history_backfill()`'s existing fix).
- Added a belt-and-suspenders guard in `on_overlays_ready()`: even with
  the fix above, an old in-flight fetch (for a station you've since
  switched away from) can still land after the fact. Any overlay whose
  station isn't part of the currently active selection now gets filtered
  out before it's ever appended to history or displayed, instead of
  silently corrupting the current view.
- Also fixed the resulting slowness: history backfill was firing *in
  parallel* with the live fetch at every station-change call site — one
  live volume plus five backfill volumes, all grid+rendering at once,
  competing for the same CPU right when you're waiting to see the
  station you just picked. Backfill now only starts from the end of
  `on_overlays_ready()`, strictly *after* the live frame has actually
  landed and rendered, rather than racing it. `on_js_ready()` no longer
  fires an immediate backfill in parallel with the very first load either
  — same fix, applied to startup.
- Still open: the blank-history-frame issue from the screenshots. This
  round of fixes targets the station-switching race specifically: your
  repro was single-station the whole time, so it's a separate root cause
  I haven't pinned down yet. Worth retesting on this version in case it
  was a downstream symptom of the same contention, but if it's still
  reproducible, the debug-hover tool plus the terminal timing output
  around that specific volume would be the next thing to look at.

## What's new in v0.10.15 — duplicate history frame on a repeat live poll

- Fixed: `on_overlays_ready()` (every live refresh, manual or
  auto-refresh) appended whatever it fetched as a new history frame
  unconditionally — if a refresh landed before the station's actual
  latest volume had advanced (NEXRAD scan timing isn't perfectly
  punctual; the measured-cadence auto-refresh interval is a good guess,
  not a guarantee), the same volume got appended a second time,
  showing up as an extra "step back" that's actually identical to live.
- Now compares the incoming frame's (station, volume_time) against the
  current last frame before appending — same underlying-identity
  comparison the backfill dedup already uses (`_base_volume_time()`) —
  and skips the append if it's a repeat.

## What's new in v0.10.14 — fixed the flicker on every history step / product / tilt change

- Every overlay update (history-slider step, playback frame, product
  switch, tilt change, arrow-key stepping) was removing all radar
  layers/sources and re-adding them fresh — `map.removeLayer()` +
  `map.removeSource()` then `map.addSource()` + `map.addLayer()`. That
  briefly leaves the map with zero radar layers while the new PNG data
  URI decodes, which reads as a sharp flicker/flash, worse the faster you
  step (arrow keys, play mode).
- Fixed in `assets/map.html`'s `renderOverlayItems()`: when a source for
  a given slot already exists, it now calls MapLibre's
  `ImageSource.updateImage({url, coordinates})` to swap the texture in
  place instead of tearing the layer down — confirmed this method exists
  in the bundled MapLibre 3.6.2. The layer never leaves the map, so there's
  nothing to flash. Sources/layers are only actually removed and rebuilt
  when the number of overlay slots changes (switching between
  single-station and multi-station/Home views) — a rarer transition where
  a brief rebuild is expected anyway.

## What's new in v0.10.13 — backfill request during startup's in-flight fetch was silently dropped

- Fixed: switching stations right after launch (or during any backfill
  already in flight — a fast multi-select/Home switch could hit this too)
  didn't load that station's history — you had to reselect it once the
  first fetch finished for it to actually pull. Auto-refresh defaulting
  on means a backfill kicks off immediately at startup for the default
  station; `_start_history_backfill()`'s "don't start a second worker
  while one's running" guard was just discarding the newer request
  outright instead of remembering it.
- `_start_history_backfill()` now sets a pending flag when it's asked to
  run while busy, and `_on_history_backfill_worker_finished()` (hooked to
  the worker's `finished` signal) re-fires it once the in-flight fetch
  completes — for whatever station/selection is actually active *then*,
  not whatever triggered the original request. Any number of rapid
  switches while busy collapse into one deferred backfill for the final
  selection, rather than queuing one per switch.

## What's new in v0.10.12 — ` toggles radar opacity

- Backtick (`` ` ``) instantly blanks the radar overlay (opacity 0) to peek
  at the bare map underneath, then restores it to whatever opacity you
  actually had dialed in — not just a fixed default. Same hotkey-relay
  path as the product/playback keys (`assets/map.html` → `reportHotkey` →
  `Bridge.hotkeyPressed` → `MainWindow.on_hotkey`).

## What's new in v0.10.11 — history backfill now works for multi-select and Home

- Auto-refresh's history backfill previously only worked in single-station
  view — shift-click multi-select and Home both explicitly skipped it,
  leaving you with a single frame until enough real refresh cycles built
  history up manually.
- `HistoryBackfillWorker` now takes a list of stations (single-station is
  just a list of one) and fetches each station's recent volumes in
  parallel, same thread-pool pattern `MultiRadarFetchWorker` already uses
  for live fetches. `on_history_backfill_ready()` zips each station's
  results together by position — frame *i* across stations — into
  frames, the same "whichever stations were fetched together count as one
  time-slice" convention live multi-station refreshes already use, rather
  than trying to time-align by actual scan timestamp.
- Worth knowing: stations don't necessarily share a scan cadence, so a
  backfilled frame's per-station volumes can end up a few minutes further
  apart than a live-refreshed frame's would — same underlying imprecision
  live multi-station frames already have, just compounded a bit more
  since each station's own cadence runs independently the further back
  you go, instead of being anchored fresh every poll cycle.
- Backfill now also fires (when Auto-refresh is on) after finishing a
  shift-click multi-select and after Set Home/Clear Home — previously it
  only fired on the checkbox's own toggle, a single-station dropdown pick,
  or a map-marker click.

## What's new in v0.10.10 — history-backfill dedup was silently swallowing valid re-toggles

- Fixed: switching Tilt to a specific angle and back to Composite, then
  re-toggling Auto-refresh to try to backfill history again, appeared to
  do nothing — only changing station would "fix" it. Two things
  compounded: (1) `render_composite()`/`render_tilt()` tag whichever frame
  you're viewing with a cosmetic `" (composite)"`/`" (X.X° tilt)"` suffix
  for the label, which `on_history_backfill_ready()`'s dedup was
  comparing literally instead of against the real volume identity; (2) a
  successful backfill that legitimately finds nothing new (because no new
  NEXRAD volume has landed on S3 yet — scans are ~5-10 min apart) returned
  completely silently, indistinguishable from an actual failure.
- Added `_base_volume_time()` to strip that display suffix before
  comparing, and status-bar messages for both "came back empty" and
  "already have the recent volumes, nothing new yet" so a no-op backfill
  reads as a no-op instead of looking broken.

## What's new in v0.10.9 — smarter defaults, nationwide alerts on startup

- Auto-refresh and Reduce smoothing now default to **on** instead of off.
  Auto-refresh defaulting on meant the history-backfill trigger needed a
  real startup path too (previously only fired from a checkbox toggle or
  a station change) — `on_js_ready()` now calls the same
  `on_auto_refresh_toggled(True)` logic directly once the page/web channel
  is actually ready, rather than relying on the checkbox's own `toggled`
  signal firing during construction (which would've raced ahead of the
  web channel being ready to receive anything).
- NWS alerts now load nationwide on first launch instead of scoped to
  whichever station happens to be selected first — so you can see where
  the weather actually is before picking where to look closer. Turns out
  this cost nothing extra: `api.weather.gov/alerts/active` was already
  returning every active alert in the country in one call
  (`_fetch_active_alerts_raw()`); the station-scoped version just threw
  away anything outside ~265km afterward. `fetch_all_warnings()` skips
  that distance filter for the first load only — every refresh after
  that (auto-refresh tick, manual refresh, station change) goes back to
  the normal station-scoped view, so the map doesn't stay cluttered with
  nationwide polygons once you're actively looking at one area.

## What's new in v0.10.8 — auto-refresh backfill on station change, frame time badge

- Fixed: switching stations while Auto-refresh was already checked didn't
  pull history — backfill only fired on the checkbox's on-toggle, not on
  a station change. Now every station-change path (dropdown, map-marker
  click including the cached-overlay shortcut) checks the checkbox itself
  and backfills if it's on, instead of requiring an off/on toggle to
  re-trigger it.
- Added a frame-time badge (top-right, next to Home Location) showing
  either "🔴 LIVE — HH:MM UTC" or, when scrubbing history, the frame's
  scan time plus the gap to the previous step ("Δ +6 min vs previous
  step") — NEXRAD's actual volume cadence isn't perfectly even, so this
  makes the real time-per-step visible instead of just an ordinal frame
  count.

## What's new in v0.10.7 — auto-refresh now backfills a bit of history

- Turning on Auto-refresh now also pulls the ~5 volumes just before the
  current one for the active station (single-station view only — Home/
  multi-select doesn't try this, same restriction as Tilt), so the
  playback slider starts with real context instead of a single frame you
  have to wait several refresh cycles to build up.
- No new backend/service needed: `_latest_key()` was already listing every
  volume key for the station/day just to take the last one — the prior
  volumes were sitting unused in that same S3 listing. `get_recent_overlays()`
  reuses that listing (`_list_keys_for_station()`) and downloads/grids the
  handful before the latest via a small thread pool, same pattern as the
  Home multi-station fetch.
- One-time pull per auto-refresh-on toggle, not per refresh cycle. Dedupes
  against whatever's already in session history (by volume_time) so
  toggling it on and off doesn't stack duplicate frames, and drops the
  result if the station changed while the fetch was in flight.

## What's new in v0.10.6 — product/playback hotkeys

- `1`-`4` or `B`/`V`/`C`/`Z` jump straight to a product (Reflectivity/Base
  Velocity/Correlation Coefficient/ZDR); Left/Right arrows step playback
  one frame at a time (pauses auto-play first if it's running, clamps at
  either end rather than wrapping).
- Handled on the JS side (`assets/map.html`) since that's where keyboard
  focus actually lives, relayed to Python over the existing QWebChannel
  bridge pattern. Had to disable MapLibre's built-in keyboard pan
  (`map.keyboard.disable()`) since its arrow-key handler is attached to the
  map container and fires before a document-level listener ever sees the
  event — same reasoning as the earlier `boxZoom.disable()` fix.

## What's new in v0.10.5 — real gridding-gap bug found (min_radius vs grid spacing)

- Root cause found for a serious one: KIND 0.9° tilt showed literally nothing
  9 miles from the radar, at a spot GR2Analyst confirmed had a real, intense
  storm (60+ dbz, imaged all the way up through its vertical profile). Math
  check: `GRID_CELLS=460` over the 920km-wide grid gives ~2004m spacing
  between grid points, but `min_radius` (250m Detail / 1000m Range) was
  smaller than *half* that spacing in both modes. Py-ART's `dist_beam` ROI
  can legitimately find zero gates within that small a search radius around
  a grid point — even sitting inside a real storm — and that grid point just
  comes back masked/empty. This is a geometry-driven gap, distinct from both
  the earlier range-clipping bug and the oversmoothing tradeoff; it likely
  explains the scattered black-hole/checkerboard patches seen in several
  earlier screenshots too.
- Fix: Range mode's `min_radius` bumped to 1300 (safely above its ~1002m
  half-spacing). Detail mode now grids its own smaller, tighter extent
  (150km instead of sharing Range mode's 460km) — matches its "prioritize
  accuracy over range" purpose, gives it ~654m spacing, and lets its
  `min_radius` (500) clear that safely too, without needing a big radius
  that would undercut the whole point of Detail mode.
- This required `RadarOverlay` to record which `grid_range_m` it was
  actually gridded at (`sample_value` was reading a single global constant
  before — harmless while both modes shared one range, but would have been
  a real bug the moment they diverged). Verified with a round-trip test:
  pick a known grid cell, convert to lat/lon, convert back, confirm it lands
  on the same cell, for both 150km and 460km grids.
- Added a debug print on hover (station, product, lat/lon, value,
  grid_range_m, detail_mode) so a future mismatch report comes with real
  diagnostic data instead of a screenshot to reverse-engineer from. Gated
  behind a new "Debug hover" checkbox (off by default) — mouse movement
  fires constantly, so this only prints while explicitly turned on right
  before reproducing an issue, then off again.
- Not resolved: the specific "cursor on a solid orange block, legend reads
  10.28 dBZ" mismatch from the 3:45 PM KIND screenshot. Traced the full
  pipeline for it — same array feeds both the PNG and the hover-lookup (so
  it's not a stale-data split), and a round-trip georeferencing test showed
  the row/col math is internally self-consistent — so this specific report
  is not yet explained by anything found in this pass. Worth revisiting
  once the hover debug print is in place; if it turns up on the next
  session it should print a very telling row/col/value combination.

## What's new in v0.10.4 — likely fix for the shading/legend mismatch

- Found a real cause for the "cursor visually on orange, legend reads a blue
  value" mismatch reported against KTLH: MapLibre's image/raster sources
  default to bilinear ('linear') texture filtering, and we'd never set
  `raster-resampling`. With GRID_CELLS still at 460 over the now-doubled
  460km range, each grid cell covers ~2km — coarse enough that at a normal
  zoom level, the browser is smoothly blending colors between neighboring
  cells for display, while the legend hover-readout (`sample_value`) reports
  the exact underlying cell's raw, un-blended value. Near a strong edge
  (e.g. the boundary of an isolated storm cell) those two can disagree by a
  lot — which lines up with what got reported (visually orange from
  blending toward a nearby high-value cell, while the true nearest-cell
  value was a real ~15.59 dBZ, solidly in the cyan/blue range on our
  anchors). Set `'raster-resampling': 'nearest'` on the radar raster layer
  in map.html so the displayed color always matches the true underlying grid
  cell — the tradeoff is the map now looks blockier/more pixelated at close
  zoom (which, worth noting, is closer to how GR2Analyst's own super-res
  imagery actually looks, not smoother).
- Not yet confirmed: whether this fully explains the reported mismatch, or
  whether there's an additional coordinate/sampling bug underneath it. Given
  the size of the discrepancy in the screenshot, worth a direct before/after
  comparison against the same spot once this build is running.
- Also reviewed the 0.9° tilt vs. GR2Analyst comparison from the same
  session: otb showed a much smaller area of light-rain coverage around the
  storm core than GR2. Best guess pending confirmation: this may just be
  "Reduce smoothing" doing exactly what it's for — Detail mode's tight ROI
  (h_factor=1.0) won't reach out to catch widespread low-reflectivity
  stratiform returns the way Range mode's wider ROI does. If the checkbox
  was off in that screenshot, this needs a fresh look instead.

## What's new in v0.10.3 — "Reduce smoothing" toggle, Kokomo mystery solved

- New checkbox next to Auto-refresh: **Reduce smoothing**. Off (default) uses
  the wider `dist_beam` ROI from v0.10.2 (`h_factor=3.0`, `min_radius=1000`) —
  better far-range coverage, some blurring of small/isolated cells. On uses
  Py-ART's own defaults (`h_factor=1.0`, `min_radius=250`) — sharp, true-to-
  source detail, but real data may drop out sooner at long range. Toggling
  re-renders instantly from the already-cached raw radar object in single-
  station view (no new S3 fetch); falls back to a full refresh in multi-
  station (Home/manual-select) view, which has no equivalent cached re-render
  path yet.
- Root cause found for the "Kokomo cell missing under multi-station select"
  mystery from v0.10.2: side-by-side against NWS's radar.weather.gov (real
  distinct storm cells near Kokomo/Converse/Greentown) vs. otb's composite at
  a similar time (a smooth blob, no cell structure at all) confirmed this was
  never a multi-station merge bug — it's the same `h_factor` oversmoothing
  hitting an isolated cell hard enough to erase it. The single-station-only
  comparison originally requested to isolate the multi-station theory turned
  out not to be needed once this evidence came in.
- Open items noted, not yet started: a possible shading/legend mismatch
  (hovering a visually-yellow map area showed a blue-range value highlighted
  on the legend) — code review of the hover-sampling and colormap pipeline
  didn't turn up an obvious bug, but couldn't be reproduced against the
  screenshots provided since the weather had changed; a toggle to remove
  smoothing (**done above**); playback "blink" during frame transitions,
  flagged by Fresh as likely a multi-day fix; nearest-surface-temperature
  hover pulled from Wunderground.

## What's new in v0.10.2 — real NEXRAD range, less short-range clipping

- Grid range doubled: `GRID_RANGE_M` was 230km (~124nm), about half of NEXRAD's
  actual base reflectivity range (~248nm/459km). Now 460km, with `GRID_CELLS`
  left at 460 so pixel size grows (~1km → ~2km) rather than compute scaling up —
  Barnes interpolation cost stays roughly flat.
- Real short-range cause found and fixed: our grid is a single flat z-layer that
  sits at z=0 (ground level) — confirmed via `numpy.linspace(0,1000,1) == [0.]`,
  so the `(0,1000)` z-window barely mattered. A beam's actual height climbs with
  range (elevation angle + earth curvature), so past a certain distance real
  gates were too far above that z=0 layer for Py-ART's default `dist_beam` ROI
  (`h_factor=1.0`) to still include them — they silently dropped out. This hit
  single-tilt renders even harder than composite, since composite still had
  low tilts filling gaps. Fixed by passing `roi_func="dist_beam"` explicitly
  with a wider `h_factor` (settled on 3.0 after comparing 4.0/2.0 against real
  KIWX storms side-by-side with GR2Analyst — 4.0 fixed range/tilt but visibly
  oversmoothed, spreading reflectivity into areas with no real return) and
  `min_radius=1000.0`, on both the shared reflectivity/velocity/ZDR grid pass
  and the separate CC pass.
- Known tradeoff from this fix: wider ROI blends in more, farther-away gates
  per pixel, which can soften/smear real detail on nearby cells (observed on
  a Kokomo-area cell under KIWX). Candidate follow-up: a toggle to reduce/
  disable this smoothing for people who want closer-to-raw detail over
  maximum range. Not yet built.
- Also noted, not yet root-caused: a storm cell near Kokomo, IN wasn't showing
  up even with both KIWX and KIND selected via multi-station shift-select.
  Needs investigation — unclear yet whether it's related to the ROI change,
  the multi-station blending logic, or something else.

## What's new in v0.10.1 — fix every station falling back to demo data

- v0.10.0's tilt caching introduced a real bug: `volume_time` was being
  used (to populate the raw-radar cache) before it was actually assigned.
  That threw an `UnboundLocalError` on every single live fetch, which the
  broad error handler quietly caught and replaced with synthetic demo data
  — every station appeared to load, but was showing fake data with the
  error message tacked onto the timestamp. Fixed by computing `volume_time`
  before it's used.
- Also fixed: picking any specific Tilt (not Composite) showed real live
  data but mislabeled it "[DEMO]" in the status bar. `render_tilt()` tags
  its overlays `source="live-tilt"` (so the Tilt dropdown can recognize
  them), but the LIVE/DEMO status label only checked for the exact string
  `"live"` — anything else, including a perfectly real tilt render, got
  called DEMO. Now treats `"live-tilt"` as LIVE too.
- Also fixed: some tilts (e.g. 0.9°) could show a real storm as nearly
  empty even though a taller-storm test ruled out simple beam-overshoot.
  Root cause: split-cut VCPs scan the lowest tilt(s) twice — once at low
  PRF for full-range reflectivity, once at high PRF for velocity/dual-pol,
  whose much shorter unambiguous range range-folds/masks reflectivity
  beyond it. `render_tilt()` was only extracting the first duplicate sweep
  at a given angle, which could silently grab the range-limited one. Now
  extracts every sweep sharing that angle together, so whichever one
  actually has full-range data for a given product gets used — same
  principle the full composite already relies on.

## What's new in v0.10.0 — Tilt selection (single-station view)

- Added a Tilt dropdown next to Product. Picking a specific elevation angle
  re-grids just that one sweep from the volume instead of the usual
  all-sweeps composite — and since the raw decoded volume is now cached in
  memory per station after its first fetch, switching tilts (in either
  direction, including back to Composite) needs no new S3 fetch at all.
  Available tilts are read from the actual volume's scan pattern (VCP),
  so the list reflects what that station really scanned, not a fixed
  assumption — NEXRAD VCPs commonly scan the lowest tilt(s) twice for
  reflectivity vs. velocity/dual-pol, so repeated angles are deduplicated
  to one pick each.
- Scoped to single-station view only — Tilt is disabled for Home mode and
  shift-click multi-select, since each station in those views can be
  running a different VCP with a different set of available angles, so
  there's no one Tilt list that would apply to all of them at once.
- Memory tradeoff worth knowing: the cached raw volume per station stays
  in memory for the life of the session (only replaced when that station
  refreshes again), not evicted after a timeout. Fine for a handful of
  stations across a session; could add an eviction policy later if it
  becomes a real problem.

## What's new in v0.9.2 — batch shift-click fetches on Shift release

- Selecting several stations previously fetched after every single
  shift-click, so clicking a second marker while the first was still
  downloading got silently dropped (stuck on "still fetching"). Shift-click
  now only toggles the selection and marker highlight immediately; the
  actual fetch is deferred until Shift is released, so picking multiple
  stations in one gesture batches into a single fetch of whatever's
  missing instead of racing itself.

## What's new in v0.9.1 — fix shift-click not registering / stray zoom-in

- Shift-click wasn't working, and occasionally the map would snap-zoom in
  extremely far instead. Root cause: MapLibre's built-in shift+drag
  box-zoom handler grabs `mousedown` before our marker's `click` listener
  ever fires, and interprets a near-zero-size drag as a valid (tiny) zoom
  target. Disabled `map.boxZoom` outright — this app has no use for it, and
  it directly conflicted with shift being our multi-select modifier.

## What's new in v0.9.0 — shift-click multi-station select

- Shift-click a station marker to toggle it in/out of a manual multi-station
  selection (markers highlight green while selected) — overrides Home mode
  and the dropdown while active. Only newly-added stations actually fetch
  from S3; anything already cached in the current frame is reused instantly,
  same principle as the v0.8.2 already-loaded-station fix. A plain click on
  any marker, picking from the Station dropdown, or setting Home all cleanly
  clear the selection and drop back to single/Home-mode behavior.

## What's new in v0.8.4 — reuse S3 client; timing confirms grid step dominates

- Real timing data from v0.8.3 showed `grid+render` — not the S3 download —
  is the larger and more variable cost per station load (5s for a quiet
  KIWX scan, up to 12s for a KLOT volume with a domain-filling storm).
  Py-ART's Barnes interpolation cost scales with how much actual weather is
  in the scan, which is expected/proportional, not a bug. It's also
  single-threaded, so it can sit there for several seconds while "total"
  CPU usage looks low on a many-core machine — one fully-pegged core just
  doesn't move the total-CPU needle much on a 12-thread CPU.
- Real fixable overhead the timing did expose: `_s3_client()` was
  constructing a brand-new boto3 client (fresh TCP/TLS handshake) twice per
  station load — once for the key listing, once for the download. Now
  cached at module level and reused across calls/stations.

## What's new in v0.8.3 — per-stage load timing (diagnostic only)

- `get_latest_overlay()` now prints a `[timing]` line to the terminal for
  every station fetch, breaking down where the time actually goes:
  finding the latest key on S3, downloading the volume (with file size),
  parsing it with Py-ART, and gridding/rendering. No behavior change —
  this is purely to answer "why did that station take 22 seconds" with
  real numbers instead of guessing, before deciding what (if anything)
  to optimize next.

## What's new in v0.8.2 — instant switch to already-loaded stations

- Clicking a station marker that's already rendered on screen (e.g. one of
  the up-to-3 Home-mode stations) now reuses that already-downloaded,
  already-gridded overlay instead of firing a brand-new S3 fetch. Previously
  every station click — even one you could already see — dropped out of
  Home mode and re-ran the whole download-and-grid pipeline from scratch.
  Clicking a station that *isn't* already loaded still fetches normally.

## What's new in v0.8.1 — merged grid passes to cut station-click delay

- **`_grid_and_render` now does 2 `grid_from_radars` passes instead of 4.**
  Reflectivity, Base Velocity, and ZDR all use identical gate criteria (just
  the reflectivity floor), so they're now gridded together in one shared
  pass instead of three separate ones — that interpolation step is the
  expensive part of a station click, so this should meaningfully cut the
  delay between clicking a station and seeing it update.
  Correlation Coefficient stays on its own dedicated pass, since its extra
  low-quality-gate exclusion would otherwise incorrectly mask valid
  reflectivity/velocity/ZDR data wherever only CC happened to be noisy —
  merging it in would trade speed for a real data-quality regression, so it
  didn't.

## What's new in v0.8.0 — ZDR product, legend hover-highlight, parallel Home fetch

- **Differential Reflectivity (ZDR) added** as a fourth product alongside
  Reflectivity, Base Velocity, and Correlation Coefficient — same
  dedicated-gatefilter treatment as the others (its own floor filter, no
  shared-filter cross-contamination), shows up automatically in the
  Product dropdown, and falls back gracefully with a note if a volume
  was scanned pre-dual-pol or nothing passed quality filtering.
- **Legend hover-highlight**: moving the cursor over the map now samples
  the real gridded value at that point (re-projected back onto the same
  grid the radar was rendered on, not a color-matching guess) and shows
  a tick + value readout sliding along the legend gradient for whichever
  product is currently selected — the on-map equivalent of GR2Analyst's
  cursor readout.
- **Home-mode fetches now run in parallel.** `MultiRadarFetchWorker`
  previously fetched/gridded each of the 3 Home stations one after another;
  it now uses a small `ThreadPoolExecutor` so S3 downloads and Py-ART
  gridding for multiple stations overlap instead of serializing, while
  preserving closest-first station order in the result.

## What's new in v0.7.8 — Clear Home + cadence-based auto-refresh

- **Clear Home button** next to Set Home — resets home lat/lon, drops
  back to whatever's in the Station dropdown, clears the distance
  readout, and removes the home marker from the map. Also cancels a
  pending "click the map" arm if you hit Clear Home mid-selection.
- **Auto-refresh replaced with a simple on/off checkbox.** Instead of
  picking a fixed 2/5/10 min interval, it now measures the actual gap
  between consecutive volumes for whatever station(s) are active
  (parsed straight from each volume's own timestamp) and refreshes on
  that real cadence, plus a 30-second buffer since a volume takes a
  moment to actually land in S3 after the scan finishes. For a
  multi-station Home view, it uses the fastest-cycling station among
  them so a quicker site never gets missed. Starts at a 5-minute guess
  until two real volumes come in to measure from, then keeps adjusting
  if a station's mode/cadence changes (e.g. switching between clear-air
  and precip VCPs).

## What's new in v0.7.7 — station-click regression fixed + click-to-set-home

**Bug fixed**: v0.7.6 broke clickable radar stations entirely. Cause: the
old `addHomeMarker()` function got renamed to `updateHomeMarker(lat, lon)`,
but a leftover call to the old name (`addHomeMarker();`, no arguments)
was still sitting inside the `stationsReady` handler — right before the
loop that actually creates the station markers. That call threw a
`ReferenceError` every time the station list loaded, which aborted the
rest of the handler before it ever reached the marker-creation loop, so
no station markers got created at all. Removed the stale call.

**Click-to-set-home**: clicking **Set Home** with both lat/lon fields
left blank now arms a "click the map" mode (cursor becomes a crosshair,
button label changes) — the next map click sets that as home, fills the
lat/lon fields in for reference, and loads the closest 3 stations, same
as manual entry. Click **Set Home** again while armed to cancel. Typing
exact coordinates into the fields still works exactly as before, if that's
ever preferred over clicking.

## What's new in v0.7.6 — settable home location + distance readout

- **Removed the fixed HOME (Dual Radar: KGRR + KIWX) preset.** Home
  location is now entered as lat/lon in the controls each session (not
  saved to disk — intentional, so it's set fresh every time the app
  loads). Hitting **Set Home** finds and loads the 3 closest stations
  automatically (`radar_source.find_closest_stations`), reusing the same
  multi-station composite rendering the old fixed preset used — nothing
  changed about how multiple overlays actually render, just how the
  station list gets picked.
- Manually selecting a station from the dropdown (or clicking one on the
  map) properly overrides home mode and goes back to single-station view.
- **Distance-from-home readout** in the status bar (bottom right),
  live-updating in miles based on wherever the cursor is over the map.
  Only appears once a home location has been set.
- A home marker (⌂) appears on the map once set, at the actual entered
  coordinates instead of a fixed location.
- README reorganized: Install and Run are now the first two sections,
  right after the title — a maintenance note is left in the file itself
  asking future edits to keep it that way.

## What's new in v0.7.5 — merging in a lot of independent progress

Versions 0.7.0–0.7.4 were built independently (while waiting on a
context reset) and represent a big jump forward:

- **All ~153 US NEXRAD sites** (CONUS + Alaska + Hawaii/Pacific + Puerto
  Rico) — the full network, not just a curated subset.
- **HOME dual-radar view** — a new "HOME (Dual Radar: KGRR + KIWX)"
  option in the Station dropdown that fetches and overlays both
  stations at once (multi-station composite rendering, with the map
  layer/source logic reworked to support any number of simultaneous
  overlays via indexed `radar-layer-N` / `radar-src-N` sources).
- **Three fully independent gatefilters** (reflectivity, velocity,
  correlation coefficient) instead of one shared filter between velocity
  and CC — a cleaner, more robust version of the fix that was in
  progress for the velocity/CC cross-contamination risk. Each product
  now grids separately with its own filter, so there's no way for one
  to affect another.
- **Velocity dealiasing, refined further** — same approach as before
  (Py-ART's region-based dealiasing, decoupled from the shared filter),
  now paired with the fully independent velocity gatefilter above.
- **NWS warnings**: on-map toggle checkbox, color-coded by event type,
  click-for-popup with severity/expiration.
- Switched the basemap source to CARTO (dark-matter / positron styles)
  and reworked station markers to real `maplibregl.Marker` DOM elements
  instead of a GeoJSON circle layer — markers now survive basemap style
  switches automatically (a real upside: they don't need reattaching).
- App branding footer, on-map layer toggle, default station changed to
  KIWX, default opacity changed to 30%.

**Bug found and fixed in this version**: radar overlay and NWS warnings
were *not* surviving a basemap (Light/Dark) switch — `map.setStyle()`
wipes every custom source/layer, and nothing was re-adding them
afterward, so switching basemaps silently blanked the radar until the
next refresh. (Station markers were unaffected — they're DOM elements,
not tied to the map style, which is why this wasn't obvious.) Fixed by
caching the last-received overlay/warnings data and replaying it once
the new style finishes loading.

**Local JS assets**: `map.html` loads `qwebchannel.js`, `maplibre-gl.js`,
and `maplibre-gl.css` from `assets/js/` — confirmed present and
committed to the repo (`assets/js/qwebchannel.js`, `assets/js/maplibre-gl.js`,
`assets/js/maplibre-gl.css`), so a fresh clone of the repo has everything
it needs with no separate download step.

## What's new in v0.6.2 — velocity dealiasing fix, round 2

v0.6.1's dealiasing didn't actually fix the reported symptom — Base
Velocity still showed just a speck at the radar site. Found two real
issues in that code:

1. **The dealiasing call was passed the same shared gatefilter used
   afterward for gridding both velocity and correlation coefficient.**
   GateFilter objects are mutable, and it isn't clearly documented
   whether `dealias_region_based` modifies the one it's given. If it
   does, that could explain why correlation coefficient/velocity
   sometimes came back completely empty on a scan that clearly had real
   weather on it (reflectivity rendered fine, since it uses its own
   independent filter, untouched by this). Fixed by decoupling
   entirely: dealiasing now uses `gatefilter=False` (Py-ART's own
   documented default — consider every gate during unfolding), and
   never touches the shared filter. The shared filter still does its
   job at the gridding step right after, so noisy gates still get
   excluded from the final output regardless.
2. **Failures were silently swallowed.** The try/except around
   dealiasing caught any error and just fell back to raw velocity with
   no record of what happened — meaning if dealiasing was failing
   every single time, there was no way to tell from the app itself.
   Now it prints the actual error to the terminal if it fails, so if
   this still isn't fixed, we'll have a concrete error message instead
   of another guess.

**Worth checking**: if Base Velocity is still just a speck after this,
look at the terminal output — a new line starting with `ontheball:
velocity dealiasing failed` would tell us exactly what's going wrong
and let us fix the real problem instead of guessing a third time.



Confirmed reflectivity's north-south flip fix worked (a real storm cell
now lines up with NWS's position). Base Velocity was still showing just
a tiny speck at the radar site on every station, which was the next
thing to fix.

Root cause: raw NEXRAD velocity is *aliased* — anything moving faster
than the radar's Nyquist velocity wraps around (folds) to the opposite
sign. Gridding that raw, still-folded data with a distance-weighted
scheme (like the one used here) is a known problem: right next to a
fold, a real velocity and its wrapped-around opposite (e.g. -63 mph
sitting next to +63 mph, which in reality are close in true wind speed)
get averaged together, producing near-garbage. Close to the radar,
velocities are small enough to rarely fold, so gridding looked fine
there — everywhere else, it fell apart. That matches the reported
symptom exactly, on every station.

Fixed by dealiasing (unfolding) velocity with Py-ART's region-based
algorithm before gridding, so the whole field is smooth and continuous
before the grid ever sees it. Wrapped in a try/except — if dealiasing
itself fails on some volume, it falls back to raw velocity rather than
breaking the product entirely.

**Worth testing next**: pull up Base Velocity on any station with real
wind/storm motion and see if it now fills in properly instead of just
showing a speck.

## What's new in v0.6.0 — the real bug: radar images were flipped north-south

This is likely the actual explanation for the last several rounds of
"missing cell" / "ghost storm" reports on Florida stations, and it
turned up because of a KDVN screenshot showing a storm rendering
southwest when it was really northwest — west correct, only north/south
swapped. That specific signature (one axis correct, one flipped) pointed
straight at an image-orientation bug rather than anything about the
weather data itself, and it checked out: confirmed directly with a test
image before shipping this, not just asserted.

The bug: Py-ART's grid arrays have row 0 as the *southernmost* row, but
the code writing them out to PNG never told matplotlib that — so it used
its default assumption (row 0 = top of the image). Combined with how
that image gets mapped onto the map's NW/NE/SE/SW corners, the whole
radar image was rendering mirrored top-to-bottom. East/west was never
affected, which is exactly why the KDVN storm's west-ness was right and
only its north/south was wrong.

This one line (`origin='lower'` on the image-save call) is probably why:
- The "ghost" storm band appearing north of KTBW was very likely a real
  storm actually south of KTBW, flipped to appear north.
- The "missing" Sarasota cell (south of KTBW) likely wasn't missing at
  all — it was probably rendering up near Ocala instead, which is where
  the ghost band showed up.

If that's right, the earlier fixes this session (removing the CC filter,
disabling the smoothing pass) may not have been fixing real problems so
much as reacting to symptoms of this one. Both of those changes are
staying as-is regardless — the CC-filter revert and lighter smoothing
were reasonable calls on their own merits (missing real precip is worse
than clutter noise; small cells shouldn't get blurred away) — but this
is very likely the actual root cause behind what you were seeing.

**This is worth re-testing above all the others** — pull up KTBW (or
any station) next time there's real weather and compare against
radar.weather.gov again. Everything should now line up in the correct
compass direction.

## What's new in v0.5.8 — small cells were being smoothed away

Reverting the CC filter in v0.5.7 didn't fully fix the Sarasota cell —
a same-station (KTBW vs KTBW) comparison against radar.weather.gov
still showed it missing. That pointed at something else entirely: a
light gaussian smoothing pass I'd added a while back purely to soften
grid-cell blockiness (a cosmetic "clean up this radar a little" request
from early on). A big widespread rain mass barely notices that blur —
it mostly just softens edges. But a small, compact, intense cell only a
few kilometers across gets its peak value blended down toward the
weaker/clear-air gates around it, which can meaningfully understate it
or hide it outright. That's exactly the pattern here: big stuff fine,
small isolated cells suppressed.

Disabled that smoothing entirely (`SMOOTH_SIGMA` set to 0). The radar
image may look a little blockier again at the pixel level, especially
zoomed in close — but showing real intensity accurately matters more
than cosmetic smoothness, and that's the actual point of this app.

**Worth re-testing**: same KTBW comparison — the Sarasota cell should
show up now with real intensity, not just as background haze.

## What's new in v0.5.7 — reverted the KPUX clutter filter

v0.5.6's lenient correlation-coefficient filter (meant to clean up ground
clutter on mountainous sites like KPUX) turned out to have a real cost:
comparing KMLB/KTBW (Florida) against radar.weather.gov showed a small
real convective cell near Sarasota that NWS displayed but ontheball
didn't — small or young cells can have noisier/marginal CC readings that
look similar to clutter on paper, so the filter was throwing out real
weak precipitation along with the clutter it was meant to catch.

Since Colorado/mountainous-terrain clutter tuning isn't the priority
(Fresh's primary sites are KGRR/KIWX, flat terrain with minimal ground
clutter to begin with), and missing real precipitation is a worse
failure mode than occasional clutter noise for this use case, that
filter is reverted. Reflectivity is back to floor-only (≥5 dBZ), same as
before v0.5.6. KPUX/mountainous sites may show clutter noise again — a
known, accepted tradeoff rather than something being actively chased
right now.

## What's new in v0.5.6 — ground clutter fix for mountainous sites (KPUX)

Removing despeckle in v0.5.5 fixed the KVWX fragmentation problem, but a
KPUX comparison against radar.weather.gov (Pueblo, CO — mountainous
terrain) showed a different problem: with *no* clutter rejection at all
on reflectivity, ground clutter showed up as broad speckled noise across
the whole view, drowning out the real storm cells that NWS showed
cleanly. Flat-terrain sites like KVWX don't have much ground clutter to
begin with, so removing despeckle looked like a clean win there — but
mountainous sites generate a lot more of it, and reflectivity had nothing
left to filter it out.

Fix: reflectivity now also uses a very lenient correlation-coefficient
floor (0.50) when that data's available, in addition to its dBZ floor.
This is a much more surgical tool than despeckle was — CC directly
measures the physical property that differs between real precipitation
(uniform, correlates near 1.0) and ground clutter (irregular, often well
below 0.5) — a **local per-gate check** based on actual physics, not a
**spatial neighbor-counting heuristic** that can accidentally fragment
real, contiguous storm structure the way despeckle did.

This threshold is intentionally much looser than the 0.80 one used for
velocity/CC — real rain only rarely drops this low over a real area, so
it shouldn't risk repeating the KPUX reflectivity-wipeout bug from a few
versions back where too strict a CC threshold nuked real rain entirely.

**Worth re-testing**: pull up KPUX again and compare against
radar.weather.gov the same way. The storm cores should look cleaner and
better-defined, with a lot less of that broad speckled haze. If clutter
is still showing through, or if real rain looks thinner than it should,
that's useful signal either way — this is fundamentally a tuning
tradeoff (there's no single threshold that's perfect on every terrain),
and real-world comparisons like these are exactly how to dial it in.

## What's new in v0.5.5 — coverage/intensity fix (KVWX vs radar.weather.gov)

Your KVWX side-by-side against the official NWS single-site radar showed
a real, same-source discrepancy: NWS showed one solid mass of moderate
rain, ontheball showed scattered disconnected patches of the same storm.
That pattern — real echo chopped into islands rather than just colored
differently — pointed straight at `despeckle_field`, which I'd added
early on to strip out radar noise.

The problem: despeckle finds "objects" (contiguous gate clusters) on the
**native polar grid** (azimuth × range bins), using only 4-directional
adjacency, and removes anything under a 10-gate threshold. A real,
geographically continuous rain shield is often texturally uneven from
ray to ray in that native representation — enough small natural dips to
get segmented into many "islands" that individually fall under the
threshold and get stripped, even though nothing is actually wrong with
the data. This is a plausible, well-supported mechanism for exactly what
you saw, and I couldn't find a way to make despeckle both keep doing its
job and stop doing this, so it's removed entirely.

Reflectivity's own filter is already just a floor (≥5 dBZ) and doesn't
depend on despeckle for noise control. Velocity/Correlation Coefficient
still get their stricter dual-pol gate filter (reflectivity floor +
correlation coefficient ≥ 0.80), which is a much more targeted way to
reject birds/insects/ground clutter than blanket despeckling anyway.

**Worth re-testing**: pull up KVWX (or any station with real weather)
again and compare against radar.weather.gov the same way — you should
now see one continuous area of rain instead of fragments. If some
genuine speckle noise creeps back in near the radar, that's a fair
trade to flag and revisit with something more targeted than a blanket
polar-grid despeckle.

## What's new in v0.5.4

- **Confirmed**: the v0.5.3 marker fix worked — `stations: 12 added ✓`
  showed up correctly on the real machine.
- **Fixed font warning noise**: with no font explicitly set, station
  labels fell back to MapLibre's SDK-default font name, which OpenFreeMap
  doesn't host — harmless (labels still rendered via a local substitute),
  but it spammed the terminal with 404 glyph-load warnings. Now
  explicitly uses `Noto Sans Regular`, which is what OpenFreeMap's styles
  actually host (confirmed — it's what the basemap's own labels use).
- **9 new stations**: Florida (Tampa Bay/KTBW, Melbourne/KMLB, Miami/KAMX,
  Jacksonville/KJAX) and Tornado Alley (Oklahoma City/KTLX, Wichita/KICT,
  Amarillo/KAMA, Dodge City/KDDC, Omaha/KOAX) — 21 stations total now.

## What's new in v0.5.3 — the actual station-marker fix

v0.5.2's font-removal theory turned out not to be the (whole) story —
markers still didn't show up, and the terminal log stayed silent on
both success and failure, which was the real clue.

The actual bug: marker/radar/warning creation was gated on
`map.isStyleLoaded()`, which is stricter than it sounds — it can report
`false` indefinitely while map tiles keep streaming in as you pan/zoom,
not just during initial load. Radar overlays "happened" to work anyway
because every refresh calls `ensureRadarLayer()` again, so it effectively
got many retries until one landed at a lucky moment. Station markers only
ever get sent **once** per app launch, so if that one attempt landed while
`isStyleLoaded()` was false, there was no second chance — it would just
silently never add the layer, ever, with nothing printed anywhere.

Fixed by tracking readiness ourselves: once the map fires its `load`
event one time, a `mapReady` flag is set and stays set — station/radar/
warning creation now checks that instead of re-querying the flakier
live tile-loading state every time.

Also added a small on-screen debug readout (top-right of the map) that
directly shows `stations: N added`, `stations: waiting for map`, or
`stations: ERROR — <message>` — no dependence on whether console
messages happen to reach the terminal (turns out plain `console.log`
likely doesn't, which is probably why v0.5.2's success-case logging
never showed up either way — switched that to `console.error` too,
which we know reaches the terminal from earlier logs).

## What's new in v0.5.1

- **Station markers made actually visible** — they were rendering, but
  in a light blue (`#3fa0ff`) that's nearly identical to lake/water tiles
  on the light basemap and to reflectivity's own blue tones — easy to
  mistake for background clutter rather than a clickable marker. Changed
  to a bold amber/gold (`#FFC107`) with a dark outline, made bigger
  (radius 6→9), and bolded the labels. Also made station markers
  layer-order-safe: they now explicitly re-raise themselves to the top
  of the map every time the radar or warnings layers are (re)created, so
  they can never end up hidden underneath either one regardless of what
  order things load in.

## What's new in v0.5.0

- **Legend** — bottom-right corner, updates automatically for whichever
  product is showing (Reflectivity/Base Velocity/Correlation Coefficient),
  with a gradient bar and min/max/unit labels.
- **Base Velocity is now in mph** instead of m/s (display range ±70 mph).
  Reflectivity stays in dBZ and Correlation Coefficient stays unitless —
  those are the standard units for those products, mph was specifically
  a velocity request.
- **NWS warning polygons** — active Tornado/Severe Thunderstorm/Flash
  Flood/Special Marine/Snow Squall/Dust Storm/Extreme Wind warnings,
  pulled free from api.weather.gov, shown as colored outlined polygons.
  Click one for a popup with the headline and expiration time. Refreshes
  alongside the radar. Only warnings issued with an actual polygon are
  shown — county-wide watches/advisories that only reference zone codes
  (no precise shape) aren't included yet.

## What's new in v0.4.0

- **Zoom slider** — vertical slider on the right side of the map (below
  the +/- buttons), in addition to scroll/pinch zoom. Two-way synced: it
  moves as you zoom with the mouse, and dragging it zooms the map.
- **History playback** — a new row below the main controls: a Play/Pause
  button + scrub slider that steps through radar frames fetched so far
  *this session* (not a historical archive — refresh a few times, or turn
  on auto-refresh, and you'll have frames to scrub/loop through). Caps at
  12 frames per station; switching stations clears the history since old
  frames aren't relevant to a different radar. Product switching re-renders
  whichever frame you're currently viewing (live or scrubbed) without a
  re-fetch, same as before.

## What's new in this version

- **Radar opacity slider** — next to Basemap, drag it down to see street
  names/labels through the radar layer.
- **Clearer velocity/CC diagnostics** — if Base Velocity or Correlation
  Coefficient comes up empty, the status bar now says exactly why instead
  of silently showing reflectivity with no explanation: either the field
  simply wasn't in that volume (station was in a reflectivity-only/clear-air
  scan), or every gate got filtered out by QC (e.g. genuinely no precip in
  range for that scan). That distinguishes "the program broke" from
  "the radar didn't have that data this time," which was ambiguous before.
- **Reflectivity/dual-pol gating fixed** — reflectivity now has its own
  lenient gate filter, independent of the stricter correlation-coefficient
  filter used for velocity/CC. Previously they shared one filter, and a
  strict CC threshold could (and did, on KPUX) wipe out real reflectivity
  data along with the noise it was meant to remove.

## Earlier changes

- **Light/dark basemap toggle** — a "Basemap" dropdown next to Product.
  OpenFreeMap hosts a dark style directly (`tiles.openfreemap.org/styles/dark`),
  so it's the same free source, just a different look. Satellite was
  considered but dropped per your call.
- **Product switcher** — a "Product" dropdown next to Station lets you
  flip between Reflectivity, Base Velocity, and Correlation Coefficient.
  All three are computed from the same grid pass on each refresh, so
  switching products is instant and doesn't re-hit the network.
- **Cleaned-up rendering** — finer grid resolution (~1km cells vs ~1.15km)
  and light gaussian smoothing to soften blocky edges.
- **Clickable station markers** — every station in `STATIONS` now shows as
  a labeled dot on the map, plus KPUX (Pueblo, CO) which covers Colorado
  Springs. Click one to hot-swap the active radar.
- **Logo** — the app icon (window/taskbar) and a small watermark in the
  bottom-left corner of the map now use the logo you gave me
  (`assets/img/logo.png`).

## Disk usage / cleanup

- **NEXRAD volume downloads**: each fetch downloads to a temp file that's
  deleted automatically the moment decoding finishes (success or failure)
  — nothing accumulates there, ever.
- **Browser cache** (map tiles, etc.): this version forces QtWebEngine's
  cache to memory-only, so nothing from browsing/tile-loading is written
  to disk either — it all just disappears when the app closes. Earlier
  versions used Qt's default disk cache, which *would* have grown slowly
  over long-running sessions; that's fixed now, no manual cleanup needed.
- **History playback frames**: kept in RAM only (capped at 12 per
  station), never written to disk.

## What to actually test

1. **Zoom feels different now** — this is the main point of the rewrite.
   You should be able to zoom in past where the old app turned to mush,
   and street/highway detail should sharpen instead of just upscaling.
2. **Map style** — see if you like MapLibre's default "liberty" look, or
   want something closer to Organic Maps' style (there are other free
   OpenFreeMap styles — "bright", "positron" — trivial one-line swap in
   `assets/map.html`, or I can wire up a style picker).
3. **Radar overlay alignment** — the reflectivity field should sit
   correctly over the right geography. If it's offset, that's a
   projection/corner-calculation bug on my end, not something to route
   around.
4. **Fallback behavior** — if a live volume fetch fails (station down,
   no internet), it should fall back to a labeled "DEMO DATA" synthetic
   blob field instead of crashing, so you always see *something* on
   screen with a clear status-bar note about why.
5. **Legend accuracy** — check the mph range on Base Velocity feels right
   for what you're seeing (green = toward radar, red = away, per the
   legend), and that reflectivity/CC legends make sense against what's
   actually on screen.
6. **Warnings** — if there's active severe weather anywhere near a
   station you check, a colored polygon should appear with a clickable
   popup. No way to force-test this without real warnings in the area,
   so this one just depends on what's happening Tuesday.

## Known rough edges / next steps (not yet done)

- Base velocity is shown raw (no dealiasing), so strong winds near/above
  the Nyquist velocity will show a hard red/cyan wraparound rather than
  a smooth gradient — same as looking at "raw" velocity in most tools
  before the dealiasing step runs. Happy to add dealiasing next if it
  matters for what you're using this for.
- No differential reflectivity (ZDR) or spectrum width yet — same
  pattern as the other products, easy to add to the `PRODUCTS` dict in
  `radar_source.py`.
- No animation/loop yet (the old 6-frame scrub feature).
- Range rings + home marker are drawn as simple GeoJSON circles/point on
  top of the map — same idea as before, just via MapLibre layers instead
  of matplotlib.
- Station markers are plain colored dots — no highlight for "currently
  selected" station yet.

## Files

- `ontheball_web.py` — main PyQt6 app (window, controls, QWebChannel bridge)
- `radar_source.py` — fetch/decode/grid/render pipeline: independent
  gatefilters per product, velocity dealiasing, NWS warnings fetch,
  outputs a georeferenced PNG + corner coordinates per product
- `assets/map.html` — the MapLibre GL page (basemap + radar image
  overlay(s) + station/home markers + NWS warnings), driven from Python
  via QWebChannel
- `assets/js/maplibre-gl.js`, `assets/js/maplibre-gl.css`,
  `assets/js/qwebchannel.js` — local JS bundle map.html loads directly
  (no CDN dependency, no separate download needed after cloning the repo)
- `diagnostics/inspect_suspect_echo.py` — standalone script for
  inspecting raw radar moments in a specific lat/lon box, useful if a
  future rendering discrepancy needs investigating against real data
