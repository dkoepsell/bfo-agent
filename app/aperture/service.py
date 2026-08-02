"""HTTP surface for Aperture.

Registered from ``create_app`` alongside the recognition-layer routes. Nothing
here computes anything: it resolves the ontology, calls the phases in order, and
records every render in the disclosure log.

``preconditions`` and ``chain`` are readable without a profile because they
disclose nothing about the framework: they describe the artifact and the
reviewer's own declaration.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import jsonify, request

from .. import config
from .. import recognition as rec
from . import audit as audit_log
from . import authz, binding, chain as chain_mod, probe as probe_mod, project, render
from .loader import ManifestError, load_manifest

log = logging.getLogger(__name__)


def _registry():
    from ..orchestrator import _get_registry

    return _get_registry()


def _resolve_ontology(name: str):
    """Return ``(manager, error_response)``."""
    try:
        return _registry().get(name), None
    except Exception:
        return None, (jsonify({"error": f"no such ontology: {name}"}), 404)


def _probe_cache_path(name: str) -> Path:
    return Path(config.LIBRARY_ROOT) / name / "aperture-preconditions.json"


def _run_probe(name: str, manager):
    return probe_mod.run_cached(
        str(manager.working_path),
        _probe_cache_path(name),
        bfo_path=str(config.BFO_PATH),
    )


def _declared_profile(name: str) -> rec.DomainProfile:
    declared = _registry().recognition_profile(name)
    profile = rec.profile_for(declared.get("domain"))
    if (declared.get("act_thickness") != profile.act_thickness
            or declared.get("repair") != profile.repair):
        from dataclasses import replace
        profile = replace(profile,
                          act_thickness=declared.get("act_thickness"),
                          repair=declared.get("repair"))
    return profile


def _build(name: str, manager):
    """Phases 1 to 3. Returns ``(report, declaration, error_response)``."""
    try:
        manifest = load_manifest()
    except ManifestError as e:
        return None, None, (jsonify({"error": str(e), "stage": "manifest"}), 500)

    probe_report = _run_probe(name, manager)

    profile = _declared_profile(name)
    block = _registry().chain_block(name)
    try:
        declaration = chain_mod.validate(
            block,
            engagement=name,
            domain=profile.key,
            declared_act_thickness=profile.act_thickness,
            declared_repair=profile.repair,
            artifact_sha256=probe_report.artifact_sha256,
        )
    except chain_mod.ThicknessContradiction as e:
        return None, None, (jsonify({
            "error": str(e), "stage": "chain", "reason": "thickness_contradiction",
            "field": e.field, "derived": e.derived, "declared": e.declared,
        }), 409)
    except chain_mod.ChainError as e:
        return None, None, (jsonify({
            "error": str(e), "stage": "chain",
            "reason": ("incomplete" if isinstance(e, chain_mod.IncompleteChain)
                       else "invalid"),
        }), 409)

    closure = probe_mod.load_closure(str(manager.working_path), str(config.BFO_PATH))
    advisories = chain_mod.cross_check(
        declaration, binding.bind(closure), probe_report.artifact_sha256)

    from .resolve import resolve

    return resolve(manifest, probe_report, declaration, advisories), declaration, None


def register(app) -> None:
    """Attach the Aperture routes to a Flask app."""

    @app.get("/aperture/manifest")
    def aperture_manifest():
        """The kernel manifest. The client profile sees labels only."""
        try:
            profile = authz.profile_from_request(request)
        except authz.ProfileRefused as e:
            return jsonify({"error": str(e)}), 403
        try:
            manifest = load_manifest()
        except ManifestError as e:
            return jsonify({"error": str(e)}), 500

        if profile == authz.CLIENT:
            return jsonify({
                "profile": "client",
                "defect_classes": sorted(manifest.client_labels().values()),
            })
        return jsonify({
            "profile": "internal",
            "version": manifest.version,
            "digest": manifest.digest,
            "strata": [s.model_dump() for s in manifest.manifest.strata],
            "failures": [
                dict(f.model_dump(),
                     transitive_preconditions=list(manifest.preconditions_for(f.id)),
                     is_human=f.is_human)
                for f in manifest.failures
            ],
        })

    @app.get("/aperture/audit/verify")
    def aperture_audit_verify():
        return jsonify(audit_log.verify().to_dict())

    @app.get("/ontologies/<name>/aperture/preconditions")
    def aperture_preconditions(name):
        manager, err = _resolve_ontology(name)
        if err:
            return err
        try:
            report = _run_probe(name, manager)
        except Exception as e:
            log.exception("aperture probe failed")
            return jsonify({"error": f"precondition probe failed: {e}"}), 500
        return jsonify(dict(report.to_dict(), order=list(probe_mod.PRECONDITIONS)))

    @app.get("/ontologies/<name>/aperture/chain")
    def aperture_get_chain(name):
        _manager, err = _resolve_ontology(name)
        if err:
            return err
        try:
            return jsonify(_registry().chain_status(name))
        except Exception as e:
            log.exception("aperture chain read failed")
            return jsonify({"error": f"chain read failed: {e}"}), 500

    @app.put("/ontologies/<name>/aperture/chain")
    def aperture_put_chain(name):
        manager, err = _resolve_ontology(name)
        if err:
            return err
        body = request.get_json(silent=True) or {}
        try:
            artifact_sha256 = probe_mod._sha256(Path(manager.working_path))
        except OSError:
            artifact_sha256 = ""
        try:
            return jsonify(_registry().set_chain_declaration(
                name,
                links=body.get("links") or {},
                basis=body.get("basis") or "",
                declared_by=body.get("declared_by"),
                declared_on=body.get("declared_on"),
                artifact_sha256=artifact_sha256,
            ))
        except chain_mod.ThicknessContradiction as e:
            return jsonify({
                "error": str(e), "reason": "thickness_contradiction",
                "field": e.field, "derived": e.derived, "declared": e.declared,
            }), 409
        except chain_mod.ChainError as e:
            return jsonify({
                "error": str(e),
                "reason": ("incomplete" if isinstance(e, chain_mod.IncompleteChain)
                           else "invalid"),
            }), 409
        except Exception as e:
            log.exception("aperture chain write failed")
            return jsonify({"error": f"chain write failed: {e}"}), 500

    @app.post("/ontologies/<name>/aperture/resolve")
    def aperture_resolve(name):
        manager, err = _resolve_ontology(name)
        if err:
            return err
        try:
            profile = authz.profile_from_request(request)
        except authz.ProfileRefused as e:
            return jsonify({"error": str(e)}), 403

        try:
            report, declaration, err = _build(name, manager)
        except Exception as e:
            log.exception("aperture resolve failed")
            return jsonify({"error": f"resolve failed: {e}"}), 500
        if err:
            return err

        manifest = load_manifest()
        if profile == authz.CLIENT:
            view = project.project_client(report, manifest, name,
                                          declaration.declared_on)
            payload = {"profile": "client", **view.to_dict()}
        else:
            payload = {"profile": "internal", **report.to_dict()}

        audit_log.record(
            profile=profile, engagement=name,
            artifact_sha256=report.artifact_sha256,
            chain_sha256=report.chain_digest,
            manifest_version=report.manifest_version,
            output=str(payload),
            extra={"endpoint": "resolve"},
        )
        return jsonify(payload)

    @app.get("/ontologies/<name>/aperture/scope")
    def aperture_scope(name):
        """The pre-engagement artifact. No detector is run."""
        manager, err = _resolve_ontology(name)
        if err:
            return err
        try:
            profile = authz.profile_from_request(request)
        except authz.ProfileRefused as e:
            return jsonify({"error": str(e)}), 403

        try:
            report, declaration, err = _build(name, manager)
        except Exception as e:
            log.exception("aperture scope failed")
            return jsonify({"error": f"scope failed: {e}"}), 500
        if err:
            return err

        manifest = load_manifest()
        view = None
        if profile == authz.CLIENT:
            view = project.project_client(report, manifest, name,
                                          declaration.declared_on)
        markdown = render.scope_statement(report, manifest, name, view=view)

        audit_log.record(
            profile=profile, engagement=name,
            artifact_sha256=report.artifact_sha256,
            chain_sha256=report.chain_digest,
            manifest_version=report.manifest_version,
            output=markdown,
            extra={"endpoint": "scope"},
        )
        payload = {"profile": profile, "markdown": markdown}
        if view is not None:
            payload["data"] = view.to_dict()
        else:
            payload["data"] = report.to_dict()
        return jsonify(payload)
