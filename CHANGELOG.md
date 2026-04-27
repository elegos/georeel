# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0]

### Added

- **Intro overview animation** — a new optional cinematic intro prepended to the video: the
  camera starts from a top-down bird's-eye view of the entire route, holds for a configurable
  static period, then smoothly descends and snaps to the fly-through starting point.  A 1-second
  *clearance hold* after the descent lets the overview ribbon finish fading before the journey
  begins.  Configurable total hold duration (default 6 s); the descent uses Blender's native SINE
  EASE_IN_OUT for a true ease-in-ease-out profile.
- **Dynamic speed** — the fly-through camera automatically accelerates through "dead sections"
  (segments far from any photo waypoint) and ramps back to normal speed as the next stop
  approaches.  Configurable peak speed multiplier (default 1.33×) and ramp duration (default 4 s).
- **Auto-zoom** — camera field-of-view responds to track curvature: the lens narrows on straight
  sections and widens into tight corners.  Configurable curvature threshold (°/m).
- **3D locality map-pin banners** — when the *Locality names* overlay is enabled, a new *3D
  banner* mode renders place names as world-space billboard objects inside Blender (TRACK_TO
  constraint + LOC_DIFF scale driver + animated alpha), baked directly into the terrain frames
  rather than composited on top.  Plain-text overlay and 3D banner modes are independent toggles.
- **`PreviewPipelineDialog`** — a single cancellable progress dialog that drives all three preview
  stages (frame render → photo composite → video assembly) through the server, replacing the
  previous per-stage dialogs.
- **`render_defaults.py`** — a new central module that holds all pipeline defaults (FPS,
  resolution, DEM/satellite settings, camera constants, etc.).  All modules import constants from
  it instead of defining them inline.
- **`--reload` flag** for the `georeel-server` CLI, for development convenience.

### Changed

- Locality name compositing is now a **separate stage before photo compositing** in both the
  preview and full-render pipelines.  Photo overlay frames are never modified by the locality text
  compositor, and locality settings are computed *before* the render step so 3D pins are baked
  into terrain frames.
- Locality compositing settings JSON is written beside the output file rather than in the OS temp
  directory, so the stale-file sweep cannot remove it while the render is in progress.
- All pipeline constants consolidated into `render_defaults.py`; all modules import from it
  instead of defining values inline.

### Fixed

- Intro overview animation: Blender keyframe injection now uses four sparse control points (hold
  start, descent start, descent end / snap, clearance end) with Blender's native SINE EASE_IN_OUT
  applied to the descent-start keyframe.  The previous approach injected one keyframe per frame
  and baked the easing curve in Python, producing subtle artefacts at the boundary frames.
- Intro overview animation: a quaternion hemisphere check is now applied before each keyframe
  insertion so component-wise NLERP always takes the short arc.  Without this, the camera
  could flip through the long arc mid-descent.
- Intro overview animation: look-at distance is now linearly interpolated during the descent to
  avoid a position discontinuity at the hold/descent boundary; easing was also changed from
  ease-out to ease-in to match the natural feel of the descent.
- Intro overview animation: the TrackOverview ribbon now fades during the descent and reaches full
  transparency exactly when the camera arrives at the track start.  The clearance hold is played
  with no ribbon visible, giving a clean transition into the fly-through.

## [1.4.0] - 2026-04-21

### Added

- **REST API server** — all pipeline logic now runs inside `georeel-server`, a FastAPI
  process managed automatically by the GUI at startup.  The GUI is now a thin HTTP
  client; all computation (GPX parsing, DEM/satellite fetching, Blender scene build,
  frame rendering, compositing, video assembly) happens server-side.
  - `georeel-server` can also be run standalone for headless or scripted use.
  - `ServerClient` — a fully typed synchronous HTTP wrapper used by all GUI workers.
  - `ServerManager` — launches and monitors the server subprocess; selects a free
    ephemeral port and retries health checks until the server is ready.
- **Server-side workspace lifecycle** — each GUI session owns one workspace on the
  server.  All uploaded files and intermediate results live inside the workspace
  directory and are cleaned up automatically:
  - Workspace directories (and all their jobs) are deleted on server shutdown or when
    the GUI closes the workspace.
  - Each job tracks its own temp directory; files are deleted as soon as the job is
    superseded or cancelled.
  - Stale `georeel_*` directories left by crashed prior runs are pruned at server
    startup.
  - Rendered video files are deleted from the server immediately after the GUI
    downloads them.
- **Server-side GPX parsing** — `POST /api/v1/gpx/parse` and `POST /api/v1/gpx/clean`
  handle GPX file parsing and hole repair.  All UI code paths (main pipeline, keyframe
  preview worker, scene-prep worker, project load worker, GPX file selection) now
  call these endpoints; no GPX parsing logic remains in the GUI process.
