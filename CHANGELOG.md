# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.2.1]: https://github.com/elegos/georeel/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/elegos/georeel/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/elegos/georeel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/elegos/georeel/releases/tag/v1.0.0
