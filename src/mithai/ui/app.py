"""Starlette app for the Control Room web UI."""

import hmac
import json
import logging
from pathlib import Path
from urllib.parse import urlencode

import anyio
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from mithai.ui.data import ControlRoomData

logger = logging.getLogger(__name__)

_COOKIE_NAME = "mithai_session"
_UI_DIR = Path(__file__).parent
_TEMPLATE_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"


def create_app(config: dict, engine=None, adapter=None) -> Starlette:
    """Create the Control Room Starlette application.

    When engine and adapter are provided, a POST /api/trigger endpoint
    is registered that allows sending messages to the agent via HTTP.
    """
    from mithai.cli.run_cmd import _create_memory_backend, _create_state

    state = _create_state(config)
    memory = _create_memory_backend(config)
    ctrl = ControlRoomData(state=state, memory=memory, config=config)
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    # Register a JSON filter for templates
    templates.env.filters["tojson"] = lambda v: json.dumps(v, indent=2, default=str)

    auth_token = config.get("ui", {}).get("auth_token", "")

    async def _check_auth(request: Request) -> Response | None:
        """Return a 401 response if auth is required and not provided.

        Token sources (checked in order):
          1. ?token= query param — sets a session cookie and redirects to a clean URL.
          2. Session cookie (set by step 1).
          3. Authorization: Bearer header (for API clients).
        """
        if not auth_token or auth_token.startswith("${"):
            return None  # No auth configured or unresolved env var

        query_token = request.query_params.get("token", "")
        if query_token and hmac.compare_digest(query_token, auth_token):
            remaining = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            qs = urlencode(remaining)
            clean_url = request.url.path + (f"?{qs}" if qs else "")
            response = RedirectResponse(url=clean_url, status_code=302)
            response.set_cookie(
                _COOKIE_NAME, auth_token,
                httponly=True, samesite="strict", path="/",
            )
            return response

        cookie_token = request.cookies.get(_COOKIE_NAME, "")
        if cookie_token and hmac.compare_digest(cookie_token, auth_token):
            return None

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[7:]
            if hmac.compare_digest(bearer_token, auth_token):
                return None

        return HTMLResponse("<h1>401 Unauthorized</h1>", status_code=401)

    # ── HTML routes ──

    async def dashboard(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        session_stats = ctrl.get_session_stats()
        approval_stats = ctrl.get_approval_stats()
        recent_sessions = ctrl.list_sessions(limit=10)
        skills = ctrl.list_skills()
        return templates.TemplateResponse(request, "dashboard.html", {
            "session_stats": session_stats,
            "approval_stats": approval_stats,
            "recent_sessions": recent_sessions,
            "skill_count": len(skills),
        })

    async def sessions_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        query = request.query_params.get("q", "")
        if query:
            sessions = ctrl.search_sessions(query)
        else:
            sessions = ctrl.list_sessions(limit=100)
        return templates.TemplateResponse(request, "sessions.html", {
            "sessions": sessions,
            "query": query,
        })

    async def session_detail(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        session_id = request.path_params["session_id"]
        session = ctrl.get_session(session_id)
        if session is None:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)
        return templates.TemplateResponse(request, "session_detail.html", {
            "session": session,
        })

    async def approvals_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        approvals = ctrl.get_approvals()
        stats = ctrl.get_approval_stats()
        return templates.TemplateResponse(request, "approvals.html", {
            "approvals": approvals,
            "stats": stats,
        })

    async def memory_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        query = request.query_params.get("q", "")
        files = ctrl.list_memory_files()
        search_results = ctrl.search_memory(query) if query else []
        return templates.TemplateResponse(request, "memory.html", {
            "files": files,
            "query": query,
            "search_results": search_results,
        })

    async def memory_file_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        file_path = request.path_params["path"]
        content = ctrl.read_memory_file(file_path)
        if content is None:
            return HTMLResponse("<h1>File not found</h1>", status_code=404)
        return templates.TemplateResponse(request, "memory_file.html", {
            "path": file_path,
            "content": content,
        })

    async def skills_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        skills = ctrl.list_skills()
        return templates.TemplateResponse(request, "skills.html", {
            "skills": skills,
        })

    async def config_page(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        redacted_config = ctrl.get_config()
        return templates.TemplateResponse(request, "config.html", {
            "config": redacted_config,
            "config_path": ctrl.get_config_path(),
        })

    # ── JSON API routes ──

    async def api_sessions(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        query = request.query_params.get("q", "")
        if query:
            return JSONResponse(ctrl.search_sessions(query))
        return JSONResponse(ctrl.list_sessions(limit=100))

    async def api_session_detail(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        session_id = request.path_params["session_id"]
        session = ctrl.get_session(session_id)
        if session is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(session)

    async def api_approvals(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        return JSONResponse({
            "approvals": ctrl.get_approvals(),
            "stats": ctrl.get_approval_stats(),
        })

    async def api_memory(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        return JSONResponse({"files": ctrl.list_memory_files()})

    async def api_memory_file(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        file_path = request.path_params["path"]

        if request.method == "PUT":
            # Create or edit. The dashboard sends the raw file content as the body
            # (Content-Type: text/plain).
            body = await request.body()
            try:
                ok = ctrl.write_memory_file(file_path, body.decode("utf-8", errors="replace"))
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            if not ok:
                return JSONResponse({"error": "invalid path"}, status_code=400)
            return JSONResponse({"path": file_path, "status": "written"})

        if request.method == "DELETE":
            try:
                removed = ctrl.delete_memory_file(file_path)
            except NotImplementedError:
                return JSONResponse({"error": "delete not supported by this backend"}, status_code=501)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            if not removed:
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse({"path": file_path, "status": "deleted"})

        content = ctrl.read_memory_file(file_path)
        if content is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"path": file_path, "content": content})

    async def api_skills(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        return JSONResponse(ctrl.list_skills())

    async def api_config(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        return JSONResponse(ctrl.get_config())

    async def api_stats(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        return JSONResponse({
            "sessions": ctrl.get_session_stats(),
            "approvals": ctrl.get_approval_stats(),
        })

    async def api_trigger(request: Request) -> Response:
        if err := await _check_auth(request):
            return err
        if engine is None or adapter is None:
            return JSONResponse({"error": "engine not available"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        message_text = body.get("message", "")
        if not message_text:
            return JSONResponse({"error": "message is required"}, status_code=400)

        from mithai.adapters.base import IncomingMessage
        channel_id = body.get("channel_id", "trigger")
        msg = IncomingMessage(
            text=message_text,
            channel_id=channel_id,
            user_id=body.get("user_id", "api"),
            platform="trigger",
        )

        # wait=true → run engine.handle inline and return the response text.
        # Default (wait absent or false) → fire-and-forget 202 for webhook callers
        # with short HTTP timeouts.
        wait_param = request.query_params.get("wait", "").lower()
        if wait_param in ("1", "true", "yes"):
            try:
                # cancellable=False (anyio default): keep working even if HTTP client disconnects.
                # LLM calls are expensive; orphaning them mid-flight wastes money.
                response_text = await anyio.to_thread.run_sync(engine.handle, msg, adapter)
            except Exception as e:
                logger.exception("api_trigger: engine.handle failed")
                return JSONResponse(
                    {"error": "engine failed", "detail": str(e), "channel_id": channel_id},
                    status_code=500,
                )
            # Defensive coerce: engine.handle is typed -> str but future refactors
            # could return non-serializable. Stringify before JSONResponse builds.
            response_text = "" if response_text is None else str(response_text)
            return JSONResponse(
                {"status": "ok", "channel_id": channel_id, "response": response_text},
                status_code=200,
            )

        return JSONResponse(
            {"status": "accepted", "channel_id": channel_id},
            status_code=202,
            background=BackgroundTask(engine.handle, msg, adapter),
        )

    async def slack_events(request: Request) -> Response:
        """Receive a Slack request forwarded by an external control plane.

        The control plane verifies the app signature and routes by team_id, then
        forwards the raw Slack request here. We delegate to the managed Slack
        adapter's Bolt handler, which re-verifies, dedups, channel-filters,
        dispatches to the engine, and posts the reply via the workspace bot token.

        DURABILITY CONTRACT (receipt-ack): a 2xx here means RECEIVED, not
        processed. The Bolt handler fast-acks and runs the turn in a background
        thread (so a separate approval-click POST is never starved — setting
        process_before_response=True would deadlock approvals). Consequently, if
        this agent process dies between the 2xx and handle() completing, THIS
        event is lost here. That is accepted by design: the control plane
        external control plane is the SOLE delivery-durability owner —
        it persists every request before acking Slack and retries on any non-2xx
        from this endpoint. Therefore a tenant MUST NOT be switched to managed
        mode until that durable queue is deployed. An agent-side durable inbox
        (persist-before-200 + replay-on-boot) is a possible future hardening if
        the engine ever needs to own durability independently.
        """
        if err := await _check_auth(request):
            return err
        if adapter is None or not hasattr(adapter, "handle_event"):
            return JSONResponse({"error": "no slack adapter"}, status_code=503)
        return await adapter.handle_event(request)

    routes = [
        Route("/", dashboard),
        Route("/slack/events", slack_events, methods=["POST"]),
        Route("/sessions", sessions_page),
        Route("/sessions/{session_id:path}", session_detail),
        Route("/approvals", approvals_page),
        Route("/memory", memory_page),
        Route("/memory/{path:path}", memory_file_page),
        Route("/skills", skills_page),
        Route("/config", config_page),
        # JSON API
        Route("/api/sessions", api_sessions),
        Route("/api/sessions/{session_id:path}", api_session_detail),
        Route("/api/approvals", api_approvals),
        Route("/api/memory", api_memory),
        Route("/api/memory/{path:path}", api_memory_file, methods=["GET", "PUT", "DELETE"]),
        Route("/api/skills", api_skills),
        Route("/api/config", api_config),
        Route("/api/stats", api_stats),
        Route("/api/trigger", api_trigger, methods=["POST"]),
    ]

    # Only mount static files if directory exists
    if _STATIC_DIR.exists():
        routes.append(Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"))

    app = Starlette(routes=routes)
    return app
