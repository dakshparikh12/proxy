"""Doc 05 · workroom.sandbox-receipts — HOST-side capture of tool receipts (§3.5 / §3.7②).

Node ``workroom.sandbox-receipts`` (evidence class ``[integration]``). This proves the
HOST half of §3.5's receipt contract on the real host code path
(``sandbox_transport.py``): every ``run_command`` produces a HOST-captured receipt with
the real ``argv`` + ``exit_code`` + a ``stdout_ref`` that references the REAL captured
stdout stream (not a model summary), and every ``write_file``/``edit_file``/``ast_grep``
produces ``artifact_hashes`` computed ON THE HOST over the landed bytes (read back through
the transport) — never the hash the tool *claimed*.

The load-bearing property (DoD): **no receipt field is derived from model text.** In
production the tool runs INSIDE the E2B sandbox (the baked Node sidecar) and its
``tools/result`` returns over HTTP — a compromised sidecar, or a model narrating a fake
result, could CLAIM ``exit_code: 0`` and a passing artifact hash. The host-side transport
capture is the wall: it reads the REAL captured stream fields the sidecar returns (the
kernel-level exit status + the actual stdout bytes) and RE-HASHES the landed file itself,
so a lying claim is structurally ignored. These tests plant a lying claim and prove the
receipt reflects the real result (§3.7② — the deterministic evidence gate reads THIS).

Spec refs: 05-WORKROOM.md §3.5 (``run_command`` emits a host-observed receipt
``{command_id, argv, exit_code, stdout_ref, artifact_hashes}`` captured by the transport on
the host, NOT parsed from model prose; write/edit/ast_grep emit ``artifact_hashes`` for the
files they touch), §3.7② (the deterministic evidence gate reads host-observed receipts —
``artifact_hashes`` as a list of ``{path, sha256}``, ``argv`` joined to the verify command,
``exit_code == 0`` gates a claimed pass), CANONICAL §12.4 (the evidence gate reads
host-observed structured receipts, NOT a regex over model prose), D-017 (the ToolReceipt
shape). Confirmed live E2B wire (CANONICAL §11.10): ``sandbox.files.read(path,
format="bytes") -> bytearray`` (how the host reads the landed file back to hash it) and
``sandbox.commands.run(cmd) -> CommandResult{exit_code, stdout, stderr}`` (the captured
stream the sidecar returns). e2b is NOT installed — the host path is proven against an
in-process fake sidecar; the live bake is the flagged deploy residual.

These run on the REAL host path: ``sandbox_transport.HostReceiptCapture`` /
``HostReceiptStore`` build a real receipt from a real (fake-backed) tools/result stream.
"""
from __future__ import annotations

import hashlib

import pytest

from workroom.sandbox_transport import (
    HostReceiptCapture,
    HostReceiptStore,
)
from tests.doc05.fakes import FakeSandboxFilesystem, FakeToolSidecar


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── the host-side captured-stream store (stdout_ref → the REAL bytes) ──────────


def test_stdout_ref_references_the_real_captured_stream_not_a_summary() -> None:
    """``stdout_ref`` is a HANDLE into a host store of the REAL captured stdout bytes —
    fetching it returns the full stream verbatim, never a truncated model summary (§3.5,
    the named risk)."""
    store = HostReceiptStore()
    real_stdout = ("line\n" * 5000).encode("utf-8")  # a big real stream
    ref = store.put_stream(real_stdout)
    assert isinstance(ref, str) and ref
    # The ref round-trips to the EXACT captured bytes — the full stream, verbatim.
    assert store.get_stream(ref) == real_stdout
    # Content-addressed: the ref embeds the digest of the real bytes (tamper-evident).
    assert store.get_stream(ref) is not None
    assert len(store.get_stream(ref)) == len(real_stdout), "the full stream is stored, not a summary"


