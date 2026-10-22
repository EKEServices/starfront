"""The front door, assembled: one sub-application mounted at `/stack/api`.

A sub-application, not a router on the main app, so that its error handlers
(the Collab envelope: no 403, no 404, 422 turned into `invalid_request`) apply
to everything under that prefix, including a path it does not have, and to
nothing else the server answers.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request

from . import envelope
from .auth import Auth
from .frames import Frames, Uploads
from .library import Library
from .projects import Projects
from .rigs import Rigs

#: How often the sweep looks for uploads that were abandoned.
SWEEP_EVERY = 900


def build(store: Any, relay: Path | None = None) -> FastAPI:
    sub = FastAPI(title="Starfront stacking front door", docs_url=None, redoc_url=None,
                  openapi_url=None)
    envelope.install(sub)
    router = APIRouter()
    auth = Auth(store)
    sub.state.auth = auth
    auth.mount(router)
    sub.state.projects = Projects(store, auth, router)
    sub.state.rigs = Rigs(store, auth, router)
    # Frame bytes wait here for the hub: STARFRONT_DATA/stack/relay (T406 reads them).
    uploads = Uploads(store, relay if relay is not None else Path(store.path).parent / "stack" / "relay")
    sub.state.uploads = uploads
    sub.state.frames = Frames(store, auth, sub.state.projects, sub.state.rigs, uploads, router)
    sub.state.library = Library(store, auth, sub.state.rigs, uploads, router)
    sub.include_router(router)
    return sub


def install(app: FastAPI, sub: FastAPI, *, sweep: bool = True) -> None:
    """What the front door needs outside `/stack/api`: the part PUT, and the sweep.

    The part URLs `init` hands out are `/stack/s3/{upload_id}/{n}`: S3's
    `UploadPart` on this server, so not under the envelope's prefix (its
    answers are S3's, not the contract's). Call this BEFORE the static mount
    at `/stack`, or the mount answers first.

    The sweep aborts uploads left `uploading` for three days, on a daemon
    thread (the server has no other background job to hang it on).
    """
    uploads: Uploads = sub.state.uploads

    @app.put("/stack/s3/{upload_id}/{n}")
    async def put_part(upload_id: str, n: int, request: Request):
        return await uploads.put_part(request, upload_id, n)

    def loop() -> None:
        while True:
            time.sleep(SWEEP_EVERY)
            try:
                sub.state.frames.sweep()
                sub.state.library.sweep()
            except Exception:       # a sweep must never end the thread
                pass

    if sweep:
        threading.Thread(target=loop, name="stack-sweep", daemon=True).start()
