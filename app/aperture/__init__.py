"""Aperture: what a review of this artifact can and cannot establish.

Aperture computes, before any detector runs, which structural failures a given
artifact is *capable* of exhibiting. It answers "what can this review establish?"
rather than "what is wrong with this artifact". It detects nothing on its own.

The point is that a negative result must be defensible by construction. A check
that could not have fired is reported as untested, never as clean, and the
distinction is computed rather than written by a reviewer.

The vocabulary (twelve kernel primitives, seven chain loci, domain profiles)
lives in :mod:`app.recognition` and is not duplicated here. This package adds
the precondition probe, the resolver, and the projection boundary between
internal and client output.
"""
from __future__ import annotations

from .loader import ManifestError, load_manifest

__all__ = ["ManifestError", "load_manifest"]
