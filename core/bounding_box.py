import math
from dataclasses import dataclass

_M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def expand(self, margin_m: float) -> "BoundingBox":
        """Return a new BoundingBox expanded by *margin_m* on every side."""
        mid_lat = (self.min_lat + self.max_lat) / 2
        lat_delta = margin_m / _M_PER_DEG_LAT
        lon_delta = margin_m / (_M_PER_DEG_LAT * math.cos(math.radians(mid_lat)))
        return BoundingBox(
            min_lat=self.min_lat - lat_delta,
            max_lat=self.max_lat + lat_delta,
            min_lon=self.min_lon - lon_delta,
            max_lon=self.max_lon + lon_delta,
        )

    def __str__(self) -> str:
        return (
            f"({self.min_lat:.5f}, {self.min_lon:.5f}) → "
            f"({self.max_lat:.5f}, {self.max_lon:.5f})"
        )
