# Main Window Settings Reference

All settings described here are **per-project** — they are saved inside the `.georeel`
project archive and restored when the project is re-opened.  They are exposed directly
in the main window tabs, without needing to open any additional dialog.

For pipeline settings (camera behaviour, render engine, satellite imagery, output codec,
etc.) see [PIPELINE_SETTINGS.md](PIPELINE_SETTINGS.md), accessible via
*Options → Pipeline Settings*.

---

## Tab overview

| Tab | What it controls |
|---|---|
| [Main](#main-tab) | GPX track, photos, photo-matching mode, output path, flythrough speed |
| [Ribbon](#ribbon-tab) | Track-ribbon colour gradient and emission style |
| [Fade](#fade-tab) | Fade-to-black at video start and end |
| [Title](#title-tab) | Optional opening-title text overlay |
| [Music](#music-tab) | Background audio track(s) |
| [Locality names](#locality-names-tab) | Nominatim reverse-geocoding place-name overlay |

---

## Main tab

### GPX Track

The GPX file for the recording.  Drag a `.gpx` file onto the drop area or use the
*Browse* button.  The file is embedded in the project archive on save.

#### Hole repair — Mode
**Key:** `gpx/repair_mode` | **Default:** `none`

Fills time gaps and removes implausible GPS jumps from the track before any processing.

| Value | Behaviour |
|---|---|
| `none` | No repair — track is used as-is |
| `linear` | Straight-line interpolation between the endpoints of each gap |
| `street` | Route via the OSRM public API (`router.project-osrm.org`); falls back silently to linear when the route cannot be fetched |

> The repair runs on every pipeline start and keyframe recalculation. Reconstructed
> segments are flagged internally so the *Shifting pin* option can colour them
> differently in the 3D scene.

#### Hole repair — OSRM profile
**Key:** `gpx/osrm_profile` | **Default:** `driving`  
*Visible only when Mode = `street`.*

Routing profile sent to the OSRM API.

| Value | Use case |
|---|---|
| `driving` | Motorised roads |
| `cycling` | Bike lanes and shared paths |
| `walking` | Footpaths and pedestrian routes |

#### Hole repair — Max speed
**Key:** `gpx/max_speed_kmh` | **Default:** `300` | **Unit:** km/h  
*Visible only when Mode ≠ `none`. Requires GPX timestamps.*

Trackpoints that imply a speed above this threshold are removed as bad GPS readings
before gap-filling begins.

#### Hole repair — Fill above (gap threshold)
**Key:** `gpx/max_gap_s` | **Default:** `30.0` | **Unit:** seconds  
*Visible only when Mode ≠ `none`. Requires GPX timestamps.*

Time gaps longer than this between two valid consecutive trackpoints are filled with
synthetic points using the selected repair mode.

#### Hole repair — Shifting pin
**Key:** `marker/shifting_pin` | **Default:** `false`  
*Visible only when Mode ≠ `none`.*

When enabled, the animated track-position marker gradually shifts from its configured
colour to its complementary colour across reconstructed (gap-filled) segments, then
fades back once the recorded track resumes.  Gives a visual cue that a portion of the
route was inferred rather than recorded.

---

### Photos

A list of geotagged photos to embed as waypoints along the fly-through.  Drag image
files onto the list or use *Add photos…*.  Photos are embedded in the project archive
on save.  The order is determined by their resolved track position, not filename.

---

### Photo matching mode

Controls how photos are mapped to positions on the GPX track.

#### Match mode
**Key:** `match_mode` (stored in project state, not QSettings) | **Default:** `both`

| Value | Behaviour |
|---|---|
| `timestamp` | Match by comparing the photo's EXIF timestamp against GPX track timestamps.  Requires both the GPX and the photos to carry accurate timestamps and a correctly set timezone offset. |
| `gps` | Match by nearest geographic distance using the GPS coordinates embedded in the photo's EXIF data.  Works even when camera clocks are wrong. |
| `both` | Use GPS coordinates as primary; fall back to timestamp for photos without EXIF GPS.  Warns when the two methods disagree beyond the configurable threshold. |

#### Camera clock timezone
**Key:** `render/photo_tz_offset_hours` | **Default:** `0.0` | **Unit:** hours (UTC offset)

EXIF timestamps are stored as local time with no timezone.  Set this to the UTC offset
of the camera clock at the time of the recording (e.g. `+2.0` for UTC+2 / CEST).
Only relevant when matching by timestamp.

---

### Output video

#### Output file path
The destination path for the rendered video.  Use the *Browse…* button or type a path
directly.  The file extension is automatically adjusted to match the container format
chosen in *Pipeline Settings → Output*.

#### Flythrough speed
**Key:** `render/camera_speed_mps` | **Default:** `80.0` | **Unit:** m/s

How fast the virtual camera moves through the 3D scene.  Higher values produce shorter
videos.

| Preset | Value | Typical use |
|---|---|---|
| Hiking | 80 m/s | Foot travel, leisurely pace |
| Cycling | 120 m/s | Bike rides |
| Driving | 320 m/s | Car or motorcycle routes |
| Custom | user-defined | Any exact value between 10 and 1000 m/s |

An estimated video duration is shown next to the control once a GPX track is loaded.

---

## Ribbon tab

Controls the visual style of the 3D track ribbon rendered in the fly-through scene.

### Color — Color mode
**Key:** `ribbon/color_mode` | **Default:** `slope`

| Value | Behaviour |
|---|---|
| `slope` | Colour by terrain gradient: flat → light blue; 20% grade → yellow; ≥ 40% → red |
| `speed` | Colour by recorded GPS speed, scaled between the 5th and 95th percentile of the track: slow → cool blue; medium → cyan/green; fast → orange |

### Appearance — Self-lit
**Key:** `ribbon/self_lit` | **Default:** `false`

| Value | Behaviour |
|---|---|
| `false` | Ribbon emits at strength 2 — vivid but still blends with scene bloom and exposure |
| `true` | Emission strength reduced to 1 so vertex colours map linearly through Filmic tone-mapping, keeping hues fully saturated regardless of sun position or sky brightness |

---

## Fade tab

Adds optional black-clip transitions at the very start and/or end of the video.

### Fade in

#### Fade in — Enabled
**Key:** `clip_effects/fade_in_enabled` | **Default:** `false`

Prepends a fade-in sequence to the video when enabled.

#### Fade in — Black clip duration
**Key:** `clip_effects/fade_in_black_dur` | **Default:** `5.0` | **Unit:** seconds

Duration of the pure-black hold before the luminance ramp begins.

#### Fade in — Fade duration
**Key:** `clip_effects/fade_in_fade_dur` | **Default:** `1.0` | **Unit:** seconds

Duration of the luminance ramp from black to full brightness.

### Fade out

#### Fade out — Enabled
**Key:** `clip_effects/fade_out_enabled` | **Default:** `false`

Appends a fade-out sequence to the video when enabled.

#### Fade out — Black clip duration
**Key:** `clip_effects/fade_out_black_dur` | **Default:** `5.0` | **Unit:** seconds

Duration of the pure-black hold after the luminance ramp completes.

#### Fade out — Fade duration
**Key:** `clip_effects/fade_out_fade_dur` | **Default:** `1.0` | **Unit:** seconds

Duration of the luminance ramp from full brightness to black.

---

## Title tab

Composites a text title onto the video using PIL (no dependency on FFmpeg's `drawtext`
filter).

### Enabled
**Key:** `clip_effects/title_enabled` | **Default:** `false`

### Text
**Key:** `clip_effects/title_text` | **Default:** `""`

Multi-line title text.  Supports `\n` line breaks.

### Font
**Key:** `clip_effects/title_font` | **Default:** `"Noto Serif"`

Font family name.  Resolved to a file path via `fc-match` at render time; the font file
is embedded in the project archive so the title renders identically on other machines.

### Font size
**Key:** `clip_effects/title_font_size` | **Default:** `95` | **Unit:** pt

Point size of the title font at the configured output resolution.

### Anchor
**Key:** `clip_effects/title_anchor` | **Default:** `"bottom-right"`

Where the text block is pinned within the frame.

| Value | Position |
|---|---|
| `top-left` | Upper-left corner |
| `top` | Top-centre |
| `top-right` | Upper-right corner |
| `center-left` | Left-centre |
| `center` | Dead centre |
| `center-right` | Right-centre |
| `bottom-left` | Lower-left corner |
| `bottom` | Bottom-centre |
| `bottom-right` | Lower-right corner |

### Margin
**Key:** `clip_effects/title_margin` | **Default:** `40` | **Unit:** pixels  
*Disabled when Anchor = `center`.*

Distance between the text block and the anchor edge(s) of the frame.

### Alignment
**Key:** `clip_effects/title_alignment` | **Default:** `"right"`

How each line of a multi-line title is aligned within the text block.

| Value | Alignment |
|---|---|
| `left` | Left-align each line |
| `center` | Centre each line |
| `right` | Right-align each line |

### Color
**Key:** `clip_effects/title_color` | **Default:** `"#ffffff"`

Hex colour string for the title text.

### Shadow
**Key:** `clip_effects/title_shadow` | **Default:** `true`

Renders a semi-transparent black drop shadow offset by ~3% of the font size, improving
legibility over bright backgrounds.

### Duration
**Key:** `clip_effects/title_duration` | **Default:** `10.0` | **Unit:** seconds

Total time (including fades) the title remains on screen.

### Title fade in
**Key (enabled):** `clip_effects/title_fade_in_enabled` | **Default:** `true`  
**Key (duration):** `clip_effects/title_fade_in_dur` | **Default:** `3.0` | **Unit:** seconds

When enabled, the title fades in from transparent over this many seconds.  If the
video itself has no fade-in black clip, black frames are prepended automatically so
the title fades in from black rather than over content.

### Title fade out
**Key (enabled):** `clip_effects/title_fade_out_enabled` | **Default:** `true`  
**Key (duration):** `clip_effects/title_fade_out_dur` | **Default:** `3.0` | **Unit:** seconds

When enabled, the title fades out to transparent over this many seconds.

---

## Music tab

Mixes one or more audio files into the video output via FFmpeg.  The audio is trimmed
to the exact video duration.

### Enabled
**Key:** `clip_effects/music_enabled` | **Default:** `false`

### Playlist
**Key:** `clip_effects/music_paths` | **Type:** JSON array of file paths | **Default:** `"[]"`

Ordered list of audio files.  Files are played in order and embedded in the project
archive.  Supported formats: `.mp3`, `.m4a`, `.aac`, `.ogg`, `.flac`, `.wav`, `.opus`.
Drag files directly onto the list to add them; drag within the list to reorder.

### Delay
**Key:** `clip_effects/music_delay` | **Default:** `0.0` | **Unit:** seconds

Silence prepended before the first audio file begins playing.

### Fade in
**Key (enabled):** `clip_effects/music_fade_in_enabled` | **Default:** `false`  
**Key (duration):** `clip_effects/music_fade_in_dur` | **Default:** `1.0` | **Unit:** seconds

Ramps the audio from silence to full volume at the start of playback.

### Fade out
**Key (enabled):** `clip_effects/music_fade_out_enabled` | **Default:** `true`  
**Key (duration):** `clip_effects/music_fade_out_dur` | **Default:** `5.0` | **Unit:** seconds

Ramps the audio to silence at the very end of the video.

### Cross-fade between tracks
**Key (enabled):** `clip_effects/music_crossfade_enabled` | **Default:** `true`  
**Key (duration):** `clip_effects/music_crossfade_dur` | **Default:** `5.0` | **Unit:** seconds

When the playlist contains more than one file, applies an `acrossfade` overlap between
consecutive tracks instead of a hard cut.

### Loop playlist
**Key:** `clip_effects/music_loop` | **Default:** `false`

Repeats the entire playlist from the beginning until the video ends.  Useful for short
music clips on long fly-throughs.

---

## Locality names tab

Queries a [Nominatim](https://nominatim.org/) reverse-geocoding service to obtain the
current place name at regular intervals along the track, then composites it onto the
video as a fade-in/fade-out text overlay.

### Enabled
**Key:** `locality_names/enabled` | **Default:** `false`

Master toggle.  When disabled all other locality-names settings are ignored.

---

### Nominatim service

#### Service
**Key:** `locality_names/service` | **Default:** `"osm"`

Which Nominatim backend to use.

| Value | Description |
|---|---|
| `osm` | Official OSM Nominatim servers (`nominatim.openstreetmap.org`). Subject to the [usage policy](https://operations.osmfoundation.org/policies/nominatim/) (max 1 req/s, no bulk use). |
| `docker` | Local container running `mediagis/nominatim:4.4`.  Requires Docker or Podman.  Radio button is disabled if neither is detected. |
| `custom` | User-supplied base URL (any Nominatim-compatible endpoint). |

#### Custom URL
**Key:** `locality_names/custom_url` | **Default:** `""`  
*Visible only when Service = `custom`.*

Base URL of the custom Nominatim instance, e.g. `http://my-server:8080`.
The `/reverse` path is appended automatically.

#### Docker/Podman — PBF URL
**Key:** `locality_names/docker_pbf_url` | **Default:** `""`  
*Visible only when Service = `docker`.*

URL of the OpenStreetMap PBF extract to load into the container.
The extract must cover the entire track.  For tracks crossing multiple
regions, use a country- or continent-level extract from
[Geofabrik](https://download.geofabrik.de/).

#### Docker/Podman — Port
**Key:** `locality_names/docker_port` | **Default:** `8080`  
*Visible only when Service = `docker`.*

Host port mapped to the container's port 8080.

#### Docker/Podman — Keep data volume
**Key:** `locality_names/docker_keep` | **Default:** `false`  
*Visible only when Service = `docker`.*

When enabled, the container's PostgreSQL data is persisted in the
`georeel-nominatim-data` Docker volume between runs, avoiding a
time-consuming PBF re-import on subsequent uses.  Use *Clean docker
volumes* to remove accumulated data when switching to a different region.

#### Docker/Podman — Container actions
Three buttons manage the container lifecycle:

| Button | Action |
|---|---|
| *Start container* | Runs `docker run -d … mediagis/nominatim:4.4`.  PBF data is imported on first start; the container exposes the API on the configured port. |
| *Stop container* | Runs `docker rm -f georeel-nominatim`. |
| *Clean docker volumes* | Runs `docker volume rm -f georeel-nominatim-data`.  Frees disk space; the next container start will re-import the PBF. |

---

### Sampling and display

#### Check every
**Key:** `locality_names/check_every_s` | **Default:** `60.0` | **Unit:** seconds (track time)

How often to query Nominatim along the route.  This is measured in **track
time** (real GPS elapsed seconds), not in video playback time.  For a 3-hour
hike with the default 60 s interval, the service is queried approximately
180 times.

Consecutive queries that return the same place name are de-duplicated; only
the first occurrence of a new name triggers a visible change in the overlay.

> **Rate limiting:** the official OSM servers allow at most 1 request/second.
> At 60 s intervals this is well within the policy; at very short intervals
> consider switching to a local Docker instance.

#### Detail level
**Key:** `locality_names/detail_level` | **Default:** `"city"`

Granularity of the Nominatim `zoom` parameter (see
[API reference](https://nominatim.org/release-docs/latest/api/Reverse/)).
If the requested level is not available for a location, Nominatim falls back
to a coarser result automatically.

| Value | Nominatim zoom | Typical result |
|---|---|---|
| `village` | 14 | Village, suburb, or hamlet |
| `town` | 12 | Town or large village |
| `city` | 10 | City or municipality |
| `state` | 5 | State, province, or region |
| `country` | 3 | Country name only |

#### Position
**Key:** `locality_names/position` | **Default:** `"bottom-right"`

Where the place-name text is anchored within the video frame.

| Value | Position |
|---|---|
| `top-left` | Upper-left corner |
| `top` | Top-centre |
| `top-right` | Upper-right corner |
| `center-left` | Left-centre |
| `center` | Dead centre |
| `center-right` | Right-centre |
| `bottom-left` | Lower-left corner |
| `bottom` | Bottom-centre |
| `bottom-right` | Lower-right corner |

#### Duration
**Key:** `locality_names/duration` | **Default:** `5.0` | **Unit:** seconds

Total time (including the fixed 1 s fade-in and 1 s fade-out) that each
place name is displayed after entering a new locality.  If the same locality
persists beyond the duration, the overlay hides and reappears only when the
next different name is detected.

When a new locality is detected before the previous overlay's duration
expires, the two labels cross-fade: the old name fades out while the new
name fades in simultaneously.

#### Text color
**Key:** `locality_names/text_color` | **Default:** `"#ffffff"`

Hex colour string for the place-name text.

#### Shadow
**Key:** `locality_names/shadow` | **Default:** `true`

Renders a semi-transparent black drop shadow, improving legibility over
bright satellite imagery or sky.
