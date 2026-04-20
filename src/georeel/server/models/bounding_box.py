from pydantic import BaseModel

from georeel.core.bounding_box import BoundingBox


class BoundingBoxSchema(BaseModel):
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def to_core(self) -> BoundingBox:
        return BoundingBox(
            min_lat=self.min_lat,
            max_lat=self.max_lat,
            min_lon=self.min_lon,
            max_lon=self.max_lon,
        )

    @classmethod
    def from_core(cls, bb: BoundingBox) -> "BoundingBoxSchema":
        return cls(
            min_lat=bb.min_lat,
            max_lat=bb.max_lat,
            min_lon=bb.min_lon,
            max_lon=bb.max_lon,
        )
