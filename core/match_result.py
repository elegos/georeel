from dataclasses import dataclass


@dataclass
class MatchResult:
    photo_path: str
    trackpoint_index: int | None = None
    error: str | None = None
    warning: str | None = None

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
