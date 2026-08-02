"""Routes for the full report bundle."""
from __future__ import annotations

import json
import logging

from flask import Response, jsonify, request

from ..aperture import audit as audit_log
from ..aperture import authz, project, render
from ..aperture.loader import load_manifest
from .bundle import build_report, report_markdown, write_bundle
from .repair import repair_for_digestibility, serialise

log = logging.getLogger(__name__)


def _registry():
    from ..orchestrator import _get_registry

    return _get_registry()


def _resolve(name: str):
    try:
        return _registry().get(name), None
    except Exception:
        return None, (jsonify({"error": f"no such ontology: {name}"}), 404)


def _flag(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in ("0", "false", "no")


def register(app) -> None:

    @app.get("/ontologies/<name>/report")
    def full_report_json(name):
        """The whole report as JSON, without the zip."""
        manager, err = _resolve(name)
        if err:
            return err
        try:
            report = build_report(name, _registry(), manager,
                                  run_reasoner=_flag(request.args.get("reasoner")))
        except Exception as e:
            log.exception("report failed")
            return jsonify({"error": f"report failed: {e}"}), 500
        return jsonify(report.to_dict())

    @app.get("/ontologies/<name>/report.md")
    def full_report_markdown(name):
        manager, err = _resolve(name)
        if err:
            return err
        try:
            report = build_report(name, _registry(), manager,
                                  run_reasoner=_flag(request.args.get("reasoner")))
        except Exception as e:
            log.exception("report failed")
            return jsonify({"error": f"report failed: {e}"}), 500
        return Response(report_markdown(report), mimetype="text/markdown")

    @app.get("/ontologies/<name>/repaired.owl")
    def repaired_owl(name):
        """The digestible variant on its own.

        Named so that nobody can mistake it for the delivered artifact.
        """
        manager, err = _resolve(name)
        if err:
            return err
        try:
            result = repair_for_digestibility(str(manager.working_path))
        except Exception as e:
            log.exception("repair failed")
            return jsonify({"error": f"repair failed: {e}"}), 500
        return Response(
            serialise(result),
            mimetype="application/rdf+xml",
            headers={"Content-Disposition":
                     f'attachment; filename="{name}-repaired.owl"'},
        )

    @app.get("/ontologies/<name>/report.zip")
    def full_report_bundle(name):
        manager, err = _resolve(name)
        if err:
            return err
        try:
            profile = authz.profile_from_request(request)
        except authz.ProfileRefused as e:
            return jsonify({"error": str(e)}), 403

        try:
            report = build_report(name, _registry(), manager,
                                  run_reasoner=_flag(request.args.get("reasoner")))
        except Exception as e:
            log.exception("report failed")
            return jsonify({"error": f"report failed: {e}"}), 500

        try:
            repaired = serialise(repair_for_digestibility(str(manager.working_path)))
        except Exception as e:
            log.warning("repair failed for %s: %s", name, e)
            repaired = (f"<!-- the repaired variant could not be produced: {e} -->\n")

        # The client coverage table travels only when a chain was declared, and
        # it is built through the same projection the Scope tab uses.
        client_markdown = ""
        scope = report.data("scope")
        if scope:
            try:
                from ..aperture.resolve import Resolution, ResolutionReport, Verdict

                rows = tuple(
                    Resolution(
                        failure_id=r["failure_id"], stratum=r["stratum"],
                        verdict=Verdict(r["verdict"]), reason=r["reason"],
                        reason_detail=r["reason_detail"], basis=r["basis"],
                        produced_by=r["produced_by"], loci=tuple(r["loci"]),
                        instrument=r["instrument"],
                        link_states=r["link_states"],
                        detectors=tuple(r["detectors"]), notes=tuple(r["notes"]),
                    ) for r in scope["rows"]
                )
                rebuilt = ResolutionReport(
                    resolution_schema=scope["resolution_schema"],
                    manifest_version=scope["manifest_version"],
                    manifest_digest=scope["manifest_digest"],
                    probe_version=scope["probe_version"],
                    artifact_sha256=scope["artifact_sha256"],
                    formal_theory_strict=scope["formal_theory_strict"],
                    profile_dl=scope["profile_dl"],
                    chain_digest=scope["chain_digest"],
                    chain_basis=scope["chain_basis"],
                    domain=scope["domain"],
                    act_thickness=scope["act_thickness"],
                    repair=scope["repair"],
                    rows=rows,
                    advisories=tuple(scope.get("advisories") or ()),
                )
                view = project.project_client(rebuilt, load_manifest(), name)
                client_markdown = render.client_markdown(view)
            except Exception as e:
                log.warning("client projection failed for %s: %s", name, e)

        blob = write_bundle(report, repaired, client_markdown)

        audit_log.record(
            profile=profile, engagement=name,
            artifact_sha256=report.artifact_sha256,
            chain_sha256=(scope or {}).get("chain_digest", ""),
            manifest_version=(scope or {}).get("manifest_version", ""),
            output=json.dumps(report.to_dict(), default=str),
            extra={"endpoint": "report.zip", "bytes": len(blob)},
        )

        return Response(
            blob,
            mimetype="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="{name}-report.zip"'},
        )
