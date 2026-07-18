"""Per-tenant envelope-key volume crypto (AC-HOST-013/014).

Each :class:`TenantVolume` owns a per-instance envelope key (a random 256-bit
AES-GCM key). Encryption/decryption round-trips through that key. Offboarding a
tenant destroys *only that instance's* key (crypto-shred): the key is
per-instance state, so destroying tenant A's key never affects tenant B.

Uses the ``cryptography`` library (AES-GCM) only — no hand-rolled disk-layer
encryption. The underlying Persistent Disk is KMS-encrypted at the infra floor
(see ``infra/code_intel.tf``); this module is the application envelope on top
of that floor.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12
_KEY_BYTES = 32


class KeyDestroyedError(RuntimeError):
    """Raised when a volume whose envelope key has been crypto-shredded is used."""


class TenantVolume:
    """A per-tenant encrypted volume with a per-instance envelope key."""

    def __init__(self, tenant: str) -> None:
        self._tenant = tenant
        # A distinct random envelope key per instance; two tenants (or two
        # instances) never share a key. ``None`` after crypto-shred.
        self._key: bytes | None = AESGCM.generate_key(bit_length=256)

    @property
    def tenant(self) -> str:
        return self._tenant

    def envelope_key(self) -> bytes:
        """Return this instance's envelope key (stable until destroyed)."""
        if self._key is None:
            raise KeyDestroyedError(
                f"envelope key for {self._tenant!r} was crypto-shredded"
            )
        return self._key

    def encrypt(self, plaintext: bytes) -> bytes:
        """AES-GCM encrypt ``plaintext``; nonce is prepended to the ciphertext."""
        key = self.envelope_key()
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(key).encrypt(nonce, plaintext, None)
        return nonce + ct

    def decrypt(self, ciphertext: bytes) -> bytes:
        """AES-GCM decrypt ``ciphertext``; raises once the key is destroyed."""
        key = self.envelope_key()
        nonce, ct = ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, ct, None)

    def destroy_key(self) -> None:
        """Crypto-shred: irrecoverably drop this instance's envelope key.

        Blast radius is bounded to this instance — the key is per-instance
        state, so other :class:`TenantVolume` instances are unaffected.
        """
        self._key = None
