"""Starlette app for the Control Room web UI."""

import hmac
import json
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from mithai.ui.data import ControlRoomData

logger = logging.getLogger(__name__)

_UI_DIR = Path(__file__).parent
_TEMPLATE_DIR = _UI_DIR / "templates"
_STATIC_DIR = _UI_DIR / "static"


def create_app(config: dict) -> Starlette:
    """Create the Control Room Starlette application."""
    from mithai.cli.run_cmd import _create_memory_backend, _create_state

    state = _create_state(config)
    memory = _create_memory_backend(config)
    ctrl = ControlRoomData(state=state, memory=memory, config=config)
    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    # Register a JSON filter for templates
    templates.env.filters["tojson"] = lambda v: json.dumps(v, indent=2, default=str)

    auth_token = config.get("ui", {}).get("auth_token", "")

    _COOKIE_NAME = "mithai_session"

    async def _check_auth(request: Request) -> Response | None:
        """Return a 401 response if auth is required and not provided.

        Token sources (checked in order):
          1. ?token= query param — if valid, sets a session cookie and redirects
             to the same path without the token in the URL.
          2. Session cookie (set by step 1).
          3. Authorization: Bearer header (for API clients).
        """
        if not auth_token or auth_token.startswith("${"):
            return None  # No auth configured or unresolved env var

        # 1. Query-param token → set cookie and redirect to clean URL
        query_token = request.query_params.get("token", "")
        if query_token and hmac.compare_digest(query_token, auth_token):
            # Build a clean URL without the token param
            params = {k: v for k, v in request.query_params.items() if k != "token"}
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            clean_url = request.url.path + (f"?{qs}" if qs else "")
            response = RedirectResponse(url=clean_url, status_code=302)
            response.set_cookie(
                _COOKIE_NAME, auth_token,
                httponly=True, samesite="strict", path="/",
            )
            return response

        # 2. Session cookie
        cookie_token = request.cookies.get(_COOKIE_NAME, "")
        if cookie_token and hmac.compare_digest(cookie_token, auth_token):
            return None

        # 3. Bearer header (API clients)
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

    routes = [
        Route("/", dashboard),
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
        Route("/api/memory/{path:path}", api_memory_file),
        Route("/api/skills", api_skills),
        Route("/api/config", api_config),
        Route("/api/stats", api_stats),
    ]

    # Only mount static files if directory exists
    if _STATIC_DIR.exists():
        routes.append(Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"))

    app = Starlette(routes=routes)
    return app
