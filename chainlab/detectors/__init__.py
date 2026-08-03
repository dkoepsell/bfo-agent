"""Detector registry: kernel id -> detector callable."""
from __future__ import annotations

from .stratum_d import detect_kd3

DETECTORS = {
    "K-D3": detect_kd3,
}

__all__ = ["DETECTORS", "detect_kd3"]
