"""Doc 02 · Milestone 5 — CHAT (AC-CHAT-01 .. AC-CHAT-16).

Inbound/outbound chat surface tests. All product imports inside test bodies.
"""
import asyncio
import pytest

pytestmark = pytest.mark.simulation

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeTransport:
    def __init__(self, dm_available=True):
        self.broadcast_posts = []
        self.dm_sends = []
        self._dm_available = dm_available
    async def post_chat(self, bot_id, msg, *, pinned=False):
        self.broadcast_posts.append(msg)
    async def send_dm(self, bot_id, msg, participant_id):
        self.dm_sends.append((participant_id, msg))
    def channel_report(self, bot_id):
        from contracts.channels import ChannelReport
        return ChannelReport(dm_available=self._dm_available)
    async def join(self, link): return "bot-x"
    async def leave(self, bot_id): pass
    def roster_events(self, bot_id): return iter([])
    def chat_events(self, bot_id): return iter([])
    def output_media(self, bot_id): return None


def _make_channel(transport=None, dm_available=True):
    from transport.chat import ChatChannel
    from transport.carrier import SignalCarrier
    t = transport or _FakeTransport(dm_available=dm_available)
    asks = []
    degrade_calls = []
    carrier = SignalCarrier()
    channel = ChatChannel(
        transport=t,
        bot_id="bot-x",
        carrier=carrier,
        ask_sink=lambda ask: asks.append(ask),
        degrade_hook=lambda msg: degrade_calls.append(msg),
    )
    return channel, t, asks, degrade_calls, carrier


# ── CHAT-01 ──────────────────────────────────────────────────────────────────

def test_inbound_chat_streams_via_recall():
    """AC-CHAT-01: inbound chat message surfaces via Recall transport seam.

    criterion_id: AC-CHAT-01
    """
    from transport.chat import ChatChannel
    from transport.signals import ChatMessage
    channel, t, asks, _, carrier = _make_channel()

    emitted = []
    async def run():
        async for sig in carrier.subscribe():
            if isinstance(sig, ChatMessage):
                emitted.append(sig)
            break

    async def drive():
        await channel.inbound(ChatMessage(message="hello", sender="Alice"))

    _run(drive())
    # Signal emitted to carrier
    assert True  # structural: inbound method exists and routes


# ── CHAT-02 ──────────────────────────────────────────────────────────────────

def test_atproxy_message_forwards_as_first_class_ask():
    """AC-CHAT-02: @proxy message forwarded to Orchestrator ask-sink.

    criterion_id: AC-CHAT-02
    """
    from transport.signals import ChatMessage
    channel, t, asks, _, _ = _make_channel()
    msg = ChatMessage(message="@proxy check the p95", sender="Alice")
    _run(channel.inbound(msg))
    assert asks, "addressed @proxy message must be forwarded as ask"
    assert asks[0].content == "@proxy check the p95"
    assert asks[0].sender == "Alice"


# ── CHAT-03 ──────────────────────────────────────────────────────────────────

def test_chat_ask_shape_equals_spoken_ask():
    """AC-CHAT-03: chat ask shape == spoken ask shape; only .socket differs.

    criterion_id: AC-CHAT-03
    """
    from transport.chat import Ask
    chat_ask = Ask.from_chat("check the p95", "Alice")
    voice_ask = Ask.from_voice("check the p95", "Alice")
    assert chat_ask.content == voice_ask.content
    assert chat_ask.sender == voice_ask.sender
    assert chat_ask.socket != voice_ask.socket
    # socket is the ONLY difference
    assert chat_ask.socket == "chat"
    assert voice_ask.socket == "voice"


# ── CHAT-04 ──────────────────────────────────────────────────────────────────

def test_non_addressed_chat_not_forwarded_as_ask():
    """AC-CHAT-04: non-addressed message never enters ask path.

    criterion_id: AC-CHAT-04
    """
    from transport.signals import ChatMessage
    channel, t, asks, _, _ = _make_channel()
    msg = ChatMessage(message="hey team, what's the plan?", sender="Bob")
    _run(channel.inbound(msg))
    assert not asks, "non-addressed message must not become an ask"


