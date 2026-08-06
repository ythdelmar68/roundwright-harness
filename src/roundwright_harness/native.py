"""External native Codex SDK factory for Roundwright's live health gate.

The module deliberately resolves Roundwright only when ``native_factory`` is
called. This keeps the harness lockfile independent of whichever candidate is
under test while still returning that candidate's exact public contract types.
"""

from __future__ import annotations

import hashlib
import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, Sandbox
from openai_codex.errors import (
    InvalidParamsError,
    MethodNotFoundError,
    ParseError,
    RetryLimitExceededError,
    ServerBusyError,
    TransportClosedError,
)
from openai_codex.generated.v2_all import ReasoningEffort as SdkReasoningEffort

_READY_RESPONSE = {"status": "ready"}
_READY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ready"}},
    "required": ["status"],
    "additionalProperties": False,
}


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _failure_for(error: BaseException):
    """Map SDK failures to Roundwright's enum without returning provider text."""

    from roundwright.provider_health import CodexFailure

    if isinstance(error, RetryLimitExceededError | ServerBusyError):
        return CodexFailure.QUOTA_OR_RATE_LIMIT
    if isinstance(error, TransportClosedError):
        return CodexFailure.TRANSPORT_OR_PROVIDER_OUTAGE
    if isinstance(error, InvalidParamsError | MethodNotFoundError):
        return CodexFailure.SDK_INCOMPATIBLE
    if isinstance(error, ParseError):
        return CodexFailure.MALFORMED_RESPONSE

    # The current SDK surfaces failed model turns as RuntimeError. Inspect only
    # for classification; the text is never included in evidence or output.
    detail = str(error).casefold()
    if any(marker in detail for marker in ("authenticate", "authentication", "login", "unauthorized", "401")):
        return CodexFailure.AUTH_MISSING
    if any(marker in detail for marker in ("quota", "rate limit", "usage limit", "429")):
        return CodexFailure.QUOTA_OR_RATE_LIMIT
    if "model" in detail and any(marker in detail for marker in ("unsupported", "unavailable", "not found")):
        return CodexFailure.MODEL_UNAVAILABLE
    if any(marker in detail for marker in ("approval", "sandbox", "denied")):
        return CodexFailure.SANDBOX_OR_APPROVAL_DENIED
    if any(marker in detail for marker in ("connection", "transport", "timed out", "timeout", "503")):
        return CodexFailure.TRANSPORT_OR_PROVIDER_OUTAGE
    return CodexFailure.UNKNOWN


class _NativeCodexSdkBackend:
    """One role-owned SDK process boundary with no credential accessor."""

    def __init__(self, audit: object, cwd: Path) -> None:
        self._audit = audit
        self._cwd = cwd

    def audit_runtime(self):
        return self._audit

    def qualify_read_only(self, request):
        from roundwright.provider_health import CodexFailure, ProbeOutcome

        try:
            effort = SdkReasoningEffort(request.reasoning_effort)
        except ValueError:
            return ProbeOutcome(False, CodexFailure.SDK_INCOMPATIBLE)

        try:
            with Codex() as codex:
                account = codex.account()
                if account.requires_openai_auth and account.account is None:
                    return ProbeOutcome(False, CodexFailure.AUTH_MISSING)
                thread = codex.thread_start(
                    approval_mode=ApprovalMode.deny_all,
                    cwd=str(self._cwd),
                    developer_instructions=(
                        "This is a bounded provider-health probe. Do not call tools, "
                        "inspect files, or perform user work. Return only the requested schema."
                    ),
                    ephemeral=True,
                    model=request.model,
                    sandbox=Sandbox.read_only,
                )
                result = thread.run(
                    "Return the provider-health readiness object now.",
                    effort=effort,
                    output_schema=_READY_SCHEMA,
                    sandbox=Sandbox.read_only,
                )
            response = json.loads(result.final_response or "null")
            if response != _READY_RESPONSE:
                return ProbeOutcome(False, CodexFailure.MALFORMED_RESPONSE)
            return ProbeOutcome(True)
        except Exception as error:
            return ProbeOutcome(False, _failure_for(error))


def native_factory():
    """Return the candidate's exact `(store, contract, configuration)` tuple.

    The calling gate must set ``ROUNDWRIGHT_CONTRACT_COMMIT`` and run with the
    candidate checkout as its current directory. No credential value or auth
    cache path crosses this boundary.
    """

    from roundwright.configuration import load_configuration
    from roundwright.provider_health import (
        CodexCapability,
        CodexHealthContract,
        CodexRuntimeAudit,
        RoleBoundCodexCredentialStore,
    )
    from roundwright.provider_recovery import ProviderRole

    contract_commit = os.environ["ROUNDWRIGHT_CONTRACT_COMMIT"]
    sdk_version = version("openai-codex")
    runtime_version = version("openai-codex-cli-bin")
    capabilities = tuple(
        CodexCapability(model, effort.value)
        for model in ("gpt-5.6-terra", "gpt-5.6-sol")
        for effort in SdkReasoningEffort
        if effort.value in {"low", "medium", "high", "xhigh"}
    )
    audit = CodexRuntimeAudit(sdk_version, runtime_version, capabilities)
    repository_root = Path.cwd().resolve(strict=True)
    configuration = load_configuration(
        cwd=repository_root,
        environment=os.environ,
        home=repository_root,
    )
    store_identity = _digest(
        {"schema": "roundwright-harness-native-store/v1", "sdk": sdk_version, "runtime": runtime_version}
    )
    channels = {
        role: (
            _digest({"schema": "roundwright-harness-native-channel/v1", "role": role.value}),
            _NativeCodexSdkBackend(audit, repository_root),
        )
        for role in ProviderRole
    }
    store = RoleBoundCodexCredentialStore(store_identity, channels)
    contract = CodexHealthContract(sdk_version, runtime_version, contract_commit)
    return store, contract, configuration