- **Server-side photo matching** — photos are uploaded to the workspace via
  `POST /api/v1/photos/upload` (EXIF metadata extracted server-side); matching against
  the GPX track is done by `POST /api/v1/photos/match`.  Workspace file paths are
  remapped back to local paths in the GUI so photo thumbnails and status indicators
  continue to work unchanged.
- **Project save/load via server** — `POST /api/v1/project/save` assembles a
  `.georeel` archive from workspace assets and returns it as a binary download;
  `POST /api/v1/project/load` accepts a `.georeel` upload, extracts it into a new
  workspace, and returns the restored state.

### Changed

- Pipeline stages 3–9 (DEM fetch, satellite fetch, scene build, camera path,
  frame render, compositor, video assembly) now run as async server jobs polled by
  the GUI rather than blocking in-process threads.
- `KeyframeCalcWorker` and `ScenePrepWorker` (background preview workers) now
  delegate GPX parsing and repair to the server; DEM and satellite fetching in those
  workers remain local (preview-only path, not part of the main export pipeline).

- **Locality names overlay** — a new *Locality names* tab in the main window optionally
  composites the current location name (from Nominatim reverse geocoding) onto the video
  as a fade-in/fade-out overlay.
  - Two Nominatim backend options: OSM public servers or a custom URL.
  - Configurable check interval (default: every 60 s of track time), detail level
    (village → country), display position (9 anchors), duration (or *Forever* mode),
    text colour, and shadow.
  - Cross-fade when the location name changes faster than the configured duration.
- **Locality names preview** — a *Preview locality names…* button in the *Locality names*
  tab queries Nominatim in a background thread and displays the full timeline in a table
  (location name, track time as either an absolute UTC clock or elapsed HH:MM:SS, and
  frame range).  Requires the GPX track to be loaded and keyframes to be calculated first.
- **Locality names timeline caching** — the timeline computed by the preview (or during
  a full render) is kept in memory and reused at render time, avoiding re-querying
  Nominatim for the same track and settings.  The timeline is saved inside the `.georeel`
  archive (`locality/timeline.json`) and restored when the project is re-opened.
  The cache is automatically invalidated when the Nominatim service, URL, check interval,
  detail level, or GPX track changes.

### Changed

- *Preview locality names…* triggers camera-path computation automatically when the
  GPX track is loaded but keyframes have not yet been calculated, so users no longer
  need to click *Calculate keyframes* manually before previewing.

### Fixed

- B-spline track ribbon produced phantom loops and overshooting artefacts at
  sharp direction reversals (e.g. switchbacks).  The cubic B-spline
  (`scipy.interpolate.splprep`) is replaced by piecewise-linear arc-length
  resampling, which faithfully follows the GPS path without any overshoot.
- Single-point GPS spikes (a point that jumps far from the track then snaps
  back) now pass through the speed check but are caught by a new path-spike
  filter that removes them before hole-repair runs.
- Gap-fill synthetic-point count was proportional to elapsed time, causing a
  stationary recording pause to insert many redundant points at the same
  location.  The count is now proportional to geographic distance, so a
  stationary pause inserts exactly one connecting point.

- GPX files that reference namespace prefixes (e.g. `ns3:TrackPointExtension`)
  without declaring them in the root element now parse correctly.  This
  commonly affects files merged from two GPX exports where only the first
  file's root element (and its namespace declarations) was kept.  The parser
  now detects undeclared prefixes, injects the well-known URI for recognised
  ones (Garmin TrackPoint / GpxExtensions / WaypointExtension), and uses a
  synthetic `urn:unknown-ns:` fallback for any others.
- Output video was all black (except the title overlay and music) and only
  about 10 seconds long despite a full render.  Root cause: `_prepend_black_frames`
  renumbered source frames as `int(stem) + n_black`, which is correct for
  0-based stems but creates a 1-frame gap at index `n_black` when compositor
  output is 1-indexed (`000001.png`…).  Frame `000150.png` was missing, causing
  FFmpeg to stop reading after 149 frames.  Source frames are now renumbered
  via `enumerate` so the output is always a gapless `000000`…`{n_black+N-1}` sequence.
- Music loop with multiple tracks created far too many FFmpeg input streams
  (one per implied 10 s chunk, so two 3-minute tracks produced ~40 inputs for
  a 6-minute video).  The repetition count is now derived from actual track
  durations probed via `ffprobe`; the 60 s-per-track fallback applies only when
  `ffprobe` is unavailable.