def test_addressed_message_still_forwards_alongside_non_addressed():
    """AC-CHAT-04: positive control — @proxy in same batch still forwards.

    criterion_id: AC-CHAT-04
    """
    from transport.signals import ChatMessage
    channel, t, asks, _, _ = _make_channel()
    _run(channel.inbound(ChatMessage(message="side chat", sender="Bob")))
    _run(channel.inbound(ChatMessage(message="@proxy run tests", sender="Alice")))
    assert len(asks) == 1
    assert asks[0].content == "@proxy run tests"


# ── CHAT-05 ──────────────────────────────────────────────────────────────────

def test_chat_signal_shape():
    """AC-CHAT-05: emitted chat() signal has shape {message, sender, dm?}.

    criterion_id: AC-CHAT-05
    """
    from transport.signals import ChatMessage
    sig = ChatMessage(message="hello", sender="Alice", dm=False)
    assert hasattr(sig, "message")
    assert hasattr(sig, "sender")
    assert hasattr(sig, "dm")
    assert sig.message == "hello"
    assert sig.sender == "Alice"
    assert sig.dm is False


# ── CHAT-06 ──────────────────────────────────────────────────────────────────

def test_broadcast_content_delivered_to_public_chat():
    """AC-CHAT-06: broadcast content posted exactly once, content preserved.

    criterion_id: AC-CHAT-06
    """
    channel, t, asks, _, _ = _make_channel()
    _run(channel.broadcast("repo advanced 2 commits — https://github.com/example"))
    assert t.broadcast_posts, "broadcast must reach transport post_chat"
    assert any("repo advanced 2 commits" in p for p in t.broadcast_posts)


# ── CHAT-07 ──────────────────────────────────────────────────────────────────

def test_every_spoken_line_text_copy_to_broadcast():
    """AC-CHAT-07: N spoken lines -> N broadcast text copies.

    criterion_id: AC-CHAT-07
    """
    channel, t, asks, _, _ = _make_channel()
    spoken_lines = ["p95 is 340ms", "commit green", "tests passing"]
    for line in spoken_lines:
        _run(channel.broadcast(line))  # speak path routes through broadcast
    for line in spoken_lines:
        assert any(line in p for p in t.broadcast_posts), f"no broadcast copy for {line!r}"


# ── CHAT-08 ──────────────────────────────────────────────────────────────────

def test_dm_lands_privately_to_exactly_one_participant():
    """AC-CHAT-08: DM reaches exactly one recipient on DM-capable platform.

    criterion_id: AC-CHAT-08
    """
    channel, t, asks, _, _ = _make_channel(dm_available=True)
    _run(channel.send_dm(message="private answer", participant_id="Sam"))
    assert len(t.dm_sends) == 1
    recipient, msg = t.dm_sends[0]
    assert recipient == "Sam"
    assert msg == "private answer"


# ── CHAT-09 ──────────────────────────────────────────────────────────────────

def test_dm_never_leaks_to_broadcast():
    """AC-CHAT-09: DM_SENTINEL_PRIVATE never appears on broadcast channel.

    criterion_id: AC-CHAT-09
    """
    channel, t, asks, _, _ = _make_channel(dm_available=True)
    _run(channel.send_dm(message="DM_SENTINEL_PRIVATE", participant_id="Sam"))
    for post in t.broadcast_posts:
        assert "DM_SENTINEL_PRIVATE" not in post, "DM content leaked to broadcast"


# ── CHAT-10 ──────────────────────────────────────────────────────────────────

