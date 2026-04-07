from .photo_metadata import PhotoMetadata


class PhotoStore:
    """Singleton container holding EXIF-enriched metadata for all selected photos."""

    _instance: "PhotoStore | None" = None

    @classmethod
    def instance(cls) -> "PhotoStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._photos: list[PhotoMetadata] = []

    def add(self, metadata: PhotoMetadata) -> None:
        if not any(p.path == metadata.path for p in self._photos):
            self._photos.append(metadata)

    def update_timestamp(self, path: str, timestamp) -> None:
        self._photos = [
            PhotoMetadata(
                path=p.path,
                timestamp=timestamp,
                latitude=p.latitude,
                longitude=p.longitude,
            ) if p.path == path else p
            for p in self._photos
        ]

    def remove(self, path: str) -> None:
        self._photos = [p for p in self._photos if p.path != path]

    def all(self) -> list[PhotoMetadata]:
        return list(self._photos)

    def clear(self) -> None:
        self._photos = []

    @property
    def all_have_timestamp(self) -> bool:
        return bool(self._photos) and all(p.has_timestamp for p in self._photos)

    @property
    def all_have_gps(self) -> bool:
        return bool(self._photos) and all(p.has_gps for p in self._photos)
