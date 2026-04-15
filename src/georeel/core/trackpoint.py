from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Trackpoint:
    latitude: float
    longitude: float
    elevation: float | None
    timestamp: datetime | None
    is_reconstructed: bool = False