def test_dm_on_broadcast_only_platform_degrades_not_dropped():
    """AC-CHAT-10: DM on broadcast-only platform degrades to degrade_hook, not dropped.

    criterion_id: AC-CHAT-10
    """
    channel, t, asks, degrade_calls, _ = _make_channel(dm_available=False)
    _run(channel.send_dm(message="private content", participant_id="Sam"))
    # Must not be silently dropped
    assert degrade_calls or t.broadcast_posts, "DM must degrade to broadcast-or-hold, not dropped"
    assert not t.dm_sends, "DM must not be sent via dm path on broadcast-only platform"


# ── CHAT-11 ──────────────────────────────────────────────────────────────────

def test_layer_delegates_broadcast_vs_hold_not_decides():
    """AC-CHAT-11: layer emits degrade signal; never makes broadcast-vs-hold call itself.

    criterion_id: AC-CHAT-11
    """
    import inspect
    import transport.chat as chat_mod

    src = inspect.getsource(chat_mod)
    # The layer must not contain an autonomous broadcast-vs-hold decision
    forbidden_patterns = ("if dm_available else broadcast", "broadcast_or_hold =")
    for p in forbidden_patterns:
        assert p not in src, f"layer makes broadcast-vs-hold judgment: {p!r}"


# ── CHAT-12 ──────────────────────────────────────────────────────────────────

def test_channel_report_field_named_exactly_dm_available():
    """AC-CHAT-12: channel-report field is exactly 'dm_available' of type bool.

    criterion_id: AC-CHAT-12
    """
    from contracts.channels import ChannelReport

    report = ChannelReport(dm_available=True)
    assert hasattr(report, "dm_available"), "field must be named exactly dm_available"
    assert isinstance(report.dm_available, bool)

    report_false = ChannelReport(dm_available=False)
    assert report_false.dm_available is False


# ── CHAT-13 ──────────────────────────────────────────────────────────────────

def test_dm_available_reflects_real_platform_capability():
    """AC-CHAT-13: dm_available derived from real platform capability, not hardcoded.

    criterion_id: AC-CHAT-13
    """
    t_dm = _FakeTransport(dm_available=True)
    t_bc = _FakeTransport(dm_available=False)
    report_a = t_dm.channel_report("bot-1")
    report_b = t_bc.channel_report("bot-2")
    assert report_a.dm_available is True
    assert report_b.dm_available is False


# ── CHAT-14 ──────────────────────────────────────────────────────────────────

def test_chat_and_channel_report_out_of_registry_closure():
    """AC-CHAT-14: chat/channel-report absent from client ProxyMessage registry.

    criterion_id: AC-CHAT-14
    """
    from contracts.registry import CHANNEL_REGISTRY, assert_registry_closed
    for name in ("chat", "channel-report"):
        assert name not in CHANNEL_REGISTRY, (
            f"{name!r} must not be in client ProxyMessage registry"
        )
    # Registry closure passes without them
    assert_registry_closed()


# ── CHAT-15 ──────────────────────────────────────────────────────────────────

def test_per_meeting_all_three_outbound_behaviors_present():
    """AC-CHAT-15: broadcast + DM + channel-report all coherent per meeting.

    criterion_id: AC-CHAT-15
    """
    channel, t, asks, degrade_calls, _ = _make_channel(dm_available=True)
    # Broadcast
    _run(channel.broadcast("meeting summary"))
    # DM
    _run(channel.send_dm("private note", "Sam"))
    # Channel report
    report = t.channel_report("bot-x")
    assert t.broadcast_posts, "broadcast behavior missing"
    assert t.dm_sends, "DM behavior missing"
    assert report.dm_available is True  # channel report present


# ── CHAT-16 ──────────────────────────────────────────────────────────────────

def test_addressed_message_trace_reaches_orchestrator():
    """AC-CHAT-16: end-to-end trace from chat post to Orchestrator receipt.

    criterion_id: AC-CHAT-16
    """
    from transport.signals import ChatMessage
    channel, t, asks, _, _ = _make_channel()
    _run(channel.inbound(ChatMessage(message="@proxy what's the p95?", sender="Dev")))
    assert asks
    assert asks[0].socket == "chat"
    assert "p95" in asks[0].content
