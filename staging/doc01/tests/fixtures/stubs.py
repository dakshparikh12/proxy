"""Doc 01 · stubs, instruments, and webhook builders for code_intel evidence.

Two families live here:

* **Instruments** (``*Counter``, ``*Tracer``, ``*Instrumented``) — passive probes
  the product writes into (``record``/``note``) so a test can assert *how* the
  pipeline behaved (LLM calls == 0, git never pushed, rebuild happened once).
* **Fixtures / stubs** (``*Stub``, ``*Repo``, ``*Fixture``, ``*_webhook_fixture``)
  — deterministic inputs. Repo-backed ones build a real local git repo via
  :mod:`repos` so clone/scan paths are exercised for real, no network.

Pure test doubles: nothing imports product code, so import is clean before the
product exists. ``small_repo_fixture`` and ``push_webhook_fixture`` are re-exported
here because several ``tests/test_m*.py`` import them from ``tests.fixtures.stubs``.
"""
from __future__ import annotations

import threading
import time
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.fixtures.repos import build_git_repo, small_repo_fixture  # noqa: F401

# --------------------------------------------------------------------------- #
# Recorded-operation value type                                                #
# --------------------------------------------------------------------------- #

Op = namedtuple("Op", ["type", "detail"])
Op.__new__.__defaults__ = (None,)  # type: ignore[attr-defined]

Conn = namedtuple("Conn", ["type", "dsn", "path"])
Conn.__new__.__defaults__ = (None, None)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Nango / token minting                                                        #
# --------------------------------------------------------------------------- #


class NangoStub:
    """Mints a distinct short-lived token per operation (never cached)."""

    def __init__(self, tokens: list):
        self._tokens = list(tokens)
        self._idx = 0
        self.mint_call_count = 0
        self.minted_tokens: list = []

    def mint(self, *args: Any, **kwargs: Any) -> str:
        token = self._tokens[self._idx % len(self._tokens)]
        self._idx += 1
        self.mint_call_count += 1
        self.minted_tokens.append(token)
        return token

    # aliases the product might use
    mint_token = mint
    get_token = mint


# --------------------------------------------------------------------------- #
# Log capture                                                                  #
# --------------------------------------------------------------------------- #


class LogCapture:
    """Capture everything written to the stdlib root logger + stdout/stderr."""

    def __init__(self) -> None:
        self.output = ""
        self._buffer: list = []
        self._handler = None

    def __enter__(self) -> "LogCapture":
        import io
        import logging

        self._stream = io.StringIO()
        self._handler = logging.StreamHandler(self._stream)
        self._handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        self._prev_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(self._handler)
        return self

    def __exit__(self, *exc: Any) -> None:
        import logging

        root = logging.getLogger()
        root.removeHandler(self._handler)
        root.setLevel(self._prev_level)
        self.output = self._stream.getvalue()


# --------------------------------------------------------------------------- #
# Tenant isolation fixture                                                     #
# --------------------------------------------------------------------------- #


class TwoTenantCloneFixture:
    """Two tenants cloning the same repo into per-tenant volumes."""

    def __init__(self) -> None:
        repo, sha = build_git_repo(
            "two-tenant-src",
            {
                "README.md": "# shared upstream\n",
                "secret_file.py": "def secret():\n    return 'A-only'\n",
                "pkg/__init__.py": "",
                "pkg/mod.py": "def some_fn():\n    return 1\n",
            },
        )
        self.repo_url = str(repo)
        self.expected_sha = sha
        self.tenant_a_node_ids = {"pkg/mod.py::some_fn", "table::a_only"}
        self.tenant_b_node_ids = {"pkg/mod.py::some_fn", "table::b_only"}

    def open_as_tenant(self, tenant_id: str, path: Path):
        """Open ``path`` as ``tenant_id``; cross-tenant access is denied."""
        parts = Path(path).parts
        # expect /tenants/<tenant>/...
        owner = None
        if "tenants" in parts:
            i = parts.index("tenants")
            if i + 1 < len(parts):
                owner = parts[i + 1]
        if owner is not None and owner != tenant_id:
            raise PermissionError(
                f"tenant {tenant_id!r} may not read tenant {owner!r} volume: {path}"
            )
        return open(path)


def path_traversal_args(tenant_b_base: str, tenant_a_target: str) -> dict:
    """Craft a ../ traversal from tenant-B's base toward tenant-A's file."""
    base = tenant_b_base.rstrip("/")
    traversal = f"{base}/../{Path(tenant_a_target).name}"
    return {
        "path": traversal,
        "tenant_b_base": tenant_b_base,
        "tenant_a_target": tenant_a_target,
    }