def test_stdout_ref_is_content_addressed_to_the_captured_bytes() -> None:
    """The same captured stream yields the same ref (content-addressed); different streams
    yield different refs — the ref is bound to the ACTUAL bytes, not an opaque counter."""
    store = HostReceiptStore()
    a = store.put_stream(b"exit 0 real output")
    b = store.put_stream(b"exit 0 real output")
    c = store.put_stream(b"totally different output")
    assert a == b, "identical captured streams map to the same content-addressed ref"
    assert a != c, "different captured streams map to different refs"


# ── run_command: host captures real argv + exit_code + stdout_ref ─────────────


@pytest.mark.asyncio
async def test_run_command_receipt_is_host_captured_from_the_real_stream() -> None:
    """``run_command`` produces a host-captured receipt whose ``argv`` + ``exit_code`` +
    ``stdout_ref`` come from the REAL captured stream the sidecar returned — not the model's
    text (§3.5). ``stdout_ref`` resolves to the real stdout bytes in the host store."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture()

    # The sidecar runs `pytest -q` inside the sandbox and returns the REAL captured stream.
    result = await sidecar.run_command(argv=["pytest", "-q"], exit_code=0, stdout=b"5 passed\n")
    receipt = await capture.capture(tool="run_command", args={"command": "pytest -q"}, result=result)

    assert set(["command_id", "argv", "exit_code", "stdout_ref", "artifact_hashes"]).issubset(receipt)
    assert receipt["argv"] == ["pytest", "-q"], "argv is the REAL argv the sidecar ran"
    assert receipt["exit_code"] == 0, "exit_code is the REAL kernel exit status"
    # stdout_ref references the real captured bytes in the host store.
    assert capture.store.get_stream(receipt["stdout_ref"]) == b"5 passed\n"
    # command_id is host-minted (a fresh id per capture), never taken from the tool payload.
    assert receipt["command_id"]


@pytest.mark.asyncio
async def test_run_command_receipt_records_a_real_nonzero_exit_code() -> None:
    """A failing command's receipt records the REAL non-zero exit_code (§3.5) — the gate
    (§3.7②) can only pass a claim whose receipt shows ``exit_code == 0``, so a real failure
    must surface as a real non-zero code, never a claimed 0."""
    sidecar = FakeToolSidecar(fs=FakeSandboxFilesystem())
    capture = HostReceiptCapture()
    result = await sidecar.run_command(argv=["pytest", "-q"], exit_code=1, stdout=b"1 failed\n")
    receipt = await capture.capture(tool="run_command", args={"command": "pytest -q"}, result=result)
    assert receipt["exit_code"] == 1, "the receipt records the REAL non-zero exit code"


@pytest.mark.asyncio
async def test_lying_model_claim_of_exit_zero_is_ignored_the_receipt_is_real() -> None:
    """THE DoD proof (run_command): a sidecar/model that CLAIMS ``exit_code: 0`` in its
    narration while the REAL captured stream failed (exit 1) does NOT get a passing receipt.
    The host capture reads the real captured exit status, never the model's claim (§3.5 /
    §3.7② / CANONICAL §12.4 — the model cannot narrate exit 0 into a passing check)."""
    sidecar = FakeToolSidecar(fs=FakeSandboxFilesystem())
    capture = HostReceiptCapture()

    # Plant the lie: the payload's model-facing narration SAYS exit 0 / "all tests passed",
    # but the REAL captured stream (the kernel exit status + real stdout) is a failure.
    result = await sidecar.run_command(
        argv=["pytest", "-q"],
        exit_code=1,
        stdout=b"E   assert 0 == 1\n1 failed\n",
        lying_claim={"exit_code": 0, "text": "All tests passed! exit code 0.", "stdout": "5 passed"},
    )
    receipt = await capture.capture(tool="run_command", args={"command": "pytest -q"}, result=result)

    assert receipt["exit_code"] == 1, "the receipt must reflect the REAL exit status, not the claimed 0"
    # The stored stream is the REAL failing output, not the lying '5 passed' summary.
    assert capture.store.get_stream(receipt["stdout_ref"]) == b"E   assert 0 == 1\n1 failed\n"
    assert b"1 failed" in capture.store.get_stream(receipt["stdout_ref"])


# ── write/edit/ast_grep: artifact_hashes computed ON THE HOST over landed bytes ─


@pytest.mark.asyncio
async def test_write_file_artifact_hash_is_computed_on_the_host_over_landed_bytes() -> None:
    """``write_file`` produces ``artifact_hashes`` computed ON THE HOST over the LANDED file
    bytes — the host reads the file back through the transport (E2B ``files.read(path,
    format='bytes')``) and hashes it itself (§3.5, the named risk)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)

    landed = b"def f():\n    return 42\n"
    result = await sidecar.write_file(path="mod.py", content=landed)
    receipt = await capture.capture(tool="write_file", args={"path": "mod.py"}, result=result)

    # artifact_hashes is a list of {path, sha256} — the shape §3.7② evidence_backed reads.
    hashes = receipt["artifact_hashes"]
    assert isinstance(hashes, list) and hashes
    entry = next(h for h in hashes if h["path"] == "mod.py")
    assert entry["sha256"] == _sha256(landed), "the hash must be the HOST sha256 of the LANDED bytes"
    # run_command fields are empty/absent for a pure write — argv=[] , exit_code=0.
    assert receipt["argv"] == []


