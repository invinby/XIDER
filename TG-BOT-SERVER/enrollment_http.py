"""Loopback-only HTTP handlers for X-DOCK enrollment.

The deployment must terminate publicly trusted HTTPS in front of this app and
must overwrite X-Real-IP. Never expose its loopback listener directly.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

from enrollment_store import EnrollmentStore


log = logging.getLogger("xider.enrollment")


def create_app(
    store: EnrollmentStore,
    on_pending: Callable[[dict], Awaitable[None]],
    *,
    trust_loopback_proxy: bool = False,
) -> web.Application:
    app = web.Application(client_max_size=4096)

    def source_tag(request: web.Request) -> str:
        source = request.remote or ""
        if trust_loopback_proxy and source in {"127.0.0.1", "::1"}:
            source = request.headers.get("X-Real-IP", source)
        try:
            source = str(ipaddress.ip_address(source))
        except ValueError:
            raise web.HTTPBadRequest(text="invalid source address")
        return hashlib.sha256(source.encode("ascii")).hexdigest()

    async def submit(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            result = store.submit(payload, source_tag(request))
        except ValueError as exc:
            if "rate limit" in str(exc):
                raise web.HTTPTooManyRequests(text="pairing rate limit") from exc
            raise web.HTTPBadRequest(text="invalid pairing request") from exc
        except web.HTTPException:
            raise
        except Exception:
            log.exception("Enrollment request store unavailable")
            raise web.HTTPServiceUnavailable(text="pairing unavailable")
        try:
            await on_pending({**result, "platform": payload["platform"], "label": payload["label"], "device_id": payload["device_id"]})
        except Exception:
            # The pending request remains visible in the owner's inbox.
            log.exception("Owner pairing notification failed")
        return web.json_response({"state": "pending", **result}, status=202, headers={"Cache-Control": "no-store"})

    async def poll(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"request_id", "nonce", "signature"}:
                raise ValueError("invalid poll fields")
            result = store.poll(payload["request_id"], payload["nonce"], payload["signature"])
        except ValueError as exc:
            raise web.HTTPBadRequest(text="invalid poll request") from exc
        except web.HTTPException:
            raise
        except Exception:
            log.exception("Enrollment poll store unavailable")
            raise web.HTTPServiceUnavailable(text="pairing unavailable")
        return web.json_response(result, headers={"Cache-Control": "no-store"})

    app.router.add_post("/x-dock/v1/request", submit)
    app.router.add_post("/x-dock/v1/poll", poll)
    return app
