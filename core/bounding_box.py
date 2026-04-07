from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def __str__(self) -> str:
        return (
            f"({self.min_lat:.5f}, {self.min_lon:.5f}) → "
            f"({self.max_lat:.5f}, {self.max_lon:.5f})"
        )
