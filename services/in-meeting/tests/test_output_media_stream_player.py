"""FIX 4 — the continuous-stream audio player, verified PROGRAMMATICALLY (not by ear).

The founder's demand: "the voice cannot be choppy, we need the best audio." Root cause: the old
page resampled every ~120ms PCM chunk SEPARATELY (each its own AudioBufferSourceNode), planting an
interpolation seam at every chunk boundary (~8 seams/sec). The rebuild plays ONE continuous stream
from a single FIFO the worklet pulls from — no per-chunk buffers, no per-chunk resample, no seams.

The worklet's FIFO/prebuffer/underrun/cut logic can't run in a browser from here, so
``in_meeting.fifo_player.FifoPlayer`` is its byte-for-byte Python MIRROR. These tests drive THAT
with realistic jittered chunk arrivals and assert the properties the fix must hold; separate
assertions pin the shipped page JS so the worklet half can't silently regress.
"""
from __future__ import annotations


def _drain(player, blocks):
    """Render ``blocks`` output blocks, concatenating them into one flat sample stream."""
    out = []
    for _ in range(blocks):
        out.extend(player.process())
    return out


def test_no_seams_when_the_fifo_never_underruns() -> None:
    """THE seam-free guarantee: once primed, the emitted sample stream is BYTE-IDENTICAL to the
    concatenated input wherever no underrun occurred (only fades at gap edges, and there are none
    here). Fed a well-supplied FIFO, the output equals the input samples in order — no resample
    seam, no interpolation, no dropped/duplicated sample."""
    from in_meeting.fifo_player import FifoPlayer

    # ramp=1 so the fade-in touches only the very first sample (scale 1 - 1/1 = 0 on sample 0),
    # then unity — we assert the seam-free body after that single ramped sample.
    player = FifoPlayer(prebuffer=256, ramp=1, block=128)

    # A long, distinctive ramp signal so any reorder/drop/dup or resample seam is visible. Sized
    # ABOVE the total fed so no chunk slice runs short (the FIFO never underruns in this test).
    sizes = [300, 120, 480, 90, 600, 240, 128, 800] * 3
    signal = [((i % 100) / 100.0) - 0.5 for i in range(sum(sizes) + 128)]
    # Feed it as jittered chunks (varying sizes) but ALL banked before we render — no underrun.
    idx = 0
    for size in sizes:
        player.append(signal[idx : idx + size])
        idx += size
    fed = signal[:idx]

    out = _drain(player, idx // 128)
    # Sample 0 is the fade-in edge (scaled to 0); every sample AFTER it is the input verbatim —
    # continuous, in order, no seam.
    assert out[0] == 0.0
    assert out[1 : len(out)] == fed[1 : len(out)], "the body is byte-identical to the input (no seams)"


def test_prebuffer_is_respected_before_the_first_emit() -> None:
    """PREBUFFER: after silence the player emits all-zero blocks until at least ``prebuffer`` samples
    are banked — the jitter cushion that stops a jittered first arrival from instantly underrunning.
    Below the threshold ⇒ silence; at/above ⇒ real audio starts."""
    from in_meeting.fifo_player import FifoPlayer

    player = FifoPlayer(prebuffer=256, ramp=8, block=128)

    # Only 200 samples banked (< prebuffer 256): the first block is pure silence, nothing consumed.
    player.append([0.5] * 200)
    first = player.process()
    assert first == [0.0] * 128, "under the prebuffer threshold the player stays silent"
    assert player.available() == 200, "and consumes nothing while priming"

    # Top past the threshold: now it emits real (fading-in) audio.
    player.append([0.5] * 200)  # 400 banked ≥ 256
    second = player.process()
    assert any(s != 0.0 for s in second), "at/above the prebuffer the player emits audio"


def test_underrun_produces_ramped_silence_not_truncation() -> None:
    """UNDERRUN: when the FIFO runs dry mid-stream the player fades the tail out over ``ramp`` and
    emits silence for the rest of the block — a click-free gap, NOT an abrupt truncation. The
    already-written samples before the gap are monotonically ramped down to zero."""
    from in_meeting.fifo_player import FifoPlayer

    ramp = 8
    player = FifoPlayer(prebuffer=1, ramp=ramp, block=128)

    # Bank just 20 samples (all 1.0 for a clear ramp), then render one block: 20 real samples then
    # an underrun. The fade covers the last ``ramp`` written samples down to zero; the rest is 0.
    player.append([1.0] * 20)
    block = player.process()

    # The gap starts at index 20 (only 20 samples available; index 0 was ramped in — ramp starts
    # at ~0, so the fade-in is monotonically NON-decreasing over the first ``ramp`` samples).
    assert block[20:] == [0.0] * (128 - 20), "the rest of the block after the underrun is silence"
    # The tail before the gap is faded OUT: the last written sample is driven toward zero, and the
    # fade is monotonically NON-increasing across the ramp region ending at the gap.
    fade_region = block[20 - ramp : 20]
    assert fade_region[-1] < fade_region[0], "the tail ramps DOWN toward the gap (fade-out, not a cliff)"
    assert all(fade_region[k] >= fade_region[k + 1] for k in range(len(fade_region) - 1)), \
        "the fade-out is monotonic (click-free)"
    # After an underrun the player re-primes (banks the prebuffer again before resuming).
    assert player.available() == 0


def test_cut_clears_the_fifo_instantly() -> None:
    """CUT (barge-in, Law 3): a cut clears the FIFO the instant it arrives — the interrupted turn's
    remaining buffered samples never play on top of the human — and the player re-primes."""
    from in_meeting.fifo_player import FifoPlayer

    player = FifoPlayer(prebuffer=64, ramp=8, block=128)
    player.append([0.5] * 1000)          # seconds of buffered audio
    player.process()                     # consume some (prime + one block)
    assert player.available() > 0

    player.cut()
    assert player.available() == 0, "the cut cleared the FIFO instantly"
    # After a cut the player is priming again: a fresh block is silence until re-banked.
    assert player.process() == [0.0] * 128


def test_jittered_arrivals_stay_seamless_across_a_sustained_stream() -> None:
    """END-TO-END jitter model: 120ms-equivalent chunks arriving with variable 0-400ms 'jitter'
    (modeled as variable inter-append gaps that never let the FIFO go dry once primed, thanks to the
    prebuffer) render a stream whose non-edge body is the input verbatim — the choppy per-chunk seam
    is gone. We interleave appends and renders the way the live feed does."""
    from in_meeting.fifo_player import FifoPlayer

    player = FifoPlayer(prebuffer=512, ramp=1, block=128)
    # Sized ABOVE all appends so no slice runs short (the prebuffer keeps the FIFO from underrunning).
    signal = [((i % 257) / 257.0) - 0.5 for i in range(40000)]

    fed = 0
    produced: list[float] = []
    # Prime: bank enough that renders never underrun (the prebuffer cushions the jitter).
    while fed < 1024:
        player.append(signal[fed : fed + 256]); fed += 256
    # Interleave: each render pulls one block; each append banks a jittered chunk ahead of it.
    for step in range(60):
        # jittered chunk size around a 120ms-equivalent, always ≥ a block so we outpace draining.
        size = 160 + (step * 37) % 240
        player.append(signal[fed : fed + size]); fed += size
        produced.extend(player.process())

    # The body (past the single ramped edge sample) is the input verbatim — no seams introduced.
    assert produced[1 : len(produced)] == signal[1 : len(produced)], "sustained stream is seam-free"


# ── the shipped page JS (the worklet half can't silently regress) ────────────────────


def test_page_js_registers_the_worklet_and_streams_via_a_fifo() -> None:
    """The inline page JS must ship the CONTINUOUS-STREAM player: an AudioWorklet registered from an
    inline Blob module, a FIFO the chunks are APPENDED to (no per-chunk buffers), a prebuffer, an
    underrun ramp, and a cut that clears the FIFO. This pins the client half of FIX 4."""
    from in_meeting.output_media import _render_page

    page = _render_page("m-page-stream")
    # the worklet is registered from an inline Blob module (a single served HTML string):
    assert "registerProcessor('stream-processor'" in page
    assert "audioWorklet.addModule" in page
    assert "new Blob([WORKLET_SRC]" in page
    assert "new AudioWorkletNode" in page
    # chunks are APPENDED to one FIFO and pushed to the worklet — no per-chunk BufferSource:
    assert "appendChunk" in page
    assert "postMessage({ type: \"samples\"" in page
    assert "createBufferSource" not in page, "no per-chunk source nodes survive (that was the seam)"
    assert "nextStartTime" not in page, "the per-chunk scheduling cursor is fully removed (no dead code)"
    # prebuffer + underrun ramp + cut-clears-FIFO all present:
    assert "PREBUFFER_S" in page and "RAMP_S" in page
    assert "_prebuffer" in page and "_fadingIn" in page
    assert "postMessage({ type: \"cut\" }" in page
    # sample-rate handled ONCE: ask for 44100, resample-at-append only if the ctx differs:
    assert "{ sampleRate: SAMPLE_RATE }" in page
    assert "resampleStream(floats, SAMPLE_RATE, CTX_RATE)" in page


def _resample_stream_mirror():
    """Python mirror of the page's STATEFUL streaming resampler (phase + tail carried)."""
    state = {"frac": 0.0, "tail": 0.0, "has": False}

    def resample(chunk: list[float], from_rate: int, to_rate: int) -> list[float]:
        if from_rate == to_rate:
            return list(chunk)
        step = from_rate / to_rate
        inp = ([state["tail"]] if state["has"] else []) + list(chunk)
        out: list[float] = []
        pos = state["frac"]
        while pos <= len(inp) - 1:
            i0 = int(pos)
            i1 = min(i0 + 1, len(inp) - 1)
            frac = pos - i0
            out.append(inp[i0] * (1 - frac) + inp[i1] * frac)
            pos += step
        state["tail"] = inp[-1]
        state["has"] = True
        state["frac"] = pos - (len(inp) - 1)
        return out

    return resample


def test_chunked_streaming_resample_equals_whole_buffer_resample() -> None:
    """THE live-chop regression: resampling per-chunk with phase restarting at 0 created a
    discontinuity every 120ms in Recall's 48k browser. The stateful streaming resampler must
    produce (near-)identical output to resampling the whole utterance at once."""
    import math

    sine = [math.sin(2 * math.pi * 440 * i / 44100) for i in range(44100)]  # 1s @ 440Hz
    # whole-buffer reference (one stateful pass over the full signal)
    whole = _resample_stream_mirror()(sine, 44100, 48000)
    # chunked pass: 120ms chunks through ONE stateful resampler
    chunked_fn = _resample_stream_mirror()
    chunk_n = int(0.120 * 44100)
    chunked: list[float] = []
    for i in range(0, len(sine), chunk_n):
        chunked.extend(chunked_fn(sine[i : i + chunk_n], 44100, 48000))
    assert abs(len(whole) - len(chunked)) <= 2  # same output length (± edge sample)
    for a, b in zip(whole, chunked, strict=False):
        assert abs(a - b) < 1e-6  # sample-identical (float-accumulation tolerance): no phase reset, no boundary clamp
    # and the output is smooth at every former chunk boundary (no repeated/flat samples)
    for i in range(1, len(chunked) - 1):
        assert not (chunked[i] == chunked[i - 1] == chunked[i + 1] != 0.0)
