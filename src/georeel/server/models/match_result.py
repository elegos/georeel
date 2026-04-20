from pydantic import BaseModel

from georeel.core.match_result import MatchResult


class MatchResultSchema(BaseModel):
    photo_path: str
    trackpoint_index: int | None = None
    error: str | None = None
    warning: str | None = None
    position: str = "track"
    sort_key: float = 0.0

    def to_core(self) -> MatchResult:
        return MatchResult(
            photo_path=self.photo_path,
            trackpoint_index=self.trackpoint_index,
            error=self.error,
            warning=self.warning,
            position=self.position,
            sort_key=self.sort_key,
        )

    @classmethod
    def from_core(cls, mr: MatchResult) -> "MatchResultSchema":
        return cls(
            photo_path=mr.photo_path,
            trackpoint_index=mr.trackpoint_index,
            error=mr.error,
            warning=mr.warning,
            position=mr.position,
            sort_key=mr.sort_key,
        )
