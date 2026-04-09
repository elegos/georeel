from dataclasses import dataclass


@dataclass
class MatchResult:
    photo_path: str
    trackpoint_index: int | None = None
    error: str | None = None
    warning: str | None = None
    # "pre"   — photo taken before the first trackpoint timestamp
    # "track" — photo falls within the track time range (default)
    # "post"  — photo taken after the last trackpoint timestamp
    position: str = "track"
    # Seconds from the first trackpoint; used to order pre/post photos
    sort_key: float = 0.0

    @property
    def ok(self) -> bool:
        return self.trackpoint_index is not None and self.error is None

    @property
    def status_text(self) -> str:
        if self.error:
            return self.error
        if self.warning:
            return f"⚠ {self.warning}"
        if self.ok:
            return f"✓ trackpoint #{self.trackpoint_index}"
        return "—"
