from dataclasses import dataclass


@dataclass
class CameraKeyframe:
    """Camera pose at a single animation frame.

    Coordinates are in metres in the scene coordinate system:
        X = east  (0 → lon_m)
        Y = north (0 → lat_m)
        Z = elevation above sea level + camera height offset

    The look-at point is 100 m ahead of the camera along its forward vector,
    at the elevation the camera should be aiming at.
    """

    frame: int
    x: float
    y: float
    z: float
    look_at_x: float
    look_at_y: float
    look_at_z: float
    is_pause: bool = False        # True for pause frames inserted at photo waypoints
    photo_path: str | None = None # path to the photo displayed during this pause