# --------------------------------------------------------------------------- #
# Git behaviour stubs / interceptor                                            #
# --------------------------------------------------------------------------- #


class GitInterceptor:
    """Records every git argv the pipeline would exec (never runs push)."""

    def __init__(self) -> None:
        self.recorded_args: list = []

    def record(self, args) -> None:
        self.recorded_args.append(list(args))

    # common aliases
    def __call__(self, args) -> None:
        self.record(args)

    def note(self, args) -> None:
        self.record(args)

    def reset(self) -> None:
        self.recorded_args = []


class LargeFileCountStub:
    def __init__(self, file_count: int):
        self.file_count = file_count

    def count(self) -> int:
        return self.file_count


class LargeLOCStub:
    def __init__(self, loc_count: int):
        self.loc_count = loc_count

    def count(self) -> int:
        return self.loc_count


class RepoWithExecutableScript:
    """A repo carrying an executable that must NEVER run during clone/index."""

    executable_name = "malicious_hook.sh"

    def __init__(self, sentinel_path: Path):
        self.sentinel_path = Path(sentinel_path)
        repo, sha = build_git_repo(
            "exec-script-repo",
            {
                "pkg/__init__.py": "",
                "pkg/mod.py": "def mod():\n    return 1\n",
                self.executable_name: (
                    "#!/bin/sh\n"
                    f"touch '{self.sentinel_path}'\n"
                ),
            },
        )
        self.url = str(repo)
        self.expected_sha = sha


# --------------------------------------------------------------------------- #
# Secret / gitleaks fixtures                                                   #
# --------------------------------------------------------------------------- #


class PlantedSecretsRepo:
    """A repo with a secret-bearing file gitleaks must catch + exclude."""

    def __init__(
        self,
        secret_introduced_on_push: bool = False,
        secret_value: str = "AKIAIOSFODNN7EXAMPLE",
    ):
        self.secret_value = secret_value
        self.secret_file = ".env"
        self.new_secret_file = "config/secrets.env"
        files = {
            "pkg/__init__.py": "",
            "pkg/mod.py": "def mod():\n    return 1\n",
            ".env": f"AWS_SECRET_ACCESS_KEY={secret_value}\n",
            "node_modules/dep.js": "module.exports = 1;\n",
        }
        name = (
            "planted-secrets-push"
            if secret_introduced_on_push
            else "planted-secrets"
        )
        repo, sha = build_git_repo(name, files)
        self.url = str(repo)
        self.expected_sha = sha
        self.secret_introduced_on_push = secret_introduced_on_push


class SecretInNonExcludedFile:
    """A secret embedded in an ordinary .py file (must be redacted, not excluded)."""

    def __init__(self, secret_value: str = "AKIAIOSFODNN7EXAMPLE"):
        self.secret_value = secret_value
        self.file_path = "app/config.py"
        self.symbol_in_file = "load_config"
        files = {
            "app/__init__.py": "",
            "app/config.py": (
                "def load_config():\n"
                f"    api_key = '{secret_value}'\n"
                "    return api_key\n"
            ),
        }
        repo, sha = build_git_repo("secret-in-source", files)
        self.url = str(repo)
        self.expected_sha = sha


class GitleaksInstrumented:
    def __init__(self) -> None:
        self.call_count = 0
        self.scanned_paths: list = []

    def record(self, paths=None) -> None:
        self.call_count += 1
        if paths:
            self.scanned_paths.extend(paths)

    note_call = record

    def reset(self) -> None:
        self.call_count = 0
        self.scanned_paths = []


class PolicyGlobsFixture:
    def __init__(self, globs: list):
        self.globs = list(globs)


# --------------------------------------------------------------------------- #
# Counters / tracers                                                           #
# --------------------------------------------------------------------------- #


class LLMCallCounter:
    def __init__(self) -> None:
        self.call_count = 0

    def record(self, *a: Any, **k: Any) -> None:
        self.call_count += 1

    note = record

    def reset(self) -> None:
        self.call_count = 0


class DBOperationCounter:
    def __init__(self) -> None:
        self.recorded_operations: list = []

    def record(self, op_type: str, detail: Any = None) -> None:
        self.recorded_operations.append(Op(op_type, detail))

    note = record

    def reset(self) -> None:
        self.recorded_operations = []


class DBQueryCounter:
    def __init__(self) -> None:
        self.query_count = 0

    def record(self, *a: Any, **k: Any) -> None:
        self.query_count += 1

    note = record

    def reset(self) -> None:
        self.query_count = 0