- Ribbon and waypoint marker were consistently ahead of the camera and photo
  carousels throughout the fly-through (initially ~3 s, reduced to ~2 s after an
  intermediate fix).  Root cause: the camera resampled its look-at positions along
  the B-spline arc of the GPS track, while the ribbon and marker use piecewise-linear
  arc-length resampling.  The B-spline smooths GPS measurement noise and is therefore
  systematically shorter than the PL path; as a result the camera look-at lagged
  behind the ribbon position at every frame.  For the default tangent orientation
  mode, the B-spline is now bypassed entirely: look-at positions are resampled
  directly along the PL path via `np.interp`, so the camera look-at advances at
  exactly the same rate as the ribbon face reveal.  The B-spline is retained only
  for non-default orientation modes that require the spline derivative.
- Sporadic abrupt orientation jumps in the fly-through camera.  A second-pass
  MAD-based (median absolute deviation) spike filter now detects frames where the
  heading change is significantly larger than the median and replaces the affected frames
  with a linear interpolation from the surrounding smooth orientations.
- Saving a project with a lazy-loaded satellite texture produced a 0-byte
  `satellite/texture.png` in the resulting archive.  The save now writes to a sibling
  `.tmp` file and atomically renames it, so the original archive (the texture's source
  ZIP) is never opened for writing while still being read.
- `autosave_tilde` produced ZIP archives with duplicate (shadow) entries when called
  more than once for the same project.  The function now performs a clean rewrite from
  the base archive — copying unchanged entries and writing updated ones — rather than
  appending, which always produces duplicate central-directory entries in append mode.

## [1.3.0] - 2026-04-15

### Added

- **Viewport draft render engine** — a new *Viewport* option in *Pipeline Settings →
  Rendering* runs EEVEE at 4 TAA samples with no shadows or ambient occlusion and
  downscales all satellite textures to 50% resolution in VRAM (¼ the memory
  footprint). It is the fastest way to verify the camera path and photo timing
  before committing to a full-quality render.
- **Scene build progress dialog** — a cancellable modal dialog now tracks tile
  splitting and Blender assembly step-by-step, replacing the multi-minute UI
  freeze that occurred during scene construction for large satellite textures.
- **GPX hole repair** — a *Repair* drop-down in the main window fills recording
  gaps (paused recorder, lost satellite signal, implausible speed jumps) with
  synthetic trackpoints using one of three modes:
  - *None* (default) — gaps are left as-is
  - *Linear* — straight-line interpolation between the gap endpoints in
    coordinate space
  - *Street* — OSRM routing API finds the shortest road route between the
    endpoints and resamples it uniformly; falls back silently to linear when
    OSRM is unavailable
- **Shifting pin** — when hole repair is active, enabling this checkbox makes the
  animated track marker alternate between its chosen colour and its
  complementary colour (hue rotated 180°) over reconstructed segments, giving a
  clear visual indication that part of the track was filled in.
- **Ribbon colour by GPS speed** — a new *Speed* option in the main window's
  *Ribbon* tab colours the track ribbon from cool blue (slow) through
  cyan/green (mid-pace) to orange (fast), scaled to the 5th–95th percentile
  speed range of the track. The previous slope gradient remains the default.
- **Ribbon self-lit mode** — a *Self-lit* checkbox in the *Ribbon* tab reduces the
  ribbon's emission strength so Blender's Filmic tone-mapper does not compress
  saturated colours toward white — recommended with the speed gradient or any
  vivid colour scheme.
- **Multiple audio tracks** — the *Music* tab in Clip Effects now supports any
  number of audio files (MP3, AAC, FLAC, OGG, WAV, Opus), each with independent
  start delay, fade-in, fade-out, and loop settings. All tracks are embedded in
  the `.georeel` project file.
- **Camera speed presets** — *Hiking* (80 m/s), *Cycling* (120 m/s), and
  *Driving* (320 m/s) presets with a live expected video duration label. Speed
  is now a per-project setting in the main window rather than in Pipeline
  Settings.
