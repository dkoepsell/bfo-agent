"""Ontology registry: discovers and loads all ontologies under the library.

Replaces the phase-1 model in which a single OntologyManager was a module
global in orchestrator.py. After phase 2, every manager lookup goes through
the registry, which tracks (a) the set of loaded ontologies and (b) the
currently active one.

Layout assumption (established in phase 1):

    ontology/
      bfo.owl
      active.txt                     <- name of the active ontology
      library/
        <name>/
          working.owl
          manifest.json
          seed/
            *.ttl

Each subdirectory of library/ that contains manifest.json is treated as
a loadable ontology. Directories starting with "_" (e.g. _archive/) are
ignored.

Design notes:
- Eager loading. Every ontology is loaded at registry construction time.
  This is fine for a library of a few ontologies but becomes expensive past
  ten. Lazy loading is a future phase (2.5 or later) if needed.
- The registry is constructed once at app startup and held by the
  orchestrator. It is not global module state.
- Thread safety: read access (active_manager, get, list_ontologies) is
  safe to call concurrently after construction. Mutations added in
  phase 4 (create/finalize/activate) will acquire the orchestrator lock.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from . import config
from .ontology_manager import OntologyManager

log = logging.getLogger(__name__)


class OntologyNotFoundError(KeyError):
    """Raised when a lookup for an ontology name fails."""


class FinalizeVerificationError(RuntimeError):
    """finalize() refused: the ontology carries commits that skipped the
    per-claim full verify and the fresh full-graph certificate failed
    (SPEC-bfo-agent-speed.md change 6). Carries the verify_full report."""

    def __init__(self, name: str, report: dict):
        self.report = report
        super().__init__(
            f"cannot finalize {name!r}: full-graph verification failed: "
            f"{report.get('detail')}"
        )


class OntologyRegistry:
    def __init__(
        self,
        bfo_path: Path,
        library_root: Path,
        active_name: str,
    ):
        self._bfo_path = Path(bfo_path)
        self._library_root = Path(library_root)
        self._active_name = active_name
        self._managers: dict[str, OntologyManager] = {}
        self._manifests: dict[str, dict] = {}
        self._discover_and_load()

    # ------------------------------------------------------------------
    # Construction-time discovery
    # ------------------------------------------------------------------

    def _discover_and_load(self) -> None:
        if not self._library_root.exists():
            raise FileNotFoundError(
                f"Library root not found: {self._library_root}. "
                f"Did phase 1 migration run?"
            )

        for entry in sorted(self._library_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                # _archive/ and future underscore-prefixed dirs are hidden
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.exists():
                print(f"[registry] skipping {entry.name}: no manifest.json")
                continue
            try:
                self._load_one(entry)
            except Exception as e:
                print(f"[registry] failed to load {entry.name}: {e}")

        if self._active_name not in self._managers:
            raise RuntimeError(
                f"Active ontology '{self._active_name}' not found in library. "
                f"Loaded: {sorted(self._managers)}"
            )

    def _load_one(self, entry: Path) -> None:
        name = entry.name
        manifest = json.loads((entry / "manifest.json").read_text())

        working_path = entry / "working.owl"
        # working.owl may not exist yet for freshly-created ontologies;
        # OntologyManager will bootstrap it from BFO + seeds on first load.
        # We only require the containing directory to exist.

        # Pick a seed file to pass to OntologyManager. The manager's
        # _apply_seed scans the seed directory for all .ttl files, so the
        # specific path only matters for resolving the seed directory.
        seed_dir = entry / "seed"
        seed_path: Optional[Path] = None
        if seed_dir.exists():
            ttls = sorted(seed_dir.glob("*.ttl"))
            if ttls:
                seed_path = ttls[0]

        mgr = OntologyManager(
            bfo_path=self._bfo_path,
            working_path=working_path,
            seed_path=seed_path,
        )
        self._managers[name] = mgr
        self._manifests[name] = manifest
        print(
            f"[registry] loaded {name}: "
            f"{len(list(mgr.working.classes()))} classes, "
            f"{len(list(mgr.working.individuals()))} individuals"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def active_name(self) -> str:
        return self._active_name

    def active_manager(self) -> OntologyManager:
        """Return the OntologyManager for the active (writable) ontology."""
        return self._managers[self._active_name]

    def get(self, name: str) -> OntologyManager:
        """Return the manager for any loaded ontology. Raises if unknown."""
        if name not in self._managers:
            raise OntologyNotFoundError(name)
        return self._managers[name]

    def fidelity(self, name: str | None = None) -> str:
        """Resolve the extraction-fidelity mode for an ontology (FM-1/FM-2).

        Reads the ``"fidelity"`` field of the manifest; an absent or invalid
        value always means ``"curated"`` (today's behavior). ``name=None``
        resolves the active ontology.
        """
        if name is None:
            name = self._active_name
        manifest = self._manifests.get(name) or {}
        value = manifest.get("fidelity")
        if value in ("faithful", "curated"):
            return value
        if value is not None:
            log.warning(
                "Ontology %s has invalid fidelity %r; treating as curated.",
                name, value,
            )
        return "curated"

    def recognition_profile(self, name: str | None = None) -> dict:
        """Resolve the recognition profile for an ontology (SPEC P2).

        Reads the ``"recognition"`` block of the manifest. An absent or unknown
        domain resolves to the scientific-reference control: no chain, strata
        A-C only, Stratum D off. We never infer an institutional profile from
        the source -- that would smuggle in acts the artifact does not have.
        """
        from . import recognition as rec

        if name is None:
            name = self._active_name
        elif name not in self._manifests:
            raise OntologyNotFoundError(name)
        manifest = self._manifests.get(name) or {}
        block = manifest.get("recognition") or {}
        domain = block.get("domain")
        profile = rec.profile_for(domain)
        if domain and profile.key != str(domain).strip().lower():
            log.warning(
                "Ontology %s declares unknown recognition domain %r; "
                "treating as %s.", name, domain, profile.key,
            )
        act = block.get("act_thickness") or profile.act_thickness
        repair = block.get("repair") or profile.repair
        if act != profile.act_thickness or repair != profile.repair:
            # Operator override of the failure geometry (5.3).
            from dataclasses import replace
            profile = replace(profile, act_thickness=act, repair=repair)
        return {
            "domain": profile.key,
            "name": profile.name,
            "authority": block.get("authority") or profile.authority,
            "act_thickness": profile.act_thickness,
            "repair": profile.repair,
            "system_class": profile.system_class,
            "has_chain": profile.has_chain,
            "active_strata": list(profile.active_strata),
            "stratum_d_thin": profile.stratum_d_thin,
            "active_primitives": list(rec.active_primitives(profile)),
            "declared": bool(block),
        }

    def set_recognition_profile(self, name: str, domain: str,
                                authority: str | None = None,
                                act_thickness: str | None = None,
                                repair: str | None = None) -> dict:
        """Declare the recognition profile of an ontology and persist it.

        The domain is operator-declared, chosen from the twelve rows of Table 1
        (plus the scientific-reference control); inference is deliberately not
        offered here.
        """
        from . import recognition as rec

        if name not in self._managers:
            raise OntologyNotFoundError(name)
        key = str(domain or "").strip().lower()
        if key not in rec.DOMAIN_PROFILES:
            raise ValueError(f"unknown recognition domain: {domain!r}")
        if act_thickness is not None and act_thickness not in ("none", "thin", "thick"):
            raise ValueError(f"invalid act_thickness: {act_thickness!r}")
        if repair is not None and repair not in ("none", "external", "internal"):
            raise ValueError(f"invalid repair: {repair!r}")

        manifest = dict(self._manifests[name])
        block = dict(manifest.get("recognition") or {})
        block["domain"] = key
        if authority is not None:
            block["authority"] = authority
        if act_thickness is not None:
            block["act_thickness"] = act_thickness
        if repair is not None:
            block["repair"] = repair
        manifest["recognition"] = block

        manifest_path = self._library_root / name / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        self._manifests[name] = manifest
        return self.recognition_profile(name)

    # --- Aperture chain declaration (APERTURE-SPEC.md section 6) ------------
    # Stored inside the same "recognition" block, because the chain and the
    # institution it belongs to are one declaration and must not drift apart.

    def chain_block(self, name: str) -> dict:
        """The raw stored chain block, or an empty dict. Never validates."""
        if name not in self._manifests:
            raise OntologyNotFoundError(name)
        block = (self._manifests[name].get("recognition") or {}).get("chain")
        return dict(block or {})

    def chain_status(self, name: str) -> dict:
        """The chain declaration if it is usable, or the reason it is not."""
        from . import recognition as rec
        from .aperture import chain as chain_mod

        declared = self.recognition_profile(name)
        profile = rec.profile_for(declared.get("domain"))
        if (declared.get("act_thickness") != profile.act_thickness
                or declared.get("repair") != profile.repair):
            from dataclasses import replace
            profile = replace(
                profile,
                act_thickness=declared.get("act_thickness"),
                repair=declared.get("repair"),
            )
        return chain_mod.status(
            self.chain_block(name),
            engagement=name,
            domain=profile.key,
            profile=profile,
        )

    def set_chain_declaration(self, name: str, links: dict, basis: str,
                              declared_by: str | None = None,
                              declared_on: str | None = None,
                              artifact_sha256: str | None = None) -> dict:
        """Declare where each chain link sits, and persist it.

        Validates before writing. A declaration that could not be resolved
        against is not worth storing, and storing it would let a later run pick
        up something no one could act on.
        """
        from . import recognition as rec
        from .aperture import chain as chain_mod

        if name not in self._managers:
            raise OntologyNotFoundError(name)

        declared = self.recognition_profile(name)
        profile = rec.profile_for(declared.get("domain"))

        block = {
            "links": links or {},
            "basis": basis,
            "declared_by": declared_by or "",
            "declared_on": declared_on or "",
            "artifact_sha256": artifact_sha256 or "",
        }
        # Raises ChainError / IncompleteChain / ThicknessContradiction.
        chain_mod.validate(
            block,
            engagement=name,
            domain=profile.key,
            declared_act_thickness=declared.get("act_thickness"),
            declared_repair=declared.get("repair"),
        )

        manifest = dict(self._manifests[name])
        recognition_block = dict(manifest.get("recognition") or {})
        recognition_block["chain"] = block
        manifest["recognition"] = recognition_block

        manifest_path = self._library_root / name / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        self._manifests[name] = manifest
        return self.chain_status(name)

    def manifest(self, name: str) -> dict:
        if name not in self._manifests:
            raise OntologyNotFoundError(name)
        return dict(self._manifests[name])

    def list_ontologies(self) -> list[dict]:
        """List all loaded ontologies with manifest + live stats."""
        result = []
        for name, mgr in sorted(self._managers.items()):
            manifest = dict(self._manifests[name])
            live_stats = {
                "classes": len(list(mgr.working.classes())),
                "individuals": len(list(mgr.working.individuals())),
            }
            result.append({
                "name": name,
                "active": name == self._active_name,
                "manifest": manifest,
                "stats": live_stats,
            })
        return result

    # ------------------------------------------------------------------
    # Phase 4: lifecycle mutations
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-scan the library from disk and rebuild the manager pool.

        Called after lifecycle mutations (create/activate/delete) so
        endpoints see current on-disk state.
        """
        self._managers = {}
        self._manifests = {}
        self._discover_and_load()

    def create(
        self,
        name: str,
        description: str,
        source_text: str | None = None,
        author: str | None = None,
        clone_seeds_from: str | None = None,
        recognition_domain: str | None = None,
    ) -> dict:
        """Create a new ontology directory with seeds and manifest.

        Does not activate. The newly created ontology is loaded into
        the registry before return.

        Raises:
          ValueError: if name is invalid or already exists.
          KeyError: if clone_seeds_from is given but refers to an
                    unknown ontology.
        """
        from datetime import datetime, timezone
        import json
        import re
        import shutil as _shutil

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError(
                f"Invalid ontology name {name!r}. Must match "
                f"[A-Za-z][A-Za-z0-9_-]{{0,63}}."
            )

        target = self._library_root / name
        if target.exists():
            raise ValueError(f"Ontology {name!r} already exists.")

        # Determine seed source. Default: active ontology's seeds.
        seed_source_name = clone_seeds_from or self._active_name
        if seed_source_name not in self._managers:
            raise KeyError(
                f"Seed source {seed_source_name!r} not in library."
            )
        seed_src = self._library_root / seed_source_name / "seed"

        # Lay out the new ontology directory.
        target.mkdir(parents=True)
        (target / "sessions").mkdir()
        (target / "jobs").mkdir()
        new_seed = target / "seed"
        if seed_src.exists():
            _shutil.copytree(seed_src, new_seed)
        else:
            new_seed.mkdir()

        manifest = {
            "name": name,
            "description": description,
            "source_text": source_text,
            "author": author,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "inactive",
            "seeded_from": seed_source_name,
            # FM-1: FIDELITY_DEFAULT applies to new ontologies only; existing
            # manifests without the field always resolve to "curated".
            "fidelity": (
                config.FIDELITY_DEFAULT
                if config.FIDELITY_DEFAULT in ("faithful", "curated")
                else "curated"
            ),
            "stats": {"note": "bootstrapped from seeds only"},
        }
        if recognition_domain:
            from . import recognition as rec
            key = str(recognition_domain).strip().lower()
            if key not in rec.DOMAIN_PROFILES:
                raise ValueError(
                    f"unknown recognition domain: {recognition_domain!r}")
            # Declared profiles only. An absent block means the scientific-
            # reference control, which is what an undeclared ontology is.
            manifest["recognition"] = {"domain": key}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # Instantiate a manager, which will bootstrap working.owl from
        # BFO + seeds since working.owl does not yet exist.
        self._load_one(target)

        return dict(manifest)

    def activate(self, name: str) -> tuple[str, str]:
        """Mark <name> as the active ontology.

        Writes ontology/active.txt and updates the in-memory state.
        Returns (new_active, previous_active). If already active, both
        values are equal (idempotent).

        Raises KeyError if name is not in the library.
        """
        if name not in self._managers:
            raise OntologyNotFoundError(name)
        previous = self._active_name
        if previous == name:
            return (name, previous)

        # Write active.txt atomically
        active_file = self._library_root.parent / "active.txt"
        tmp = active_file.with_suffix(".tmp")
        tmp.write_text(name + "\n")
        tmp.replace(active_file)

        self._active_name = name
        return (name, previous)

    def finalize(self, name: str) -> dict:
        """Set manifest status to 'finalized'. Idempotent.

        SPEC-bfo-agent-speed.md change 6: a curated ontology carrying
        commits that skipped the per-claim full verify must pass a fresh
        full-graph certificate before it freezes; on failure
        FinalizeVerificationError is raised (the route surfaces it as 409).
        Faithful ontologies finalize with the incoherence ledger as their
        annotation -- their divergence from coherence is recorded evidence,
        not a defect blocking finalization.

        Raises KeyError if name is unknown.
        """
        import json

        if name not in self._managers:
            raise OntologyNotFoundError(name)

        manifest = dict(self._manifests[name])

        if (
            config.FINALIZE_REQUIRES_FULL_VERIFY
            and manifest.get("status") != "finalized"
            and self.fidelity(name) != "faithful"
        ):
            mgr = self._managers[name]
            if getattr(mgr, "commits_since_full_verify", 0) > 0:
                report = mgr.verify_full()
                if not report["ok"]:
                    raise FinalizeVerificationError(name, report)

        manifest["status"] = "finalized"
        from datetime import datetime, timezone
        manifest["finalized_at"] = datetime.now(timezone.utc).isoformat()

        manifest_path = self._library_root / name / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        self._manifests[name] = manifest
        return dict(manifest)

    def delete(self, name: str) -> None:
        """Remove an ontology from the library.

        Refuses to delete the active ontology or any finalized ontology.
        Raises ValueError in those cases, KeyError if name is unknown.
        """
        import shutil as _shutil

        if name not in self._managers:
            raise OntologyNotFoundError(name)
        if name == self._active_name:
            raise ValueError(
                f"Cannot delete active ontology {name!r}. "
                f"Activate a different ontology first."
            )
        if self._manifests[name].get("status") == "finalized":
            raise ValueError(
                f"Cannot delete finalized ontology {name!r}. "
                f"Finalized artifacts are preserved for audit."
            )

        target = self._library_root / name
        if target.exists():
            _shutil.rmtree(target)
        self._managers.pop(name, None)
        self._manifests.pop(name, None)

    # ------------------------------------------------------------------
    # Phase 7: import existing OWL as a new ontology
    # ------------------------------------------------------------------

    def preview_owl(self, file_path: Path) -> dict:
        """Inspect an OWL file without committing it.

        Returns a structured report with class/individual counts, BFO
        alignment summary, and HermiT consistency under standard BFO
        axioms (using the active ontology's seeds).

        The file_path can point anywhere readable; nothing is moved.
        """
        from owlready2 import World
        import json as _json

        report = {
            "filename": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "loaded": False,
            "classes": 0,
            "individuals": 0,
            "object_properties": 0,
            "data_properties": 0,
            "annotation_properties": 0,
            "base_iri": None,
            "bfo_aligned_classes": 0,
            "consistent": None,
            "consistency_detail": None,
            "warnings": [],
        }

        try:
            w = World()
            # Load BFO first so cross-references can resolve
            w.get_ontology(str(self._bfo_path)).load()
            onto = w.get_ontology(file_path.as_uri()).load()
        except Exception as e:
            report["warnings"].append(f"Failed to load: {e}")
            return report

        report["loaded"] = True
        report["base_iri"] = str(onto.base_iri) if onto.base_iri else None

        classes = list(onto.classes())
        individuals = list(onto.individuals())
        report["classes"] = len(classes)
        report["individuals"] = len(individuals)
        report["object_properties"] = len(list(onto.object_properties()))
        report["data_properties"] = len(list(onto.data_properties()))
        report["annotation_properties"] = len(list(onto.annotation_properties()))

        # BFO alignment: count classes whose ancestor chain includes a
        # BFO_xxxxxxx class.
        bfo_count = 0
        for c in classes:
            ancestors = c.ancestors() if hasattr(c, "ancestors") else set()
            for a in ancestors:
                name = getattr(a, "name", "") or ""
                if name.startswith("BFO_"):
                    bfo_count += 1
                    break
        report["bfo_aligned_classes"] = bfo_count

        # Consistency check using owlready2/HermiT
        try:
            from owlready2 import sync_reasoner_hermit
            with onto:
                sync_reasoner_hermit(w, infer_property_values=False,
                                    debug=0)
            report["consistent"] = True
        except Exception as e:
            report["consistent"] = False
            report["consistency_detail"] = str(e)[:500]

        # Warnings
        if report["classes"] == 0:
            report["warnings"].append("Ontology has no classes")
        if bfo_count == 0 and report["classes"] > 0:
            report["warnings"].append(
                "No BFO-aligned classes detected. Imported ontology will "
                "load but lacks formal BFO grounding."
            )
        elif bfo_count < report["classes"] / 2:
            report["warnings"].append(
                f"Only {bfo_count}/{report['classes']} classes have BFO "
                f"ancestors. Coverage is partial."
            )
        if not report["consistent"]:
            report["warnings"].append("Ontology is inconsistent under HermiT")

        return report

    def import_from_file(
        self,
        file_path: Path,
        name: str,
        description: str,
        source_text: str | None = None,
        author: str | None = None,
        clone_seeds_from: str | None = None,
    ) -> dict:
        """Create a new library entry whose working.owl is the imported file.

        Validates name and uniqueness, copies the file in, optionally
        clones seeds from another library entry (default: active),
        writes manifest, and registers.

        Raises ValueError on bad name or duplicate, KeyError on bad
        clone source.
        """
        from datetime import datetime, timezone
        import json as _json
        import re
        import shutil as _shutil

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError(
                f"Invalid ontology name {name!r}. Must match "
                f"[A-Za-z][A-Za-z0-9_-]{{0,63}}."
            )

        target = self._library_root / name
        if target.exists():
            raise ValueError(f"Ontology {name!r} already exists.")

        if not file_path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        seed_source_name = clone_seeds_from or self._active_name
        if seed_source_name not in self._managers:
            raise KeyError(
                f"Seed source {seed_source_name!r} not in library."
            )
        seed_src = self._library_root / seed_source_name / "seed"

        # Lay out the new ontology directory.
        target.mkdir(parents=True)
        (target / "sessions").mkdir()
        (target / "jobs").mkdir()
        new_seed = target / "seed"
        if seed_src.exists():
            _shutil.copytree(seed_src, new_seed)
        else:
            new_seed.mkdir()

        # Copy the OWL into place as working.owl
        _shutil.copy(file_path, target / "working.owl")

        # Run the preview again on the now-canonical location to get
        # final stats for the manifest.
        preview = self.preview_owl(target / "working.owl")

        manifest = {
            "name": name,
            "description": description,
            "source_text": source_text,
            "author": author,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "inactive",
            "imported_from": file_path.name,
            "import_preview": {
                "classes": preview["classes"],
                "individuals": preview["individuals"],
                "bfo_aligned_classes": preview["bfo_aligned_classes"],
                "consistent": preview["consistent"],
                "warnings": preview["warnings"],
            },
            "stats": {
                "classes": preview["classes"],
                "individuals": preview["individuals"],
            },
        }
        (target / "manifest.json").write_text(_json.dumps(manifest, indent=2))

        # Register in the live registry. Use _load_one which now
        # tolerates an existing working.owl (post phase-4 fix).
        self._load_one(target)

        return dict(manifest)

