"""
Registry of supported satellite imagery providers and quality tiers.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    label: str
    url_template: str   # {z}/{x}/{y} xyz format; use {api_key} for key substitution
    max_zoom: int
    requires_key: bool
    key_label: str = ""
    attribution: str = ""


PROVIDERS: tuple[ProviderConfig, ...] = (
    ProviderConfig(
        id="esri_world",
        label="ESRI World Imagery (free)",
        url_template=(
            "https://server.arcgisonline.com/ArcGIS/rest/services"
            "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        max_zoom=19,
        requires_key=False,
        attribution="© Esri, Maxar, Earthstar Geographics",
    ),
    ProviderConfig(
        id="esri_clarity",
        label="ESRI Clarity (free, beta, higher detail)",
        url_template=(
            "https://clarity.maptiles.arcgis.com/arcgis/rest/services"
            "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        max_zoom=19,
        requires_key=False,
        attribution="© Esri, Maxar, Earthstar Geographics",
    ),
    ProviderConfig(
        id="maptiler_satellite",
        label="MapTiler Satellite (free API key, zoom 20)",
        url_template=(
            "https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key={api_key}"
        ),
        max_zoom=20,
        requires_key=True,
        key_label="MapTiler API key:",
        attribution="© MapTiler, © Maxar",
    ),
    ProviderConfig(
        id="custom",
        label="Custom XYZ URL",
        url_template="",
        max_zoom=19,
        requires_key=False,
        attribution="",
    ),
)

_BY_ID: dict[str, ProviderConfig] = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> ProviderConfig:
    return _BY_ID.get(provider_id, PROVIDERS[0])


# Quality tier → target XYZ zoom level.
# Each step doubles resolution: zoom 13 ≈ 19 m/px, 15 ≈ 5 m/px, 17 ≈ 1.2 m/px.
# The same zoom is used regardless of track size, so "Very High" always means
# the same ground resolution whether the track is 5 km or 500 km.
QUALITY_ZOOM: dict[str, int] = {
    "standard":  13,
    "high":      15,
    "very_high": 17,
}
