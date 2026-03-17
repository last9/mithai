"""Tests for multi-modal image support in Engine.handle()."""

from unittest.mock import MagicMock

from mithai.adapters.base import ImageAttachment, IncomingMessage
from mithai.core.session import SessionManager
from mithai.state.memory import MemoryStateBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    from mithai.core.engine import Engine
    from mithai.memory.filesystem import FilesystemMemoryBackend
    import tempfile
    from pathlib import Path

    llm = MagicMock()
    resp = MagicMock()
    resp.content = [{"type": "text", "text": "got it"}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    state = MemoryStateBackend()
    memory = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))

    config = {
        "bot": {},
        "learning": {"enabled": False},
        "llm": {"provider": "anthropic", "api_key": "test"},
    }

    engine = Engine(config=config, llm=llm, state=state, memory=memory, skills={})
    return engine, llm


def _make_adapter():
    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = None
    return adapter


def _make_message(text="describe this", images=None, thread_id=None, message_id=None):
    return IncomingMessage(
        text=text,
        channel_id="C1",
        user_id="alice",
        platform="slack",
        thread_id=thread_id or "1.0",
        message_id=message_id or "1.0",
        images=images or [],
    )


def _last_user_content(llm_mock):
    """Extract the last user message's content from the first create_message call."""
    call_kwargs = llm_mock.create_message.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
    # Find the last user message
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    raise AssertionError("No user message found")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_text_with_image_sends_multimodal_content():
    """When images are attached, user content is a list of image + text blocks."""
    engine, llm = _make_engine()
    img = ImageAttachment(data="aW1hZ2VkYXRh", media_type="image/png")
    msg = _make_message(text="describe this", images=[img])

    engine.handle(msg, _make_adapter())

    content = _last_user_content(llm)
    assert isinstance(content, list)
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2VkYXRh"},
    }
    assert content[-1] == {"type": "text", "text": "describe this"}


def test_multiple_images_all_before_text():
    """Multiple images appear as separate blocks before the text block."""
    engine, llm = _make_engine()
    imgs = [
        ImageAttachment(data="cG5n", media_type="image/png"),
        ImageAttachment(data="anBn", media_type="image/jpeg"),
    ]
    msg = _make_message(text="compare these", images=imgs)

    engine.handle(msg, _make_adapter())

    content = _last_user_content(llm)
    assert isinstance(content, list)
    assert len(content) == 3
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert content[2] == {"type": "text", "text": "compare these"}


def test_no_images_sends_plain_string():
    """Without images, user content is a plain string."""
    engine, llm = _make_engine()
    msg = _make_message(text="hello", images=[])

    engine.handle(msg, _make_adapter())

    content = _last_user_content(llm)
    assert isinstance(content, str)
    assert content == "hello"


def test_image_only_empty_text():
    """Image-only message with empty text doesn't crash — text block has empty string."""
    engine, llm = _make_engine()
    img = ImageAttachment(data="aW1n", media_type="image/png")
    msg = _make_message(text="", images=[img])

    engine.handle(msg, _make_adapter())

    content = _last_user_content(llm)
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    assert content[-1] == {"type": "text", "text": ""}


def test_images_with_thread_backfill():
    """When thread backfill is present, text block includes the prefix; images still present."""
    engine, llm = _make_engine()
    adapter = _make_adapter()
    adapter.fetch_thread_context.return_value = ["bob: earlier message"]

    img = ImageAttachment(data="aW1n", media_type="image/png")
    # thread_id != message_id triggers backfill; no prior session turns
    msg = _make_message(text="what about this?", images=[img], thread_id="100.0", message_id="200.0")

    engine.handle(msg, adapter)

    content = _last_user_content(llm)
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    text_block = content[-1]["text"]
    assert "[Thread history" in text_block
    assert "bob: earlier message" in text_block
    assert "what about this?" in text_block


def test_images_with_pending_observations():
    """Pending observations are included in the text block alongside images."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    # Seed a session with a turn, then add a pending observation
    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    engine._sessions.append_turn(key, SessionManager.build_turn(
        user_id="alice", user_message="first", tool_calls=[], assistant_response="ok",
    ))
    engine._sessions.append_observation(key, {"user_id": "bob", "text": "me too"})

    img = ImageAttachment(data="aW1n", media_type="image/png")
    msg = _make_message(text="and this?", images=[img])

    engine.handle(msg, adapter)

    content = _last_user_content(llm)
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    text_block = content[-1]["text"]
    assert "bob: me too" in text_block


def test_observation_images_included_in_next_mention():
    """Images from observed messages are sent to the LLM on the next @mention."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    # Seed a session with a turn (bot is already engaged in this thread)
    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    engine._sessions.append_turn(key, SessionManager.build_turn(
        user_id="alice", user_message="first", tool_calls=[], assistant_response="ok",
    ))

    # Simulate an observed message with an image (user posts image without @mention)
    engine._sessions.append_observation(key, {
        "user_id": "bob",
        "text": "check this screenshot",
        "images": [{"data": "b2JzX2ltZw==", "media_type": "image/png"}],
    })

    # Now the user @mentions the bot (no image on this message)
    msg = _make_message(text="what do you think of that image?")
    engine.handle(msg, adapter)

    content = _last_user_content(llm)
    # Should be multi-modal because the observation had an image
    assert isinstance(content, list)
    # Observation image should be first
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == "b2JzX2ltZw=="
    # Text block should contain both observation context and the user's message
    text_block = content[-1]["text"]
    assert "bob: check this screenshot" in text_block
    assert "what do you think of that image?" in text_block