class DBConnectionTracer:
    def __init__(self) -> None:
        self.opened_connections: list = []

    def record(self, conn_type: str, dsn: str = None, path: str = None) -> None:
        self.opened_connections.append(Conn(conn_type, dsn, path))

    note = record


class FactoryCounter:
    def __init__(self) -> None:
        self.created_count = 0

    def record(self, *a: Any, **k: Any) -> None:
        self.created_count += 1

    note = record


class GraphRebuildCounter:
    def __init__(self) -> None:
        self.count = 0

    def record(self, *a: Any, **k: Any) -> None:
        self.count += 1

    note = record

    def reset(self) -> None:
        self.count = 0


class ConcurrencyInstrumented:
    """Tracks peak concurrency of parallel file reads."""

    def __init__(self) -> None:
        self.max_concurrent = 0
        self._current = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self._current += 1
            self.max_concurrent = max(self.max_concurrent, self._current)

    def exit(self) -> None:
        with self._lock:
            self._current -= 1

    def __enter__(self) -> "ConcurrencyInstrumented":
        self.enter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.exit()


class EventLog:
    def __init__(self) -> None:
        self.operations: list = []

    def record(self, op_type: str, detail: Any = None) -> None:
        self.operations.append(Op(op_type, detail))

    note = record


# --------------------------------------------------------------------------- #
# LSP stubs / lifecycle                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class Reference:
    file: str
    line: int
    confidence: str = "resolved"


class LspStubWarm:
    """A warm LSP that resolves references instantly and exactly."""

    is_alive = True

    def __init__(self) -> None:
        self.started = True

    def references(self, symbol: str, **kwargs: Any) -> list:
        return [Reference("pkg/util.py", 1), Reference("pkg/a.py", 5)]

    def start(self) -> None:
        self.is_alive = True

    def restart(self) -> None:
        self.is_alive = True


class LspStubSlow:
    """An LSP that hangs longer than the timeout, forcing a grep fallback."""

    is_alive = True

    def __init__(self, delay_s: float = 5.0):
        self.delay_s = delay_s

    def references(self, symbol: str, **kwargs: Any) -> list:
        time.sleep(self.delay_s)
        return []

    def start(self) -> None:
        self.is_alive = True

    def restart(self) -> None:
        self.is_alive = True


class LspLifecycleInstrumented:
    """Wraps an LSP stub and records restarts + warm state after connect/push."""

    def __init__(self, inner: Any = None):
        self.inner = inner if inner is not None else LspStubWarm()
        self.restart_count = 0
        self.is_running_after_connect = False
        self.is_running_after_push = False
        self.is_alive = True

    def references(self, symbol: str, **kwargs: Any):
        return self.inner.references(symbol, **kwargs)

    def restart(self) -> None:
        self.restart_count += 1
        self.is_alive = True
        if hasattr(self.inner, "restart"):
            self.inner.restart()

    def mark_connected(self) -> None:
        self.is_running_after_connect = True

    def mark_pushed(self) -> None:
        self.is_running_after_push = True


# --------------------------------------------------------------------------- #
# Webhook builders                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class Webhook:
    kind: str
    repo_url: str = ""
    sha: str = ""
    delivery_guid: str = "delivery-1"
    changed_files: list = field(default_factory=list)
    num_commits: int = 1
    signature_valid: bool = True
    tenant_id: str = ""
    payload: bytes = b"{}"
    headers: dict = field(default_factory=dict)


def push_webhook_fixture(
    repo_url: str,
    sha: str = "deadbeef",
    delivery_guid: str = "delivery-1",
    changed_files: list = None,
    num_commits: int = 1,
) -> Webhook:
    return Webhook(
        kind="push",
        repo_url=repo_url,
        sha=sha,
        delivery_guid=delivery_guid,
        changed_files=list(changed_files) if changed_files else [],
        num_commits=num_commits,
        signature_valid=True,
        headers={"X-GitHub-Delivery": delivery_guid, "X-Hub-Signature-256": "sha256=valid"},
    )


def bad_hmac_webhook_fixture() -> Webhook:
    return Webhook(
        kind="push",
        repo_url="https://github.com/example/repo",
        sha="abc123",
        delivery_guid="delivery-bad",
        signature_valid=False,
        headers={"X-Hub-Signature-256": "sha256=deadbeefbadsignature"},
    )


def uninstall_webhook_fixture(tenant_id: str) -> Webhook:
    return Webhook(
        kind="uninstall",
        tenant_id=tenant_id,
        signature_valid=True,
        headers={"X-GitHub-Event": "installation"},
    )


# --------------------------------------------------------------------------- #
# Drift simulation                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class DriftSimulation:
    local_tip: str
    remote_tip: str
    commits_behind: int
