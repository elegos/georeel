"""Canonical QSettings keys and default values for all render/GPX/output settings.

Both ``core`` modules (camera_path, scene_builder, …) and ``ui`` modules
(render_settings_dialog, main_window, …) import from here so that any change
to a default is made exactly once.
"""
from typing import Any

# ── QSettings keys ────────────────────────────────────────────────────────────

KEY_PATH_SMOOTHING               = "render/path_smoothing"
KEY_HEIGHT_MODE                  = "render/camera_height_mode"
KEY_HEIGHT_OFFSET                = "render/camera_height_offset"
KEY_INTRO_TRACK_LIFT             = "render/intro_track_lift_m"
KEY_ORIENTATION                  = "render/camera_orientation"
KEY_TILT_DEG                     = "render/camera_tilt_deg"
KEY_PHOTO_PAUSE_MODE             = "render/photo_pause_mode"
KEY_PHOTO_PAUSE_DURATION         = "render/photo_pause_duration"
KEY_FPS                          = "render/fps"
KEY_CAMERA_SPEED                 = "render/camera_speed_mps"
KEY_ENGINE                       = "render/engine"
KEY_ASPECT_RATIO                 = "render/aspect_ratio"
KEY_RESOLUTION                   = "render/resolution"
KEY_QUALITY                      = "render/quality"
KEY_PHOTO_TZ_OFFSET              = "render/photo_tz_offset_hours"
KEY_PHOTO_TRANSITION             = "render/photo_transition"
KEY_PHOTO_FILL                   = "render/photo_fill"
KEY_PHOTO_FADE_DURATION          = "render/photo_fade_duration"
KEY_TANGENT_LOOKAHEAD_S          = "render/tangent_lookahead_s"
KEY_TANGENT_WEIGHT               = "render/tangent_weight"
KEY_FRUSTUM_MARGIN_KM            = "render/frustum_margin_km"
KEY_RENDER_SEGMENTS              = "render/n_segments"
KEY_PNG_COMPRESSION              = "render/png_compression"
KEY_INTRO_OVERVIEW_ENABLED       = "render/intro_overview_enabled"
KEY_INTRO_OVERVIEW_DURATION_S    = "render/intro_overview_duration_s"
KEY_DYNAMIC_SPEED_ENABLED        = "render/dynamic_speed_enabled"
KEY_DYNAMIC_SPEED_FACTOR         = "render/dynamic_speed_factor"
KEY_DYNAMIC_SPEED_RAMP_S         = "render/dynamic_speed_ramp_s"
KEY_AUTO_ZOOM_ENABLED            = "render/auto_zoom_enabled"
KEY_AUTO_ZOOM_CURVATURE_DEG_PER_M = "render/auto_zoom_curvature_deg_per_m"

KEY_GPX_REPAIR_MODE              = "gpx/repair_mode"
KEY_GPX_OSRM_PROFILE             = "gpx/osrm_profile"
KEY_GPX_MAX_SPEED_KMH            = "gpx/max_speed_kmh"
KEY_GPX_MAX_GAP_S                = "gpx/max_gap_s"
KEY_GPX_MAX_JUMP_KM              = "gpx/max_jump_km"

KEY_CACHE_USE_CUSTOM_DIR         = "cache/use_custom_dir"
KEY_CACHE_BASE_DIR               = "cache/base_dir"

KEY_PIN_COLOR                    = "pins/color"
KEY_PIN_CUSTOM_COLOR             = "pins/custom_color"
KEY_MARKER_COLOR                 = "marker/color"
KEY_MARKER_CUSTOM_COLOR          = "marker/custom_color"
KEY_MARKER_SHIFTING_PIN          = "marker/shifting_pin"
KEY_RIBBON_COLOR_MODE            = "ribbon/color_mode"
KEY_RIBBON_SELF_LIT              = "ribbon/self_lit"