- **Configurable PNG frame compression** — a *Frame PNG compression* spin box
  (0–9, default 1, matching Blender's own default) in *Pipeline Settings →
  Rendering* controls the zlib level used for intermediate frame files.
  Compression is now performed out-of-process: Blender writes each frame
  uncompressed for maximum render throughput, then a background thread pool
  re-compresses the PNG while the GPU is already rendering the next frame.
  The number of compression workers scales with available CPU cores (up to 4).
- **Tooltips** on all *Pipeline Settings* and *Blender Settings* controls.
- **Install scripts** — one-line automated installers for Linux/macOS (shell) and
  Windows (PowerShell); see `INSTALL.md`.

### Changed

- Satellite texture is now split into N×M PNG tiles (≤400 Mpx each) backed by
  matching terrain sub-meshes, working around Blender's 2 GB texture pack limit
  and keeping per-segment VRAM proportional to the visible terrain fraction.
- Satellite imagery quality levels (*Standard / High / Very High*) are now
  zoom-level-based (z=13/15/17) instead of tile-count-based, ensuring
  consistent ground resolution regardless of track length.
- GPX reported max speed now uses the 99th-percentile segment speed, filtering
  out GPS artifacts that previously inflated the value to implausible figures.
- All temporary working files (satellite tiles, `.blend` scene, rendered frame
  PNGs, composited frames) are now managed by a unified temp manager that
  supports a configurable base directory and prunes stale directories from
  crashed runs on the next startup.
- Preview generation uses smaller satellite textures for faster turnaround.
- GPX track loading and photo thumbnail loading are non-blocking; the UI remains
  responsive while data is read in background threads.
- *Render Settings* dialog renamed to *Pipeline Settings* throughout the UI and
  documentation.

### Fixed

- Camera briefly snapped to an incorrect heading for a single frame during tight
  curves. The root cause was component-wise Gaussian smoothing of the heading
  vector, which is mathematically unstable near 180° reversals. Direction
  smoothing now operates in angle space (`arctan2` → `np.unwrap` → Gaussian
  filter → back to unit vector) before the camera offset is computed.
- Corrupted or truncated satellite tiles caused scene construction to abort with
  an unhandled exception. Invalid tiles are now detected and replaced with a
  neutral fallback before the texture is assembled.

---

## [1.2.1] - 2026-04-11

### Fixed

- Video fade-out had no visible effect when the title overlay was enabled
  together with fade-in. The fade-out start time was miscalculated when the
  fade-in black frames were materialised as real PNGs (the `skip_prepend` path):
  `fi_black` was counted twice, pushing `fo_start` into the already-black
  padding region where the filter had no visible effect.

## [1.2.0] - 2026-04-10

### Added

- **Music track**: attach an audio file (MP3, AAC, FLAC, OGG, WAV, Opus) to the
  final video. Configurable start delay, fade-in, fade-out, and loop. The audio
  file is embedded in the `.georeel` project file so the project is fully
  self-contained.
- **Clip effects — Fade tab**: video fade-in from black and fade-out to black,
  each with independently configurable black-hold and fade durations.
- **Clip effects — Title tab**: text overlay rendered with a configurable font,
  size, colour, drop-shadow, position anchor, margin, alignment, and
  display/fade duration.
- **File menu**: Open (Ctrl+O), Open Recent submenu (last 10 existing files),
  Save (Ctrl+S), and Save As (Ctrl+Shift+S).
- **Non-blocking save**: saving a project runs in a background thread; UI
  actions are disabled and an indeterminate progress bar appears in the status
  bar while the save is in progress.
- **App version** is now displayed in the status bar.

### Changed

- Clip Effects panel is split into three focused tabs: *Fade*, *Title*, and
  *Music*, replacing the previous single crowded tab.
- Action buttons (Preview, Start, Clear, …) are now displayed below the tab
  widget and remain visible regardless of which tab is active.
- The `.georeel` project file now embeds the GPX track, satellite texture,
  title font, and music file, making it fully portable with no external file
  dependencies.
- Photos embedded in a `.georeel` file are now stored under their original
  filenames. Previously they were renamed to a zero-padded sequence
  (`0000.jpg`, `0001.jpg`, …).
- Preview video preserves the user's chosen aspect ratio (landscape, portrait,
  square) at 720 p resolution.
- Music fade-out is suppressed in the preview video unless the preview covers
  the entire track (i.e. the full video is short enough to be fully previewed).

### Fixed

- Aspect ratio was incorrect in the preview video when a non-landscape ratio
  was selected.

## [1.1.0] - 2026-04-09

### Added

- Photo overlays are now composited into the preview video.

### Changed

- Frame rendering now uses Blender's `animation=True` mode: the render engine
  (and GPU) stays alive for the entire sequence instead of reinitialising on
  every frame, eliminating the idle cycles visible in GPU monitoring tools.
- Temporary working directories (rendered frames, composited frames) are now
  deleted immediately when a render job finishes or is cancelled, rather than
  waiting until the application exits.
- Architecture documentation updated to reflect current 9-stage pipeline,
  corrected data-flow table, and added temporary-file lifecycle table.
- Added a link to the architecture documentation from the README.

### Fixed

- Multi-photo carousels at the same waypoint now display all photos in
  sequence. Previously, only the first photo was shown because the pause
  schedule produced one entry per photo but the compositor only kept the last.
- Final video was cut to roughly half its expected length when the route
  contained photo pauses. The cause was an off-by-one in the gap-absorption
  loop that skipped every second pause block.
- Track marker animation now pauses for the full carousel duration when
  multiple photos share a waypoint. Previously the marker held only for one
  photo's duration regardless of cluster size.

## [1.0.0] - 2026-04-09

First version.

[1.5.0]: https://github.com/elegos/georeel/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/elegos/georeel/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/elegos/georeel/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/elegos/georeel/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/elegos/georeel/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/elegos/georeel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/elegos/georeel/releases/tag/v1.0.0
