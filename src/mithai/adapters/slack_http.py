"""Slack adapter using HTTP Events API (webhook mode)."""

import logging
import threading

from mithai.adapters.base import BotReplyHandler, ChannelJoinHandler, ChannelObserveHandler, MessageHandler
from mithai.adapters.slack import SlackAdapterBase

logger = logging.getLogger(__name__)


class SlackHTTPAdapter(SlackAdapterBase):
    """
    Slack adapter using the Bolt SDK with HTTP Events API.

    Slack POSTs events to a registered HTTPS endpoint instead of using
    WebSocket (Socket Mode). Suitable for cloud deployments behind a
    load balancer.

    Two deployment shapes:

    * Standalone (``managed=False``): the adapter runs its own uvicorn server on
      ``host:port`` and Slack POSTs directly to ``https://your-host/slack/events``.
      Both the "Event Subscriptions Request URL" and "Interactivity Request URL"
      in the Slack app settings must point there.

    * Managed (``managed=True``): the adapter does NOT bind its own HTTP port and
      does NOT open a Socket Mode WebSocket. Instead the embedded API server
      (``mithai run`` with ``MITHAI_UI_PORT``) exposes ``POST /slack/events`` and
      delegates to this adapter's Bolt request handler. This is the model used by
      the external-orchestrator control plane for a single distributed app: one public URL
      verifies + routes events by team_id, then forwards them to the right agent.
      It avoids per-agent port collisions (every agent would otherwise bind :3000)
      and the shared app-level-token Socket Mode lottery.

    Requires slack-bolt and uvicorn: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, signing_secret: str,
                 host: str = "0.0.0.0", port: int = 3000,
                 allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, respond: str = "all",
                 managed: bool = False):
        # The Slack signature is the ONLY Slack-side authentication for HTTP/Events
        # mode — without a signing secret, Bolt's SlackRequestHandler performs no
        # HMAC verification and would accept arbitrary forged events. Fail fast.
        if not signing_secret or signing_secret.startswith("${"):
            raise RuntimeError(
                "SlackHTTPAdapter requires a signing_secret (set SLACK_SIGNING_SECRET). "
                "Without it, inbound Slack requests cannot be verified."
            )
        super().__init__(bot_token, allowed_channels, approval_timeout,
                         signing_secret=signing_secret, respond=respond)
        self._host = host
        self._port = port
        self._server = None
        self._managed = managed
        self._bolt_handler = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()

    def start(self, on_message: MessageHandler, on_channel_join: ChannelJoinHandler | None = None,
              on_observe: ChannelObserveHandler | None = None,
              on_bot_reply: BotReplyHandler | None = None) -> None:
        self._register_message_handlers(on_message, on_channel_join, on_observe, on_bot_reply)

        if self._managed:
            try:
                from slack_bolt.adapter.starlette import SlackRequestHandler
            except ImportError:
                raise ImportError(
                    "Managed SlackHTTPAdapter requires slack-bolt[starlette]. "
                    "Install with: pip install mithai[slack]"
                )
            self._stop_event.clear()  # reset in case stop() was called before start()
            self._bolt_handler = SlackRequestHandler(self._app)
            logger.info(
                "Slack HTTP adapter in managed mode — events arrive via the embedded "
                "API server's POST /slack/events (no own port, no Socket Mode)"
            )
            self._ready.set()
            self._stop_event.wait()  # block this thread until stop() (no own server)
            return

        try:
            import uvicorn
            from slack_bolt.adapter.starlette import SlackRequestHandler
            from starlette.applications import Starlette
            from starlette.requests import Request
            from starlette.routing import Route
        except ImportError:
            raise ImportError(
                "SlackHTTPAdapter requires uvicorn, starlette, and slack-bolt[starlette]. "
                "Install with: pip install mithai[slack]"
            )

        bolt_handler = SlackRequestHandler(self._app)

        async def slack_events(request: Request):
            return await bolt_handler.handle(request)

        starlette_app = Starlette(routes=[
            Route("/slack/events", slack_events, methods=["POST"]),
        ])

        config = uvicorn.Config(starlette_app, host=self._host, port=self._port,
                                log_level="warning")
        self._server = uvicorn.Server(config)
        logger.info("Starting Slack HTTP adapter on %s:%d", self._host, self._port)
        self._server.run()  # blocks (same as SocketModeHandler.start())

    async def handle_event(self, request):
        """Handle a Slack request forwarded by the embedded API server (managed mode).

        Delegates to the Bolt SlackRequestHandler so signature verification, dedup,
        channel filtering, dispatch, and the reply all run through the standard
        pipeline using this workspace's bot token.

        Receipt-ack contract: Bolt fast-acks and runs the turn in a background
        thread, so a 2xx means RECEIVED (not processed). The control plane's
        durable queue is the sole delivery-durability owner and retries on any
        non-2xx — see the slack_events route docstring in mithai.ui.app for the
        full contract and the deploy ordering it implies.

        If the handler isn't registered yet (a brief window during startup, before
        start() runs), return 503 so the control plane / Slack retries rather than
        surfacing a 500.
        """
        if self._bolt_handler is None:
            from starlette.responses import JSONResponse
            return JSONResponse({"error": "adapter not ready"}, status_code=503)
        return await self._bolt_handler.handle(request)

    def stop(self) -> None:
        self._stop_event.set()  # unblock managed start()
        if self._server:
            self._server.should_exit = True