KEY_IMAGERY_PROVIDER             = "imagery/provider"
KEY_IMAGERY_QUALITY              = "imagery/quality"
KEY_IMAGERY_API_KEY              = "imagery/api_key"
KEY_IMAGERY_CUSTOM_URL           = "imagery/custom_url"
KEY_IMAGERY_FETCH_MODE           = "imagery/fetch_mode"

KEY_CONTAINER                    = "output/container"
KEY_CODEC                        = "output/codec"
KEY_ENCODER                      = "output/encoder"
KEY_OUTPUT_CQ                    = "output/cq"
KEY_OUTPUT_PRESET                = "output/preset"

# ── Default values ────────────────────────────────────────────────────────────

DEFAULTS: dict[str, Any] = {
    KEY_PATH_SMOOTHING:               "spline",
    KEY_HEIGHT_MODE:                  "dem_fixed",
    KEY_HEIGHT_OFFSET:                2000,
    KEY_INTRO_TRACK_LIFT:             5,
    KEY_ORIENTATION:                  "tangent",
    KEY_TILT_DEG:                     45,
    KEY_PHOTO_PAUSE_MODE:             "hold",
    KEY_PHOTO_PAUSE_DURATION:         3.0,
    KEY_FPS:                          30,
    KEY_CAMERA_SPEED:                 80.0,
    KEY_ENGINE:                       "eevee",
    KEY_ASPECT_RATIO:                 "landscape",
    KEY_RESOLUTION:                   "1080p",
    KEY_QUALITY:                      "medium",
    KEY_PHOTO_TRANSITION:             "fade",
    KEY_PHOTO_FILL:                   "blurred",
    KEY_PHOTO_FADE_DURATION:          0.5,
    KEY_TANGENT_LOOKAHEAD_S:          60.0,
    KEY_TANGENT_WEIGHT:               "linear",
    KEY_FRUSTUM_MARGIN_KM:            50.0,
    KEY_RENDER_SEGMENTS:              1,
    KEY_PNG_COMPRESSION:              1,
    KEY_INTRO_OVERVIEW_ENABLED:       False,
    KEY_INTRO_OVERVIEW_DURATION_S:    3.0,
    KEY_DYNAMIC_SPEED_ENABLED:        False,
    KEY_DYNAMIC_SPEED_FACTOR:         1.33,
    KEY_DYNAMIC_SPEED_RAMP_S:         4.0,
    KEY_AUTO_ZOOM_ENABLED:            False,
    KEY_AUTO_ZOOM_CURVATURE_DEG_PER_M: 0.5,
    KEY_PHOTO_TZ_OFFSET:              0.0,
    KEY_GPX_REPAIR_MODE:              "none",
    KEY_GPX_MAX_SPEED_KMH:            300,
    KEY_GPX_MAX_GAP_S:                30.0,
    KEY_GPX_MAX_JUMP_KM:              50.0,
    KEY_CACHE_USE_CUSTOM_DIR:         False,
    KEY_CACHE_BASE_DIR:               "",
    KEY_PIN_COLOR:                    "ForestGreen",
    KEY_PIN_CUSTOM_COLOR:             "#228B22",
    KEY_MARKER_COLOR:                 "LightBlue",
    KEY_MARKER_CUSTOM_COLOR:          "#ADD8E6",
    KEY_MARKER_SHIFTING_PIN:          False,
    KEY_RIBBON_COLOR_MODE:            "slope",
    KEY_RIBBON_SELF_LIT:              False,
    KEY_IMAGERY_PROVIDER:             "esri_world",
    KEY_IMAGERY_QUALITY:              "standard",
    KEY_IMAGERY_API_KEY:              "",
    KEY_IMAGERY_CUSTOM_URL:           "",
    KEY_IMAGERY_FETCH_MODE:           "prefetch",
    KEY_CONTAINER:                    "mkv",
    KEY_CODEC:                        "h265",
    KEY_ENCODER:                      "libx265",
    KEY_OUTPUT_CQ:                    28,
    KEY_OUTPUT_PRESET:                "medium",
}
