"""CLI/terminal adapter for local development and testing."""

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.human.mcp import HumanRequest


class CLIAdapter(Adapter):
    """
    Interactive terminal REPL.

    Useful for testing skills and the engine without a chat platform.
    """

    def __init__(self):
        self._running = False

    def start(self, on_message: MessageHandler) -> None:
        self._running = True
        print("mithai> ready (type 'quit' to exit)\n")

        while self._running:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not text:
                continue
            if text.lower() in ("quit", "exit", "q"):
                break

            message = IncomingMessage(
                text=text,
                channel_id="cli",
                user_id="local",
                platform="cli",
            )

            response = on_message(message, self)
            print(f"\nmithai> {response}\n")

    def stop(self) -> None:
        self._running = False

    def send(self, message: OutgoingMessage) -> None:
        print(f"mithai> {message.text}")

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        print(f"\n{'=' * 50}")
        print(f"HUMAN APPROVAL REQUIRED [{request.level.upper()}]")
        print(f"{'=' * 50}")
        print(request.description)
        print(f"{'=' * 50}")

        if request.level == "confirm":
            confirm_text = _extract_confirm_token(request)
            print(f"\nType '{confirm_text}' to confirm, or anything else to deny:")
            try:
                answer = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nDenied.")
                return False
            approved = answer == confirm_text
        else:
            print("\nApprove? [y/N]: ", end="")
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nDenied.")
                return False
            approved = answer in ("y", "yes")

        if approved:
            print("Approved.\n")
        else:
            print("Denied.\n")
        return approved


def _extract_confirm_token(request: HumanRequest) -> str:
    """Extract a confirmation token from tool input for the confirm level."""
    for value in request.tool_input.values():
        if isinstance(value, str) and value:
            return value
    return request.tool_name.split("__")[-1]
