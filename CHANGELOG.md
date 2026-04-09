# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/elegos/georeel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/elegos/georeel/releases/tag/v1.0.0
