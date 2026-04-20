import base64

from pydantic import BaseModel

from georeel.core.elevation_grid import ElevationGrid


class ElevationGridSchema(BaseModel):
    rows: int
    cols: int
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    data_b64: str  # base64-encoded row-major float32 bytes

    def to_core(self) -> ElevationGrid:
        raw = base64.b64decode(self.data_b64)
        return ElevationGrid.from_bytes(
            raw,
            rows=self.rows,
            cols=self.cols,
            min_lat=self.min_lat,
            max_lat=self.max_lat,
            min_lon=self.min_lon,
            max_lon=self.max_lon,
        )

    @classmethod
    def from_core(cls, grid: ElevationGrid) -> "ElevationGridSchema":
        return cls(
            rows=grid.rows,
            cols=grid.cols,
            min_lat=grid.min_lat,
            max_lat=grid.max_lat,
            min_lon=grid.min_lon,
            max_lon=grid.max_lon,
            data_b64=base64.b64encode(grid.to_bytes()).decode(),
        )