def test_observation_images_merged_with_current_images():
    """Both observation images and current message images appear in content."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    engine._sessions.append_turn(key, SessionManager.build_turn(
        user_id="alice", user_message="first", tool_calls=[], assistant_response="ok",
    ))
    engine._sessions.append_observation(key, {
        "user_id": "bob",
        "text": "old screenshot",
        "images": [{"data": "b2xk", "media_type": "image/png"}],
    })

    # Current message also has an image
    img = ImageAttachment(data="bmV3", media_type="image/jpeg")
    msg = _make_message(text="compare these", images=[img])
    engine.handle(msg, adapter)

    content = _last_user_content(llm)
    assert isinstance(content, list)
    # observation image first, then current image, then text
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 2
    assert image_blocks[0]["source"]["data"] == "b2xk"  # observation
    assert image_blocks[1]["source"]["data"] == "bmV3"  # current


def test_observation_without_images_no_multimodal():
    """Text-only observations don't trigger multi-modal content."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    engine._sessions.append_turn(key, SessionManager.build_turn(
        user_id="alice", user_message="first", tool_calls=[], assistant_response="ok",
    ))
    engine._sessions.append_observation(key, {
        "user_id": "bob",
        "text": "just a text reply",
    })

    msg = _make_message(text="what happened?")
    engine.handle(msg, adapter)

    content = _last_user_content(llm)
    assert isinstance(content, str)
    assert "bob: just a text reply" in content


def test_non_image_files_field_on_incoming_message():
    """IncomingMessage.non_image_files stores skipped file names."""
    msg = _make_message(text="check this")
    assert msg.non_image_files == []

    msg2 = IncomingMessage(
        text="check this",
        channel_id="C1",
        user_id="alice",
        platform="slack",
        non_image_files=["script.py", "data.csv"],
    )
    assert msg2.non_image_files == ["script.py", "data.csv"]


def test_session_stores_text_and_images():
    """Session turn stores message.text as string and images separately."""
    engine, llm = _make_engine()
    img = ImageAttachment(data="aW1n", media_type="image/png")
    msg = _make_message(text="describe this image", images=[img])

    engine.handle(msg, _make_adapter())

    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    session = engine._sessions.load(key)
    turns = session.get("turns", [])
    assert len(turns) == 1
    assert turns[0]["user_message"] == "describe this image"
    assert isinstance(turns[0]["user_message"], str)
    assert turns[0]["images"] == [{"data": "aW1n", "media_type": "image/png"}]


def test_session_no_images_key_when_none():
    """Session turn omits 'images' key when no images attached."""
    engine, llm = _make_engine()
    msg = _make_message(text="hello", images=[])

    engine.handle(msg, _make_adapter())

    scope = "1.0"
    key = SessionManager.session_key("slack", scope)
    session = engine._sessions.load(key)
    turns = session.get("turns", [])
    assert "images" not in turns[0]


def test_history_replays_images_for_recent_turns():
    """Images from recent turns are replayed as multi-modal content in history."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    # Turn 1: message with image
    img = ImageAttachment(data="aW1n", media_type="image/png")
    msg1 = _make_message(text="describe this", images=[img])
    engine.handle(msg1, adapter)

    # Turn 2: text-only follow-up — should see turn 1's image in history
    msg2 = _make_message(text="what else do you see?")
    engine.handle(msg2, adapter)

    # Check the second LLM call's messages
    second_call = llm.create_message.call_args_list[1]
    messages = second_call.kwargs.get("messages") or second_call[1].get("messages")

    # First user message in history should be multi-modal (image + text)
    hist_user_content = messages[0]["content"]
    assert isinstance(hist_user_content, list)
    assert hist_user_content[0]["type"] == "image"
    assert hist_user_content[0]["source"]["data"] == "aW1n"
    assert hist_user_content[-1] == {"type": "text", "text": "describe this"}

    # Current user message (last one with role=user) should be plain text
    user_messages = [m for m in messages if m["role"] == "user"]
    current_user_content = user_messages[-1]["content"]
    assert isinstance(current_user_content, str)
    assert current_user_content == "what else do you see?"


def test_history_drops_images_beyond_budget():
    """Only the last _MAX_IMAGE_HISTORY_TURNS turns get images; older ones are text-only."""
    engine, llm = _make_engine()
    adapter = _make_adapter()

    # Send 3 turns with images
    for i in range(3):
        img = ImageAttachment(data=f"img{i}", media_type="image/png")
        engine.handle(_make_message(text=f"turn {i}", images=[img]), adapter)

    # Send a 4th turn without images to trigger history rebuild
    engine.handle(_make_message(text="final"), adapter)

    fourth_call = llm.create_message.call_args_list[3]
    messages = fourth_call.kwargs.get("messages") or fourth_call[1].get("messages")

    # Collect user messages from history (every other message starting at 0)
    user_messages = [m for m in messages if m["role"] == "user"]

    # turn 0 (oldest) — should be text-only (beyond budget of 2)
    assert isinstance(user_messages[0]["content"], str)
    assert user_messages[0]["content"] == "turn 0"

    # turn 1 — should have images (within budget)
    assert isinstance(user_messages[1]["content"], list)
    assert user_messages[1]["content"][0]["type"] == "image"

    # turn 2 — should have images (within budget)
    assert isinstance(user_messages[2]["content"], list)
    assert user_messages[2]["content"][0]["type"] == "image"

    # turn 3 (current) — no images
    assert isinstance(user_messages[3]["content"], str)
    assert user_messages[3]["content"] == "final"
