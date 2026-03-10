"""Slack adapter using HTTP Events API (webhook mode)."""

import logging

from mithai.adapters.base import ChannelJoinHandler, ChannelObserveHandler, MessageHandler
from mithai.adapters.slack import SlackAdapterBase

logger = logging.getLogger(__name__)


class SlackHTTPAdapter(SlackAdapterBase):
    """
    Slack adapter using the Bolt SDK with HTTP Events API.

    Slack POSTs events to a registered HTTPS endpoint instead of using
    WebSocket (Socket Mode). Suitable for cloud deployments behind a
    load balancer.

    Both "Event Subscriptions Request URL" and "Interactivity Request URL"
    in the Slack app settings must point to https://your-host/slack/events.

    Requires slack-bolt and uvicorn: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, signing_secret: str,
                 host: str = "0.0.0.0", port: int = 3000,
                 allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, respond: str = "all"):
        super().__init__(bot_token, allowed_channels, approval_timeout,
                         signing_secret=signing_secret, respond=respond)
        self._host = host
        self._port = port
        self._server = None

    def start(self, on_message: MessageHandler, on_channel_join: ChannelJoinHandler | None = None,
              on_observe: ChannelObserveHandler | None = None) -> None:
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

        self._register_message_handlers(on_message, on_channel_join, on_observe)

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

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