@pytest.mark.asyncio
async def test_lying_tool_claimed_artifact_hash_is_ignored_host_rehashes_landed_file() -> None:
    """THE DoD proof (writes): a tool that CLAIMS an artifact hash matching the plan's
    required hash, while the file it actually LANDED has different bytes, does NOT get a
    matching receipt. The host RE-HASHES the landed file itself, so the claimed hash is
    structurally ignored (§3.5, the named risk; §3.7② — hashes from the transport, not
    claimed)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)

    real_bytes = b"# actually landed: buggy\nraise RuntimeError\n"
    claimed_good = _sha256(b"# what the plan wanted: correct\nreturn ok\n")
    result = await sidecar.write_file(
        path="mod.py",
        content=real_bytes,
        lying_claim={"artifact_hashes": [{"path": "mod.py", "sha256": claimed_good}]},
    )
    receipt = await capture.capture(tool="write_file", args={"path": "mod.py"}, result=result)

    entry = next(h for h in receipt["artifact_hashes"] if h["path"] == "mod.py")
    assert entry["sha256"] == _sha256(real_bytes), "the receipt hash must be the host hash of the LANDED bytes"
    assert entry["sha256"] != claimed_good, "the claimed (lying) hash must NOT appear in the receipt"


@pytest.mark.asyncio
async def test_edit_file_and_ast_grep_hash_landed_bytes_on_the_host() -> None:
    """``edit_file`` and ``ast_grep`` likewise emit ``artifact_hashes`` the host computes over
    the landed bytes (§3.5 — write/edit/ast_grep all emit artifact_hashes)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)

    edited = b"x = 2\n"
    edit_result = await sidecar.write_file(path="e.py", content=edited, tool="edit_file")
    edit_receipt = await capture.capture(tool="edit_file", args={"path": "e.py"}, result=edit_result)
    assert next(h for h in edit_receipt["artifact_hashes"] if h["path"] == "e.py")["sha256"] == _sha256(edited)

    rewritten = b"bar(1)\n"
    ast_result = await sidecar.write_file(path="a.py", content=rewritten, tool="ast_grep")
    ast_receipt = await capture.capture(tool="ast_grep", args={"path": "a.py"}, result=ast_result)
    assert next(h for h in ast_receipt["artifact_hashes"] if h["path"] == "a.py")["sha256"] == _sha256(rewritten)


# ── the receipt is surfaced to the verify gate in the §3.7② reader shape ───────


