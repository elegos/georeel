"""
Computes the ground-visible margin around the track bounding box given
the camera parameters, so that DEM and satellite imagery cover everything
the camera can see during the fly-through.

Camera model matches render_frames.py:
  - Blender sensor width  : 36 mm (full-frame default)
  - Focal length          : 35 mm
  - Aspect ratio          : 16:9 (standard HD/4K output)
  - Tilt                  : downward from horizontal, degrees
"""

import math

_SENSOR_W_MM = 36.0
_FOCAL_MM    = 35.0
_ASPECT      = 16 / 9
_MAX_VIEW_M  = 50_000.0   # default cap: never expand by more than 50 km
_MIN_VIEW_M  =    500.0   # always expand by at least 500 m


def frustum_margin(height_m: float, tilt_deg: float,
                   max_view_m: float = _MAX_VIEW_M) -> float:
    """Return the margin in metres to expand the track bbox on every side.

    The margin equals the farthest ground point the camera can see, which
    is on the top edge of the frame (shallowest ray). The camera can face
    any direction along the track, so the margin is applied uniformly.
    """
    # Vertical FOV for the 16:9 sensor/lens combination
    hfov = 2 * math.atan(_SENSOR_W_MM / 2 / _FOCAL_MM)
    vfov = 2 * math.atan(_SENSOR_W_MM / _ASPECT / 2 / _FOCAL_MM)

    # Angle below horizontal of the top edge of the frame
    top_ray_down = math.radians(tilt_deg) - vfov / 2

    if top_ray_down <= 0:
        # Top of frame is at or above the horizon — very large view distance
        view_dist = max_view_m
    else:
        view_dist = height_m / math.tan(top_ray_down)

    view_dist = max(_MIN_VIEW_M, min(max_view_m, view_dist))

    # The side extent at that distance is narrower, but since the camera
    # can face any direction we use the forward distance as the uniform margin.
    return view_dist
