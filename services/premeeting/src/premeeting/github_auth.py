"""Direct GitHub-App installation-token mint (PM-AUTH-01/02/03).

To clone a customer's private repo we mint a short-lived installation token DIRECTLY (no Nango
in v0): sign a JWT with the App private key → ``POST /app/installations/<id>/access_tokens``
→ receive a ~1-hour, read-only-Contents-scoped token. The token rides ONLY in the clone URL
(``cloner.py``); it is NEVER cached on the instance and NEVER logged (PM-AUTH-02).

The mint goes THROUGH the ONE ``libs.http.call_external`` seam (retry + cost telemetry, the
§14 external-call hard rule) — no raw HTTP client lives here. A non-2xx / network failure
raises the typed :class:`AuthError` the pipeline turns into an honest ``not_ready`` reason
(PM-AUTH-03), never a silent success.

The whole mint sits behind ONE clean seam (:class:`InstallationTokenMinter`) so adding
GitLab/Bitbucket later swaps in a different broker without touching the clone path.
"""
from __future__ import annotations

import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

# The JWT for the mint is signed with the App private key (RS256). ``cryptography`` is the
# repo's crypto dep (no new pyjwt); the sign is a plain RS256 over the compact JWT segments.
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

_GITHUB_API = "https://api.github.com"
_JWT_TTL_S = 540  # 9 minutes — under GitHub's 10-min App-JWT ceiling (clock-skew margin).

# The type of the ONE external-call seam (``libs.http.call_external``): given an async op,
# returns an outcome whose ``.value`` is the op's result. Injected so a stub can record the
# single upstream POST in a test (fake ONLY at this seam), the real seam on the live path.
CallExternal = Callable[..., Awaitable[Any]]


class AuthError(RuntimeError):
    """A mint failure (non-2xx / network) — the pipeline turns this into an honest
    ``not_ready('auth: ...')`` reason, never a silent success (PM-AUTH-03 / Law 2)."""


def _b64url(raw: bytes) -> str:
    """Base64url without padding — the JWS compact-serialization alphabet."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_app_jwt(*, app_id: str, private_key_pem: str, now: int | None = None) -> str:
    """Sign the short-lived GitHub-App JWT (RS256) used as the Bearer for the mint POST.

    ``iss`` is the App id, ``iat`` is backdated 60s for clock skew, ``exp`` is +9min (under
    GitHub's 10-min ceiling). The private key comes from Secret Manager on the live path; it
    is a function argument here and never stored on the instance.
    """
    issued = (now if now is not None else int(time.time())) - 60
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iat": issued, "exp": issued + _JWT_TTL_S, "iss": str(app_id)}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    # A GitHub-App key is RSA (RS256). Narrow to RSAPrivateKey so ``.sign`` resolves to the
    # PKCS1v15 + SHA256 overload; a non-RSA key is a mis-configuration surfaced as an AuthError.
    if not isinstance(key, RSAPrivateKey):
        raise AuthError("App private key is not an RSA key (RS256 required)")
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


class InstallationTokenMinter:
    """The ONE seam that mints a fresh installation token per operation — never cached.

    A new token is minted on every :meth:`mint` call (no instance cache), so a rotated/revoked
    grant can never linger past its single-operation lifetime (PM-AUTH-02). The ``call_external``
    seam is injected so the single upstream POST is provable in a test and metered on the live
    path. Adding a second provider later is a new minter behind this same interface — the clone
    path never changes.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        call_external: CallExternal,
        api_base: str = _GITHUB_API,
    ) -> None:
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._call_external = call_external
        self._api_base = api_base.rstrip("/")

    async def mint(self, installation_id: str, *, now: int | None = None) -> str:
        """Mint a fresh, short-lived installation token for ``installation_id``.

        Signs an App JWT, POSTs it to ``/app/installations/<id>/access_tokens`` THROUGH the
        ``call_external`` seam (one POST, Bearer JWT), and returns ONLY the token string. The
        token is never retained on the instance and never logged. A non-2xx / transport fault
        raises :class:`AuthError` (PM-AUTH-03)."""
        app_jwt = build_app_jwt(
            app_id=self._app_id, private_key_pem=self._private_key_pem, now=now
        )
        url = f"{self._api_base}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async def _op() -> dict[str, Any]:
            # The raw client is constructed ONLY inside libs.http (the call_external home);
            # here we hand the seam a client-builder-free op that issues the POST via the
            # seam-provided http_client, so no raw client lives in this module.
            from libs.http.src.http.external import http_client

            async with http_client(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, json={})
                status = resp.status_code
                if status < 200 or status >= 300:
                    # Raise inside the op so a 4xx/5xx becomes an AuthError (not a retry-forever);
                    # the body is NOT logged (it may echo the request) — only the status code.
                    raise AuthError(f"token mint returned HTTP {status}")
                body: dict[str, Any] = resp.json()
                return body

        try:
            outcome = await self._call_external(
                _op, service="github-app-token-mint", unit_cost_usd=0.0
            )
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport/seam fault → honest typed error
            raise AuthError(f"token mint failed: {type(exc).__name__}") from exc

        value = getattr(outcome, "value", outcome)
        token = value.get("token") if isinstance(value, dict) else None
        if not token or not isinstance(token, str):
            raise AuthError("token mint response carried no token")
        return token


__all__ = ["AuthError", "InstallationTokenMinter", "build_app_jwt"]
