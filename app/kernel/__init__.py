"""The contradiction kernel as versioned data, plus what is computed from it.

:mod:`app.recognition` remains the vocabulary of the recognition layer: the
chain, the domain profiles, the product construction. This package holds the
part the reporting pipeline needs to agree on, which is narrower and stricter:
which predicates exist, whether each detects a defect or a coverage gap, what it
weighs, and what it needs in order to run at all.

Everything that emits or aggregates a finding reads the registry. That is what
keeps the findings table and the completeness table drawing from one set.
"""
from __future__ import annotations

from .registry import (
    COVERAGE,
    DEFECT,
    Primitive,
    Registry,
    RegistryError,
    load_registry,
)

__all__ = [
    "COVERAGE",
    "DEFECT",
    "Primitive",
    "Registry",
    "RegistryError",
    "load_registry",
]
