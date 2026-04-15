# Pipeline Settings Reference

Open via *Options → Pipeline Settings* (keyboard shortcut: none; also accessible from the toolbar).

## Summary

Pipeline Settings is a tabbed dialog that controls every configurable parameter of the GeoReel pipeline. Settings are persisted across sessions using Qt's `QSettings` store.

| Tab | What it controls |
|---|---|
| [Playback](#playback) | Output frame rate |
| [Camera](#camera) | Camera height, orientation, look-ahead, photo pause behaviour |
| [Rendering](#rendering) | Render engine, resolution, quality, PNG compression, segmented rendering |
| [Photos](#photos) | Photo matching, timezone offset, transition style and duration |
| [Map](#map) | Satellite imagery provider, zoom level, API key |
| [Pins](#pins) | Colours of the track marker and photo waypoint pins |
| [Output](#output) | Video container, codec, hardware encoder, quality, preset |

The most performance-critical settings are in the [Rendering](#rendering) tab. If your render is slow or runs out of GPU memory, start there. See the [Troubleshooting](#troubleshooting-performance-and-memory) section at the end of this document.

---

## Playback

### Frame rate
**Key:** `render/fps` | **Default:** `30`

Frames per second of the output video.

| Value | Use case |
|---|---|
| 24 fps | Cinematic look |
| 30 fps | Standard broadcast / social media |
| 60 fps | Ultra-smooth; doubles render time and file size |

---

## Camera

### Path smoothing — Method
**Key:** `render/path_smoothing` | **Default:** `spline`

How raw GPS trackpoints are converted into a smooth camera path.

| Value | Behaviour |
|---|---|
| `spline` | Cubic B-spline passes through every trackpoint — faithful to the GPS trace |
| `dp_spline` | Douglas-Peucker simplification first, then B-spline — reduces jitter from noisy GPS at the cost of some positional accuracy |

### Height — Mode
**Key:** `render/camera_height_mode` | **Default:** `dem_fixed`

| Value | Behaviour |
|---|---|
| `dem_fixed` | Camera stays exactly *Distance* metres above the raw DEM surface |
| `dem_smooth` | Camera height is low-pass filtered so it does not follow every small terrain bump — more cinematic |

### Height — Distance to track
**Key:** `render/camera_height_offset` | **Default:** `2000` m | **Range:** 5–5000 m

Slant distance between the camera and the track point directly below it. Typical values: 500–1500 m for hiking, 1000–3000 m for cycling or driving.

### Orientation — Method
**Key:** `render/camera_orientation` | **Default:** `tangent`

| Value | Behaviour |
|---|---|
| `tangent` | Camera faces the direction of travel — smooth, follows curves naturally |
| `lookat` | Camera points toward the next photo waypoint — more deliberate, narrative feel |

### Orientation — Downward tilt
**Key:** `render/camera_tilt_deg` | **Default:** `45`° | **Range:** 0–89°

Degrees below horizontal the camera points. 0° = dead horizontal; 45° = good balance of horizon and terrain detail; 75°+ = near top-down.

### Orientation — Terrain view distance
**Key:** `render/frustum_margin_km` | **Default:** `50.0` km | **Range:** 1–500 km

Maximum terrain fetch radius around the camera path. Also used as the margin when computing which terrain tiles are visible per render segment. Increase this if terrain appears to end at the edges of the frame (especially at shallow tilt angles).

### Orientation — Look-ahead
**Key:** `render/tangent_lookahead_s` | **Default:** `60.0` s | **Range:** 1–300 s

In *Path tangent* mode, the camera heading is a weighted average of trackpoints within this time window ahead of the current position. Longer values smooth sharp corners; shorter values hug the actual path. Only applies when Method = `tangent`.

### Orientation — Weight distribution
**Key:** `render/tangent_weight` | **Default:** `linear`

How trackpoints inside the look-ahead window contribute to the average heading.

| Value | Behaviour |
|---|---|
| `linear` | Nearer points count more — good default |
| `uniform` | All points equal weight — smoother but slower to react to turns |
| `exponential` | Very strongly biased toward the nearest points — tightest cornering |

### Photo pause — Camera movement
**Key:** `render/photo_pause_mode` | **Default:** `hold`

What the 3D camera does while a photo is displayed full-screen.

| Value | Behaviour |
|---|---|
| `hold` | Camera freezes at the waypoint |
| `ease` | Camera smoothly decelerates into the waypoint, pauses, then accelerates away |

### Photo pause — Duration per photo
**Key:** `render/photo_pause_duration` | **Default:** `3.0` s | **Range:** 0.5–30 s

How long each photo is shown full-screen before the fly-through resumes.

---

## Rendering

### Engine
**Key:** `render/engine` | **Default:** `eevee`

| Value | Description | Speed |
|---|---|---|
| `viewport` | EEVEE at 4 samples, no shadows, no ambient occlusion, satellite textures downscaled to 50% in VRAM. Best for checking camera path and timing. | Fastest |
| `eevee` | Full-quality EEVEE rasterisation at the configured quality level. Recommended for final output. | Fast |
| `cycles` | Physically-based path tracing — accurate lighting, shadows, and reflections. | 10–50× slower than EEVEE |

> **Note:** Satellite terrain renders are texture-bandwidth-bound, not compute-bound. Reducing the sample count (Low vs. High) has a much smaller impact than reducing texture size. The Viewport engine's 50% texture downscale (→ ¼ the VRAM footprint) is the most effective single speedup for large satellite scenes.

### Aspect ratio
**Key:** `render/aspect_ratio` | **Default:** `landscape`

| Value | Dimensions |
|---|---|
| `landscape` | 16:9 (1280×720, 1920×1080, 2560×1440, 3840×2160) |
| `portrait` | 9:16 (720×1280, 1080×1920, 1440×2560, 2160×3840) |
| `square` | 1:1 (720×720, 1080×1080, 1440×1440, 2160×2160) |

### Resolution
**Key:** `render/resolution` | **Default:** `1080p`

Pixel dimensions of the output video. Higher resolutions are sharper but take proportionally longer to render and produce larger files.

### Quality
**Key:** `render/quality` | **Default:** `medium`

Number of render samples per pixel. Applies to EEVEE and Cycles only (Viewport always uses 4 samples).

| Value | EEVEE samples | Cycles samples |
|---|---|---|
| `low` | 32 | 64 |
| `medium` | 64 | 128 |
| `high` | 128 | 256 |

### Frame PNG compression
**Key:** `render/png_compression` | **Default:** `6` | **Range:** 0–9

zlib compression level for the intermediate PNG frames written by Blender.

| Level | Effect |
|---|---|
| 0 | No compression — fastest disk writes, largest temporary files |
| 6 | Default zlib level — good balance of speed and size |
| 9 | Maximum compression — smallest files, slowest writes |

Intermediate frames are deleted after the video is assembled, so lower values trade temporary disk space for faster rendering throughput. On fast NVMe storage the difference is small; on slow HDDs or network storage, level 0–2 can noticeably reduce render time.

### Render segments
**Key:** `render/n_segments` | **Default:** `1` | **Range:** 1–16

Split the render into N sequential Blender passes. Each pass launches a fresh Blender process that:

1. Loads only the terrain tiles visible from the camera during its frame range (determined by the camera AABB expanded by *Terrain view distance*).
2. Renders its frame slice and exits, fully releasing GPU memory.

Use `1` (single pass) for most scenes. Increase to 4–8 when the scene's satellite textures exceed your GPU VRAM.

> **Note:** Segmented rendering reduces peak VRAM only when the camera actually traverses different parts of the terrain in each segment. If your track crosses the entire terrain in every segment (common for short or looping tracks), all tiles remain visible and VRAM usage does not decrease. In that case, the Viewport engine's texture downscale is more effective.

### Use custom temp directory
**Key:** `cache/use_custom_dir` | **Default:** `false`

When enabled, GeoReel writes all temporary working files (satellite tile PNGs, the `.blend` scene, rendered frame PNGs) to the directory specified by *Temp directory path* instead of the system default (`/tmp` on Linux/macOS, `%TEMP%` on Windows).

Enable this when:
- The system temp partition is too small for large satellite textures and frame sequences.
- You want working files on a faster disk (e.g. an NVMe drive separate from the OS partition).

### Temp directory path
**Key:** `cache/base_dir` | **Default:** *(empty — system temp)*

Absolute path to the directory used for temporary working files when *Use custom temp directory* is enabled. GeoReel creates uniquely named subdirectories inside this path for each pipeline run and removes them on completion or cancellation.

---

## Photos

### Matching mode
**Key:** (set in the Photos tab) | **Default:** `both`

| Mode | Behaviour |
|---|---|
| `gps` | Matches by geographic proximity to the nearest trackpoint |
| `timestamp` | Matches by EXIF date/time against GPX timestamps |
| `both` | GPS primary; timestamp fallback when GPS EXIF is missing; warns when the two methods disagree by more than the configured threshold |

### Timezone offset
**Key:** `render/photo_tz_offset_hours` | **Default:** `0.0`

UTC offset of the camera clock, in hours (e.g. `+2.0` for CEST). Used only in `timestamp` and `both` modes.

### Transition
**Key:** `render/photo_transition` | **Default:** `fade`

| Value | Behaviour |
|---|---|
| `fade` | Cross-dissolve between terrain and photo |
| `cut` | Hard edit — instant switch |

### Letterbox fill
**Key:** `render/photo_fill` | **Default:** `blurred`

What fills the bars when the photo's aspect ratio differs from the video's.

| Value | Behaviour |
|---|---|
| `blurred` | Blurred version of the photo — visually cohesive |
| `black` | Plain black bars |

### Fade duration
**Key:** `render/photo_fade_duration` | **Default:** `0.5` s

Duration of the cross-dissolve transition when *Transition* = `fade`.

---

## Map

### Imagery provider
**Key:** `imagery/provider` | **Default:** `esri_world`

| Provider ID | Name | API key required |
|---|---|---|
| `esri_world` | ESRI World Imagery | No |
| `esri_clarity` | ESRI Clarity (beta) | No |
| `maptiler` | MapTiler Satellite | Yes (free tier available) |
| `custom` | Custom XYZ URL | — |

### Imagery quality
**Key:** `imagery/quality` | **Default:** `standard`

Controls the zoom level used when fetching satellite tiles. Higher quality fetches more tiles at higher zoom — increases download time and satellite texture file size.

### Fetch mode
**Key:** `imagery/fetch_mode` | **Default:** `prefetch`

| Value | Behaviour |
|---|---|
| `prefetch` | All tiles downloaded before scene construction begins |
| `on_demand` | Tiles fetched as needed during scene construction |

---

## Pins

### Track marker colour
**Key:** `marker/color` | **Default:** `LightBlue`

Colour of the animated position marker that moves along the track ribbon during playback. Choose a named CSS colour or set a custom hex value.

### Photo waypoint pin colour
**Key:** `pins/color` | **Default:** `ForestGreen`

Colour of the billboard pins placed at each photo waypoint along the track.

---

## Output

### Container
**Key:** `output/container` | **Default:** `mkv`

| Value | Notes |
|---|---|
| `mkv` | Matroska — attaches the source GPX and render settings JSON inside the file |
| `mp4` | MPEG-4 — wider compatibility, no attachments |

### Codec
**Key:** `output/codec` | **Default:** `h265`

| Value | Notes |
|---|---|
| `h264` | H.264/AVC — maximum compatibility |
| `h265` | H.265/HEVC — ~40% smaller files than H.264 at equivalent quality |
| `av1` | AV1 — best compression but slowest encoding; limited hardware support |

### Encoder
**Key:** `output/encoder` | **Default:** `libx265`

GeoReel detects available hardware accelerators at runtime and offers them alongside software fallbacks.

| Accelerator | Encoders available |
|---|---|
| NVIDIA NVENC | `h264_nvenc`, `hevc_nvenc`, `av1_nvenc` |
| AMD AMF | `h264_amf`, `hevc_amf`, `av1_amf` |
| Intel QSV | `h264_qsv`, `hevc_qsv`, `av1_qsv` |
| Apple VideoToolbox | `h264_videotoolbox`, `hevc_videotoolbox` |
| Software | `libx264`, `libx265`, `libaom-av1`, `libsvtav1` |

### Quality (CQ/CRF)
**Key:** `output/cq` | **Default:** `28`

Constant-quality parameter passed to the encoder. Lower = higher quality, larger file. Typical ranges: H.264/H.265: 18–28 (lower is better); AV1 software: 28–40.

### Preset
**Key:** `output/preset` | **Default:** `medium`

Encoding speed/compression trade-off. Slower presets produce smaller files at the same quality. Common values: `ultrafast`, `fast`, `medium`, `slow`, `veryslow`.

---

## Troubleshooting: Performance and Memory

### Render is slow

**Most likely cause:** the satellite texture is large and the GPU is texture-bandwidth-bound.

1. **Switch to Viewport engine** (*Rendering → Engine → Viewport*). This downscales all terrain textures to 50% (¼ VRAM) and disables shadow/AO computation. It is the single most effective speedup for terrain renders. Use it to verify the camera path and timing before committing to a full-quality render.

2. **Lower the resolution** (*Rendering → Resolution*). Halving the resolution (e.g. 1080p → 720p) reduces the number of pixels rendered per frame by ~56%, proportionally reducing render time.

3. **Reduce quality** (*Rendering → Quality → Low*). This halves the sample count vs. Medium, but for texture-bound scenes the speedup is modest (10–20%) compared to the texture downscale.

4. **Lower PNG compression** (*Rendering → Frame PNG compression → 0*). If your storage is slow (HDD, network share), setting the zlib level to 0 eliminates compression time. Intermediate frames are temporary, so disk space is the only cost.

### GPU runs out of VRAM

Symptoms: Blender crashes or falls back to CPU mid-render; GPU memory monitor shows the render process occupying 100% of VRAM.

**Step 1 — Use the Viewport engine.** The 50% texture downscale reduces VRAM from the satellite textures by 4×. This is almost always the right first step.

**Step 2 — Increase render segments** (*Rendering → Render segments*). With N segments, each Blender process loads only the terrain tiles visible from the camera during its fraction of the animation. Each process exits completely between segments, releasing all VRAM before the next one starts.

> **Caveat:** Segmented rendering only helps when the camera visits different parts of the terrain in each segment. If your track is short or the camera sees the entire terrain at once, all tiles remain loaded in every segment and VRAM usage does not decrease. In that case, only the Viewport texture downscale will help.

**Step 3 — Reduce the satellite imagery zoom level** (*Map → Imagery quality*). A lower zoom level fetches coarser tiles, producing a smaller satellite texture and a proportionally smaller VRAM footprint.

**Step 4 — Set a custom temp directory on a fast local disk** (*Rendering → Use custom temp directory*). If your default temp storage is slow or on a network share, placing the working directory on a fast local NVMe drive reduces the time Blender spends writing and reading frame PNGs between segments. See [Insufficient disk space during processing](#insufficient-disk-space-during-processing) for setup instructions.

### Insufficient disk space during processing

GeoReel writes substantial temporary data to disk during a pipeline run:

- **Satellite tile PNGs** — one PNG per terrain tile; size depends on imagery quality and tile count (tens of MB to several GB total).
- **Rendered frame PNGs** — one PNG per video frame; at 1080p/30 fps a 5-minute video produces ~9 000 frames at roughly 3–6 MB each uncompressed (27–54 GB at zlib level 0).
- **The `.blend` scene file** — typically 50–500 MB depending on terrain complexity.

By default all of this goes into the system temp directory (`/tmp` on Linux/macOS). Many Linux distributions mount `/tmp` as a `tmpfs` in RAM, which means it is limited to a fraction of total RAM. This can easily be exhausted for large renders.

**To redirect temporary files to a different location:**

1. Open *Options → Pipeline Settings → Rendering*.
2. Enable **Use custom temp directory**.
3. Set **Temp directory path** to a directory on a partition with sufficient free space (e.g. `/home/yourname/georeel_tmp` or a dedicated data drive).

GeoReel creates per-run subdirectories inside that path and removes them automatically on completion or cancellation. Stale subdirectories from crashed runs are pruned on the next application startup.

**Estimating required space:** `frames × resolution_MB + texture_GB`. At 1080p with zlib level 6, allow roughly 1–2 MB per frame. Set PNG compression to 0 only if disk throughput is the bottleneck, not available space.

### Scene build takes a long time or freezes the UI

Scene building (Stage 5) is the most time-consuming single step because it loads the satellite texture (potentially several gigabytes), splits it into tile PNGs, and launches Blender. A progress dialog shows the current stage and lets you cancel.

- **"Loading satellite texture"** — loading a large PNG or project file into memory. Cannot be skipped; reduce imagery quality to fetch a smaller texture.
- **"Splitting satellite texture into N tiles"** — writing per-tile PNG files to disk. Speed depends on disk throughput and PNG compression level; use compression level 0 for fastest writes.
- **"Running Blender to assemble 3D scene"** — Blender is constructing the mesh and applying textures headlessly; typically 10–60 seconds depending on tile count.

### Camera path looks jittery

Switch *Camera → Path smoothing* to *Douglas-Peucker + B-spline*. This simplifies the GPS trace before fitting the spline, removing high-frequency noise from the trackpoints. Alternatively, increase *Look-ahead* to average over a longer window of future trackpoints.

### Photos appear at the wrong positions

Check the *Matching mode* in the Photos tab:
- If photo EXIF GPS data is unreliable (indoor shots, tunnel sections), switch to `timestamp`.
- If the camera clock was set to a different timezone, set the correct UTC offset in *Timezone offset*.
- Enable `both` mode to get a warning when the GPS and timestamp methods disagree by more than 100 m — this usually indicates a misconfigured clock or missing GPS lock.
