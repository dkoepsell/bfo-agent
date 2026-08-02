"""Which render profile a request gets.

The spec's capability tokens were designed for an offline single-host CLI. This
is a Flask service, so the split is role-based instead: ``internal`` for the
analyst running the tool, ``client`` for a client-facing surface.

**Read this before relying on it.** There is no account auth wired into this
application today: ``create_app`` registers no login and CORS is open. So the
profile is not currently *enforced* against an identity. What it does guarantee
is the direction of travel: the configured default may be narrowed to ``client``
by a request, never widened to ``internal`` by one. A caller can always ask for
less disclosure and never for more.

When account auth lands, replace the body of :func:`resolve_profile` with the
caller's role. Everything else in the package already takes its profile from
here, and the projection boundary in :mod:`app.aperture.project` is what actually
prevents disclosure either way.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import config
from .project import PROFILES

INTERNAL = "internal"
CLIENT = "client"


class ProfileRefused(Exception):
    """The requested profile is not one this tool knows.

    Never degrades to a lesser profile on failure, and never to a greater one.
    It refuses.
    """


def default_profile() -> str:
    configured = str(getattr(config, "APERTURE_DEFAULT_PROFILE", INTERNAL) or INTERNAL)
    configured = configured.strip().lower()
    return configured if configured in PROFILES else INTERNAL


def resolve_profile(requested: Optional[str], *, caller_role: Optional[str] = None) -> str:
    """Resolve the effective profile for one request.

    ``requested`` may narrow the default to ``client``. It may never widen a
    ``client`` default to ``internal``.
    """
    effective = default_profile()

    if caller_role:
        # Forward-compatible: a client role pins the profile regardless of the
        # configured default or what the request asked for.
        if str(caller_role).strip().lower() == CLIENT:
            effective = CLIENT

    if requested is None or requested == "":
        return effective

    requested = str(requested).strip().lower()
    if requested not in PROFILES:
        raise ProfileRefused(
            f"unknown profile {requested!r}; expected one of {list(PROFILES)}")

    if requested == CLIENT:
        return CLIENT
    if effective == CLIENT:
        raise ProfileRefused(
            "the client profile cannot be widened to internal by a request")
    return effective


def profile_from_request(request: Any) -> str:
    """Convenience wrapper for a Flask request."""
    return resolve_profile(request.args.get("profile"))