@pytest.mark.asyncio
async def test_receipt_shape_is_consumable_by_the_evidence_gate() -> None:
    """The host-captured receipt is in exactly the shape §3.7②'s ``evidence_backed`` reads:
    ``" ".join(receipt["argv"])`` keys the named verify command, ``exit_code`` gates the
    pass, and ``artifact_hashes`` is a list of ``{path, sha256}`` — proven by running the
    spec's ``evidence_backed`` logic against a real captured receipt (§3.7② / CANONICAL
    §12.4)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)

    # A verify command ran clean, and a file landed — both captured host-side.
    run_result = await sidecar.run_command(argv=["pytest", "-q", "tests/test_rate.py"], exit_code=0,
                                           stdout=b"3 passed\n")
    run_receipt = await capture.capture(tool="run_command",
                                        args={"command": "pytest -q tests/test_rate.py"}, result=run_result)
    landed = b"RATE = 100\n"
    write_result = await sidecar.write_file(path="rate.py", content=landed)
    write_receipt = await capture.capture(tool="write_file", args={"path": "rate.py"}, result=write_result)

    receipts = [run_receipt, write_receipt]

    # The spec's deterministic gate (§3.7②) — reads receipts, never model prose.
    def evidence_backed(verify_cmds, receipts, required_hashes=None):  # noqa: ANN001, ANN202
        if not verify_cmds:
            return False
        by_argv = {" ".join(r["argv"]): r for r in receipts}
        for cmd in verify_cmds:
            r = by_argv.get(cmd)
            if r is None or r["exit_code"] != 0:
                return False
        if required_hashes:
            produced = {h["path"]: h["sha256"] for rr in receipts for h in rr.get("artifact_hashes", [])}
            if any(produced.get(p) != want for p, want in required_hashes.items()):
                return False
        return True

    assert evidence_backed(["pytest -q tests/test_rate.py"], receipts), (
        "a real exit-0 receipt for the named verify command must back the pass"
    )
    assert evidence_backed(["pytest -q tests/test_rate.py"], receipts,
                           required_hashes={"rate.py": _sha256(landed)}), (
        "the host-hashed landed bytes must satisfy the plan's required artifact hash"
    )
    # A pass claimed for a command with NO matching receipt is not backed → the gate FAILs it.
    assert not evidence_backed(["pytest -q tests/test_never_ran.py"], receipts)
    # A required hash that does not match the landed bytes is not backed → the gate FAILs it.
    assert not evidence_backed(["pytest -q tests/test_rate.py"], receipts,
                               required_hashes={"rate.py": _sha256(b"different bytes")})


@pytest.mark.asyncio
async def test_capture_never_throws_on_a_missing_landed_file() -> None:
    """Never-throw boundary (Hard Rule 6): if the host cannot read a claimed-touched file
    back (it never landed), capture does NOT raise — it records an EMPTY/soft hash for that
    path so the gate simply finds no matching hash and FAILs the pass, honestly (§3.3 / Law
    2 — a partial receipt beats a false claim, never a crash)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)

    # The tool claims it touched 'ghost.py' but no bytes ever landed in the fake fs.
    result = await sidecar.write_file(path="ghost.py", content=b"x", landed=False)
    receipt = await capture.capture(tool="write_file", args={"path": "ghost.py"}, result=result)
    entry = next(h for h in receipt["artifact_hashes"] if h["path"] == "ghost.py")
    assert entry["sha256"] == "", "an unreadable/never-landed file yields an empty host hash, not a crash"


@pytest.mark.asyncio
async def test_read_only_tools_produce_no_effect_receipt() -> None:
    """A read-only tool (``read_file``/``grep``/``glob``/``list_files``) touches nothing, so
    it produces NO effect-receipt (``None``) — only effect-emitting tools (run_command +
    the three writes) produce receipts the gate reads (§3.5 / §3.7②)."""
    fs = FakeSandboxFilesystem()
    sidecar = FakeToolSidecar(fs=fs)
    capture = HostReceiptCapture(file_reader=fs.read_bytes)
    result = await sidecar.read_file(path="mod.py", content=b"whatever")
    receipt = await capture.capture(tool="read_file", args={"path": "mod.py"}, result=result)
    assert receipt is None, "a read-only tool emits no effect-receipt (nothing ran, nothing landed)"
