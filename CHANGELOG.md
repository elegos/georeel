# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  (0–9, default 6) in *Pipeline Settings → Rendering* controls the zlib level
  used for intermediate frame files; set to 0 on slow storage to reduce render
  time at the cost of temporary disk space.
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

[1.3.0]: https://github.com/elegos/georeel/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/elegos/georeel/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/elegos/georeel/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/elegos/georeel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/elegos/georeel/releases/tag/v1.0.0
