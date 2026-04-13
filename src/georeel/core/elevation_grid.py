from dataclasses import dataclass

import numpy as np


@dataclass
class ElevationGrid:
    """A regular lat/lon grid of elevation values (metres).

    Row 0 corresponds to max_lat (north), the last row to min_lat (south).
    Column 0 corresponds to min_lon (west), the last column to max_lon (east).
    """

    data: np.ndarray   # float32, shape (rows, cols)
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    @property
    def rows(self) -> int:
        return self.data.shape[0]

    @property
    def cols(self) -> int:
        return self.data.shape[1]

    def elevation_at(self, lat: float, lon: float) -> float:
        """Bilinearly interpolated elevation at an arbitrary (lat, lon)."""
        row_f = (self.max_lat - lat) / (self.max_lat - self.min_lat) * (self.rows - 1)
        col_f = (lon - self.min_lon) / (self.max_lon - self.min_lon) * (self.cols - 1)
        row_f = float(np.clip(row_f, 0, self.rows - 1))
        col_f = float(np.clip(col_f, 0, self.cols - 1))

        r0, c0 = int(row_f), int(col_f)
        r1, c1 = min(r0 + 1, self.rows - 1), min(c0 + 1, self.cols - 1)
        dr, dc = row_f - r0, col_f - c0

        return float(
            self.data[r0, c0] * (1 - dr) * (1 - dc)
            + self.data[r1, c0] * dr * (1 - dc)
            + self.data[r0, c1] * (1 - dr) * dc
            + self.data[r1, c1] * dr * dc
        )

    def elevation_at_batch(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Vectorised bilinear interpolation for arrays of (lat, lon) pairs.

        Same formula as ``elevation_at`` but operates on entire arrays at once
        using numpy indexing — eliminates the per-point Python overhead.
        """
        row_f = np.clip(
            (self.max_lat - lats) / (self.max_lat - self.min_lat) * (self.rows - 1),
            0, self.rows - 1,
        )
        col_f = np.clip(
            (lons - self.min_lon) / (self.max_lon - self.min_lon) * (self.cols - 1),
            0, self.cols - 1,
        )
        r0 = row_f.astype(np.intp)
        c0 = col_f.astype(np.intp)
        r1 = np.minimum(r0 + 1, self.rows - 1)
        c1 = np.minimum(c0 + 1, self.cols - 1)
        dr = row_f - r0
        dc = col_f - c0
        return (
            self.data[r0, c0] * (1 - dr) * (1 - dc)
            + self.data[r1, c0] * dr       * (1 - dc)
            + self.data[r0, c1] * (1 - dr) * dc
            + self.data[r1, c1] * dr       * dc
        )

    # ------------------------------------------------------------------
    # Serialisation — v2 (ZIP): raw binary
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        return self.data.astype(np.float32).tobytes()

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        rows: int,
        cols: int,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
    ) -> "ElevationGrid":
        arr = np.frombuffer(data, dtype=np.float32).reshape(rows, cols).copy()
        return cls(data=arr, min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon)

