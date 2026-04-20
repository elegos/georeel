from datetime import datetime

from pydantic import BaseModel

from georeel.core.photo_metadata import PhotoMetadata


class PhotoMetadataSchema(BaseModel):
    photo_id: str
    path: str  # absolute server-side path
    timestamp: datetime | None
    latitude: float | None
    longitude: float | None

    def to_core(self) -> PhotoMetadata:
        return PhotoMetadata(
            path=self.path,
            timestamp=self.timestamp,
            latitude=self.latitude,
            longitude=self.longitude,
        )
