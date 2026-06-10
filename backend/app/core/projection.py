from __future__ import annotations

from dataclasses import dataclass


EQUIRECTANGULAR_SCALE = 8.0


@dataclass(frozen=True)
class ProjectedPoint:
    x: float
    y: float


def project_lon_lat_equirectangular(
    lon: float,
    lat: float,
    *,
    scale: float = EQUIRECTANGULAR_SCALE,
) -> ProjectedPoint:
    """Project lon/lat into a deterministic planar x/y space for exploratory layouts."""

    return ProjectedPoint(
        x=round(float(lon) * scale, 6),
        y=round(float(-lat) * scale, 6),
    )
