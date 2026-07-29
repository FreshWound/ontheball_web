# ontheball — web basemap prototype (v0.7.5)

**Versioning note**: deliverables are named with a version number
(`ontheball_web_v0.7.5.zip`, folder `otb_web_v0.7.5/`) so a broken WIP
build never overwrites a working one. Unzip each version to its own
folder rather than replacing an existing one in place — that way you can
always fall back to the last version that worked.

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

**Known gap, not yet addressed**: `map.html` now loads
`js/qwebchannel.js`, `js/maplibre-gl.js`, and `js/maplibre-gl.css` from
a local `assets/js/` folder instead of the CDN-hosted ES module bundle
used in earlier versions. That folder isn't part of this delivery — it
wasn't included in what was shared back, and this environment has no
way to verify its exact contents. If the app is running correctly with
it already in place, no action needed; flagging only in case that
folder ever needs to be reconstructed or is missing on a fresh checkout.

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


## Install (one-time)

Using a virtual environment keeps these dependencies isolated from your
system Python — recommended so installs stay clean across versions:

```bash
cd otb_web_v0.7.5        # or wherever you unzipped this version

# One-time per machine: make sure the venv module is available
sudo apt-get install -y python3-venv

# Create and activate a virtual environment (per project folder)
python3 -m venv .venv
source .venv/bin/activate
# your prompt should now show (.venv) at the front

# Install everything the app needs — inside a venv, no --break-system-packages needed
pip install --upgrade pip
pip install PyQt6 PyQt6-WebEngine arm_pyart boto3 numpy matplotlib cmweather scipy
```

If PyQt6 complains about a missing xcb-cursor plugin on a fresh system,
that's a system package, not a Python one — install it outside the venv:

```bash
sudo apt-get install -y libxcb-cursor0
```

The `assets/` folder already contains a local copy of MapLibre GL JS
(`maplibre-gl.mjs`, `-shared.mjs`, `-worker.mjs`, `.css`) pulled via npm,
so there's no npm/node dependency at runtime — only pip.

When you're done testing: `deactivate`. Next time, just
`source .venv/bin/activate` again in that same folder — no reinstall
needed unless you're on a new version/folder.

## Run

```bash
cd otb_web_v0.7.5
source .venv/bin/activate   # if not already active
python3 ontheball_web.py
```

A window opens with a station picker, "Refresh now", and an auto-refresh
interval dropdown. It fetches the latest volume for the selected station
from `s3://unidata-nexrad-level2` (free, anonymous), decodes + clutter-filters
it with Py-ART exactly like before, grids it, and renders it onto the map
as a semi-transparent overlay. The basemap itself is loaded live from
OpenFreeMap (`tiles.openfreemap.org`) — needs your normal internet access,
no API key.

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
- `radar_source.py` — fetch/decode/grid/render pipeline, same gatefilter
  logic as before, now outputting a georeferenced PNG + corner coordinates
  instead of a full matplotlib figure
- `assets/map.html` — the MapLibre GL page (basemap + radar image overlay
  + range rings/home marker), driven from Python via QWebChannel
- `assets/maplibre-gl*.mjs`, `assets/maplibre-gl.css` — local MapLibre GL
  bundle (no CDN dependency)
