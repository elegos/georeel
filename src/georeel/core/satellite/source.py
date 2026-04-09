from abc import ABC, abstractmethod

from ..bounding_box import BoundingBox
from .texture import SatelliteTexture


class SatelliteSource(ABC):
    """Common interface for all satellite/imagery backends.

    To add a new backend, subclass this and implement `name` and `fetch`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name shown in the UI."""

    @abstractmethod
    def fetch(self, bbox: BoundingBox) -> SatelliteTexture:
        """Download imagery for *bbox* and return a stitched texture."""
