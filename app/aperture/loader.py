"""Load, validate and derive from the Aperture kernel manifest.

The loader owns everything the manifest deliberately does not restate:

* the transitive precondition set for each failure, from the cumulative stratum
  ordering plus that failure's own ``requires``;
* the digest of the file as loaded, so a resolution can record which manifest
  produced it and a cached resolution invalidates when the manifest changes;
* the translation between this manifest's instrument names and the enum in
  :mod:`app.recognition`, which is what the drift test pins the two together on.

Nothing here reads an ontology or runs a detector.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import STRATUM_ORDER, FailureSpec, Manifest, StratumId

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "manifest.yaml"

# app.recognition.Instrument value -> manifest instrument id. The two vocabularies
# differ in two names for historical reasons; this is the single place that knows.
INSTRUMENT_FROM_RECOGNITION: dict[str, str] = {
    "reasoner": "dl_reasoner",
    "structural": "structural",
    "world_data": "world_evidence",
    "process_model": "process_model",
}


class ManifestError(Exception):
    """The manifest is missing, unparseable, or fails validation.

    Raised at load time on purpose. A manifest defect must stop the run before
    anything is resolved, not surface as a strange verdict later.
    """


@dataclass(frozen=True)
class LoadedManifest:
    """A validated manifest plus everything derived from it."""

    manifest: Manifest
    digest: str
    path: Path
    # failure id -> the full transitive precondition set, in stratum order then
    # the failure's own extras. Order is stable so the resolver can report the
    # *first* unsatisfied precondition deterministically.
    preconditions: dict[str, tuple[str, ...]]

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def failures(self) -> tuple[FailureSpec, ...]:
        return self.manifest.failures

    def by_id(self, failure_id: str) -> FailureSpec:
        for f in self.manifest.failures:
            if f.id == failure_id:
                return f
        raise KeyError(failure_id)

    def ids(self) -> tuple[str, ...]:
        return tuple(f.id for f in self.manifest.failures)

    def stratum(self, stratum_id: StratumId):
        for s in self.manifest.strata:
            if s.id == stratum_id:
                return s
        raise KeyError(stratum_id)

    def gated_by_chain(self, failure_id: str) -> bool:
        return self.stratum(self.by_id(failure_id).stratum).gated_by_chain

    def preconditions_for(self, failure_id: str) -> tuple[str, ...]:
        return self.preconditions[failure_id]

    def is_human(self, failure_id: str) -> bool:
        """True when the failure has no mechanical detector.

        Callers must send these to IN_SCOPE_HUMAN. Resolving a null detector to
        out of scope would make the framework's non-mechanical checks disappear
        silently, which inverts its purpose.
        """
        return self.by_id(failure_id).is_human

    def client_labels(self) -> dict[str, str]:
        """failure id -> client label. The only mapping a client surface may hold."""
        return {f.id: f.client_label for f in self.manifest.failures}


def _cumulative_preconditions(manifest: Manifest) -> dict[StratumId, tuple[str, ...]]:
    """Stratum id -> every stratum precondition at or below it, in order.

    Strata are cumulative: a failure at stratum C requires the preconditions of
    A and B as well as its own.
    """
    by_id = {s.id: s for s in manifest.strata}
    out: dict[StratumId, tuple[str, ...]] = {}
    running: list[str] = []
    for sid in STRATUM_ORDER:
        running.append(by_id[sid].precondition)
        out[sid] = tuple(running)
    return out


def _derive_preconditions(manifest: Manifest) -> dict[str, tuple[str, ...]]:
    cumulative = _cumulative_preconditions(manifest)
    out: dict[str, tuple[str, ...]] = {}
    for f in manifest.failures:
        ordered = list(cumulative[f.stratum])
        for extra in f.requires:
            if extra not in ordered:
                ordered.append(extra)
        out[f.id] = tuple(ordered)
    return out


def load_manifest(path: str | Path | None = None) -> LoadedManifest:
    """Read and validate the manifest. Raises :class:`ManifestError` on any defect.

    Not cached. The manifest is small, it is read once per resolution, and a
    stale cache here would be a silent correctness bug of exactly the kind this
    file exists to prevent.
    """
    p = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    try:
        raw_bytes = p.read_bytes()
    except OSError as e:
        raise ManifestError(f"cannot read manifest at {p}: {e}") from e

    digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError(f"manifest at {p} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"manifest at {p} must be a mapping, got {type(data).__name__}")

    try:
        manifest = Manifest.model_validate(data)
    except Exception as e:
        raise ManifestError(f"manifest at {p} failed validation: {e}") from e

    return LoadedManifest(
        manifest=manifest,
        digest=digest,
        path=p,
        preconditions=_derive_preconditions(manifest),
    )
