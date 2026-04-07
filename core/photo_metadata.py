from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PhotoMetadata:
    path: str
    timestamp: datetime | None
    latitude: float | None
    longitude: float | None

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def has_timestamp(self) -> bool:
        return self.timestamp is not None
