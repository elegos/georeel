from datetime import datetime

from pydantic import BaseModel

from georeel.core.gpx_stats import GpxStats


class GpxStatsSchema(BaseModel):
    point_count: int
    start_time: datetime | None
    end_time: datetime | None
    duration_s: float | None
    total_distance_m: float
    avg_speed_kmh: float | None
    max_speed_kmh: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None
    elevation_gain_m: float
    elevation_loss_m: float

    @classmethod
    def from_core(cls, stats: GpxStats) -> "GpxStatsSchema":
        return cls(
            point_count=stats.point_count,
            start_time=stats.start_time,
            end_time=stats.end_time,
            duration_s=stats.duration.total_seconds() if stats.duration else None,
            total_distance_m=stats.total_distance_m,
            avg_speed_kmh=stats.avg_speed_kmh,
            max_speed_kmh=stats.max_speed_kmh,
            min_elevation_m=stats.min_elevation_m,
            max_elevation_m=stats.max_elevation_m,
            elevation_gain_m=stats.elevation_gain_m,
            elevation_loss_m=stats.elevation_loss_m,
        )
