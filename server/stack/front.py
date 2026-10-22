"""The front door, assembled: one sub-application mounted at `/stack/api`.

A sub-application, not a router on the main app, so that its error handlers
(the Collab envelope: no 403, no 404, 422 turned into `invalid_request`) apply
to everything under that prefix, including a path it does not have, and to
nothing else the server answers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from . import envelope
from .auth import Auth
from .projects import Projects
from .rigs import Rigs


def build(store: Any) -> FastAPI:
    sub = FastAPI(title="Starfront stacking front door", docs_url=None, redoc_url=None,
                  openapi_url=None)
    envelope.install(sub)
    router = APIRouter()
    auth = Auth(store)
    sub.state.auth = auth
    auth.mount(router)
    sub.state.projects = Projects(store, auth, router)
    sub.state.rigs = Rigs(store, auth, router)
    sub.include_router(router)
    return sub
