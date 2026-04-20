from datetime import datetime

from pydantic import BaseModel

from georeel.core.trackpoint import Trackpoint


class TrackpointSchema(BaseModel):
    latitude: float
    longitude: float
    elevation: float | None
    timestamp: datetime | None
    is_reconstructed: bool = False

    def to_core(self) -> Trackpoint:
        return Trackpoint(
            latitude=self.latitude,
            longitude=self.longitude,
            elevation=self.elevation,
            timestamp=self.timestamp,
            is_reconstructed=self.is_reconstructed,
        )

    @classmethod
    def from_core(cls, tp: Trackpoint) -> "TrackpointSchema":
        return cls(
            latitude=tp.latitude,
            longitude=tp.longitude,
            elevation=tp.elevation,
            timestamp=tp.timestamp,
            is_reconstructed=tp.is_reconstructed,
        )
