"""github_auth.py — installation-token mint (PM-AUTH-01/02/03).

Fakes ONLY at the ``call_external`` seam (the model/vendor seam). The JWT signing is REAL
(RS256 via cryptography over a freshly-generated key), so the Bearer the mint sends is a real,
verifiable App JWT. The upstream POST body is served by a recording stub that stands in for the
GitHub endpoint at exactly the ``call_external`` boundary.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives import hashes

from premeeting.github_auth import AuthError, InstallationTokenMinter, build_app_jwt


def _gen_key() -> tuple[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return pem, key.public_key()


def _decode_segment(seg: str) -> dict[str, Any]:
    pad = "=" * (-len(seg) % 4)
    return json.loads(base64.urlsafe_b64decode(seg + pad))


class _Outcome:
    def __init__(self, value: Any) -> None:
        self.value = value


class RecordingSeam:
    """A stub ``call_external`` that records each call and drives the op against a canned body.

    This is the ONLY fake — it sits exactly at the ``libs.http.call_external`` seam. It patches
    ``http_client`` (inside the op) via the token_response it hands back, so the mint's real POST
    construction runs but no live network call is made.
    """

    def __init__(self, *, token: str = "ghs_TESTTOKEN_abc123", status: int = 201) -> None:
        self.token = token
        self.status = status
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, op: Any, *, service: str, unit_cost_usd: float = 0.0) -> _Outcome:
        self.calls.append({"service": service})
        # Run the op with http_client monkeypatched to our fake so the POST is constructed for
        # real (url + headers) but resolves to the canned response.
        value = await _run_op_with_fake_http(op, token=self.token, status=self.status, sink=self.calls[-1])
        return _Outcome(value)


async def _run_op_with_fake_http(op: Any, *, token: str, status: int, sink: dict[str, Any]) -> Any:
    class _Resp:
        status_code = status

        def json(self) -> dict[str, Any]:
            return {"token": token, "expires_at": "2026-07-27T13:00:00Z",
                    "permissions": {"contents": "read"}}

    class _Client:
        def __init__(self, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, *, headers: dict[str, str], json: Any) -> _Resp:
            sink["url"] = url
            sink["headers"] = headers
            sink["json"] = json
            return _Resp()

    # http_client is imported lazily inside the op; patch the source module.
    import libs.http.src.http.external as ext
    saved = ext.http_client
    ext.http_client = _Client  # type: ignore[assignment]
    try:
        return await op()
    finally:
        ext.http_client = saved  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_pm_auth_01_one_post_through_call_external_with_bearer_jwt() -> None:
    pem, pub = _gen_key()
    seam = RecordingSeam()
    minter = InstallationTokenMinter(app_id="12345", private_key_pem=pem, call_external=seam)

    token = await minter.mint("999")

    assert token == "ghs_TESTTOKEN_abc123"
    # EXACTLY one upstream call, to the access_tokens path, carrying a Bearer JWT.
    assert len(seam.calls) == 1
    call = seam.calls[0]
    assert call["url"].endswith("/app/installations/999/access_tokens")
    auth = call["headers"]["Authorization"]
    assert auth.startswith("Bearer ")
    # The Bearer is a REAL RS256 App JWT — verify signature + claims against the public key.
    jwt = auth[len("Bearer "):]
    header_b64, claims_b64, sig_b64 = jwt.split(".")
    claims = _decode_segment(claims_b64)
    assert claims["iss"] == "12345"
    assert claims["exp"] > claims["iat"]
    pad = "=" * (-len(sig_b64) % 4)
    sig = base64.urlsafe_b64decode(sig_b64 + pad)
    pub.verify(sig, f"{header_b64}.{claims_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())


@pytest.mark.asyncio
async def test_pm_auth_02_never_cached_two_mints_two_calls_and_no_log_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pem, _ = _gen_key()
    seam = RecordingSeam(token="ghs_LEAKCANARY_zzz")
    minter = InstallationTokenMinter(app_id="1", private_key_pem=pem, call_external=seam)

    with caplog.at_level(logging.DEBUG):
        t1 = await minter.mint("42")
        t2 = await minter.mint("42")

    # Two mints → two DISTINCT upstream calls (no instance cache / reuse).
    assert len(seam.calls) == 2
    assert t1 == t2 == "ghs_LEAKCANARY_zzz"
    # The token string appears in ZERO log records (PM-AUTH-02: never logged).
    for rec in caplog.records:
        assert "ghs_LEAKCANARY_zzz" not in rec.getMessage()
    # And the minter holds no token attribute (never cached on the instance).
    assert not any("ghs_LEAKCANARY_zzz" in str(v) for v in vars(minter).values())


@pytest.mark.asyncio
async def test_pm_auth_03_non_2xx_raises_typed_autherror() -> None:
    pem, _ = _gen_key()
    seam = RecordingSeam(status=401)
    minter = InstallationTokenMinter(app_id="1", private_key_pem=pem, call_external=seam)

    with pytest.raises(AuthError) as exc:
        await minter.mint("42")
    # The error names the auth failure (the pipeline maps this to not_ready('auth: ...')).
    assert "401" in str(exc.value) or "mint" in str(exc.value).lower()


def test_build_app_jwt_is_deterministic_for_a_fixed_clock() -> None:
    pem, _ = _gen_key()
    a = build_app_jwt(app_id="7", private_key_pem=pem, now=1_700_000_000)
    b = build_app_jwt(app_id="7", private_key_pem=pem, now=1_700_000_000)
    assert a == b  # same clock + key → identical JWT (deterministic sign)
