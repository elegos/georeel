from pydantic import BaseModel

from georeel.core.camera_keyframe import CameraKeyframe


class CameraKeyframeSchema(BaseModel):
    frame: int
    x: float
    y: float
    z: float
    look_at_x: float
    look_at_y: float
    look_at_z: float
    is_pause: bool = False
    photo_path: str | None = None
    is_intro: bool = False

    def to_core(self) -> CameraKeyframe:
        return CameraKeyframe(
            frame=self.frame,
            x=self.x,
            y=self.y,
            z=self.z,
            look_at_x=self.look_at_x,
            look_at_y=self.look_at_y,
            look_at_z=self.look_at_z,
            is_pause=self.is_pause,
            photo_path=self.photo_path,
            is_intro=self.is_intro,
        )

    @classmethod
    def from_core(cls, kf: CameraKeyframe) -> "CameraKeyframeSchema":
        return cls(
            frame=kf.frame,
            x=kf.x,
            y=kf.y,
            z=kf.z,
            look_at_x=kf.look_at_x,
            look_at_y=kf.look_at_y,
            look_at_z=kf.look_at_z,
            is_pause=kf.is_pause,
            photo_path=kf.photo_path,
            is_intro=kf.is_intro,
        )
