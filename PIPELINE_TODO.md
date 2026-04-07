# GeoReel Pipeline

| # | Stage | Status | Module |
|---|-------|--------|--------|
| 1 | **GPX Parser** | ✅ done | `core/gpx_parser.py` |
| 2 | **Photo Matcher** | ✅ done | `core/photo_matcher.py` |
| 3 | **DEM Fetcher** | ✅ done | `core/dem_fetcher.py` |
| 4 | **Satellite Imagery Fetcher** | ✅ done | `core/satellite/xyz_source.py` |
| 5 | **3D Scene Builder** | ✅ done | `core/scene_builder.py` + `core/blender_scripts/build_scene.py` |
| 6 | **Camera Path Generator** | ✅ done | `core/camera_path.py` |
| 7 | **Frame Renderer** | ✅ done | `core/frame_renderer.py` + `core/blender_scripts/render_frames.py` |
| 8 | **Photo Overlay Compositor** | ✅ done | `core/photo_compositor.py` |
| 9 | **Video Assembler** | ✅ done | `core/video_assembler.py` |

---

## Improvements backlog

| # | Item | Status | Notes |
|---|------|--------|-------|
| I-1 | **Multiple imagery providers** | ✅ done | ESRI World, ESRI Clarity, MapTiler Satellite (API key), Custom XYZ; quality tiers (Standard/High/Very High); provider+quality stored in texture for cache invalidation |
| I-2 | **Camera defaults & offset UI** | ✅ done | Default tilt 15°→45°, default offset 80→200 m; offset field is now integer QSpinBox |
| I-3 | **Terrain extent based on camera frustum** | ✅ done | `core/frustum.py` computes ground-visible margin from height+tilt+FOV; bbox expanded before DEM+imagery fetch; cache checks use ≤/≥ containment; render settings saved in project and restored on load; quality comparison uses ordering (cached high satisfies standard request) |
| I-4 | **GPX path ribbon on terrain** | ✅ done | Flat ribbon mesh (10 m wide, 2 m above terrain); per-vertex slope color light-blue→yellow→red (0%→20%→40%+); Emission material always visible; also fixed scene coordinate system bug (camera_path now uses grid bbox, not track bbox) |
| I-5 | **Photo pins (billboards)** | ✅ done | Google Maps-style flat pin mesh (body + triangle tip + outline) per matched waypoint; photo thumbnail quad inside body; Locked Track constraint keeps pins facing camera; named color palette (mustard yellow default) + custom color picker with swatch in Render Settings → Pins tab |
| I-6 | **Smooth path-tangent camera orientation** | ✅ done | Point-ahead algorithm: camera looks at weighted average position over next N seconds of path; configurable look-ahead (default 60 s) and weight distribution (linear/uniform/exponential) in Camera tab |
| I-7 | **Embed render settings in output** | ✅ done | MKV: `georeel_settings.json` attached via FFmpeg `-attach`; MP4/other: `<stem>_settings.json` written alongside; API key excluded from both |
| I-8 | **Photo timezone offset for timestamp matching** | ✅ done | EXIF `DateTimeOriginal` is local time; GPX timestamps are UTC; add configurable UTC offset (hours) in Photos tab so timestamp matching works correctly |
| I-9 | **Deduplicate/spread overlapping photo pins** | ✅ done | Pins sharing the same trackpoint spread symmetrically along X with step = 1.5× scaled pin width; pins are extruded via Solidify modifier with thickness = 30% of pin width (min 2 m); all pin dimensions scale as `height_offset / 200` for consistent appearance at any altitude |
| I-10 | **Animated GPX ribbon + position marker** | ✅ done |
| I-11 | **Preview map** | ✅ done | Single top-down rendered frame from the .blend (Build modifier removed so ribbon is fully shown, photo billboards visible); orthographic camera auto-fitted to scene bbox; shown in a resizable dialog after stage 5 | Ribbon: Build modifier `frame_duration = fly_total_frames`; `frame_start` keyframed LINEAR during pauses (freeze) and CONSTANT between pauses (advance); Marker: Follow Path `offset_factor` keyframed CONSTANT at each pause start, LINEAR between pauses; Compositor: `_absorb_photo_gaps` merges photo blocks separated by ≤ 2×fade_frames fly frames so photos display back-to-back |
