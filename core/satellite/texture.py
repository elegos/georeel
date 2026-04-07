import io
from dataclasses import dataclass

from PIL import Image


@dataclass
class SatelliteTexture:
    """A stitched, georeferenced RGB satellite image."""

    image: Image.Image
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    # ------------------------------------------------------------------
    # Serialisation — stored as satellite/texture.png inside the ZIP
    # ------------------------------------------------------------------

    def to_png_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.image.convert("RGB").save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    @classmethod
    def from_png_bytes(
        cls,
        data: bytes,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
    ) -> "SatelliteTexture":
        buf = io.BytesIO(data)
        image = Image.open(buf)
        image.load()
        return cls(
            image=image.convert("RGB"),
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
        )
