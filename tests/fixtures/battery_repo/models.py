"""Shared data shapes for the request path."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    user_id: str
    expires_at: float


@dataclass(frozen=True)
class Profile:
    user_id: str
    display_name: str
