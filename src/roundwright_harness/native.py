"""External native Codex SDK factory for Roundwright's live health gate.

The module deliberately resolves Roundwright only when ``native_factory`` is
called. This keeps the harness lockfile independent of whichever candidate is
under test while still returning that candidate's exact public contract types.
"""

from __future__ import annotations

import hashlib
import json
import os
from enum import Enum
from importlib.metadata import version
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, Sandbox
from openai_codex.errors import (
    InternalRpcError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    ParseError,
    RetryLimitExceededError,
    ServerBusyError,
    TransportClosedError,
)
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CodexErrorInfoValue,
    HttpConnectionFailedCodexErrorInfo,
    ItemCompletedNotification,
    MessagePhase,
    ReasoningEffort as SdkReasoningEffort,
    ResponseStreamConnectionFailedCodexErrorInfo,
    ResponseStreamDisconnectedCodexErrorInfo,
    ResponseTooManyFailedAttemptsCodexErrorInfo,
    TurnCompletedNotification,
    TurnError,
    TurnStatus,
)

_READY_RESPONSE = {"status": "ready"}
_READY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ready"}},
    "required": ["status"],
    "additionalProperties": False,
}


class _FailureKind(Enum):
    """Internal names that exactly match Roundwright's public failure enum."""

    AUTH_MISSING = "AUTH_MISSING"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    QUOTA_OR_RATE_LIMIT = "QUOTA_OR_RATE_LIMIT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    SDK_INCOMPATIBLE = "SDK_INCOMPATIBLE"
    SANDBOX_OR_APPROVAL_DENIED = "SANDBOX_OR_APPROVAL_DENIED"
    TRANSPORT_OR_PROVIDER_OUTAGE = "TRANSPORT_OR_PROVIDER_OUTAGE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    UNKNOWN = "UNKNOWN"


class _IncompleteCatalogError(RuntimeError):
    """The stable high-level SDK did not return the complete model catalog."""


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _roundwright_failure(kind: _FailureKind):
    from roundwright.provider_health import CodexFailure

    return CodexFailure[kind.name]


def _auth_failure(*, account_present: bool) -> _FailureKind:
    return _FailureKind.AUTH_EXPIRED if account_present else _FailureKind.AUTH_MISSING


def _http_failure(status: int | None, *, account_present: bool) -> _FailureKind:
    """Classify only stable HTTP status semantics exposed by the SDK."""

    if status == 401:
        return _auth_failure(account_present=account_present)
    if status == 429:
        return _FailureKind.QUOTA_OR_RATE_LIMIT
    if status == 408 or (status is not None and status >= 500):
        return _FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE
    return _FailureKind.UNKNOWN


def _turn_failure_kind(error: TurnError | None, *, account_present: bool) -> _FailureKind:
    """Map the SDK's structured turn failure without inspecting provider text."""

    if error is None or error.codex_error_info is None:
        return _FailureKind.UNKNOWN

    detail = error.codex_error_info.root
    if isinstance(detail, CodexErrorInfoValue):
        stable_values = {
            CodexErrorInfoValue.unauthorized: _auth_failure(account_present=account_present),
            CodexErrorInfoValue.usage_limit_exceeded: _FailureKind.QUOTA_OR_RATE_LIMIT,
            CodexErrorInfoValue.server_overloaded: _FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE,
            CodexErrorInfoValue.internal_server_error: _FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE,
            CodexErrorInfoValue.sandbox_error: _FailureKind.SANDBOX_OR_APPROVAL_DENIED,
            CodexErrorInfoValue.cyber_policy: _FailureKind.SANDBOX_OR_APPROVAL_DENIED,
        }
        return stable_values.get(detail, _FailureKind.UNKNOWN)

    http_variants = (
        (HttpConnectionFailedCodexErrorInfo, "http_connection_failed"),
        (ResponseStreamConnectionFailedCodexErrorInfo, "response_stream_connection_failed"),
        (ResponseStreamDisconnectedCodexErrorInfo, "response_stream_disconnected"),
        (ResponseTooManyFailedAttemptsCodexErrorInfo, "response_too_many_failed_attempts"),
    )
    for variant, attribute in http_variants:
        if isinstance(detail, variant):
            status = getattr(detail, attribute).http_status_code
            classified = _http_failure(status, account_present=account_present)
            if classified is not _FailureKind.UNKNOWN:
                return classified
            return _FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE
    return _FailureKind.UNKNOWN


def _exception_failure_kind(error: BaseException) -> _FailureKind:
    """Map typed SDK exceptions; untyped failures deliberately stay unknown."""

    if isinstance(
        error,
        (RetryLimitExceededError, ServerBusyError, InternalRpcError, TransportClosedError),
    ):
        return _FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE
    if isinstance(error, (InvalidRequestError, InvalidParamsError, MethodNotFoundError)):
        return _FailureKind.SDK_INCOMPATIBLE
    if isinstance(error, ParseError):
        return _FailureKind.MALFORMED_RESPONSE
    return _FailureKind.UNKNOWN


def _capability_pairs(model_list: object) -> tuple[tuple[str, str], ...]:
    """Return factual model/effort pairs from the SDK's validated catalog."""

    if model_list.next_cursor is not None:
        raise _IncompleteCatalogError
    pairs = {
        (model.model, option.reasoning_effort.value)
        for model in model_list.data
        for option in model.supported_reasoning_efforts
        if model.model and option.reasoning_effort.value
    }
    return tuple(sorted(pairs))


def _readiness_failure(response_text: str | None) -> _FailureKind | None:
    try:
        response = json.loads(response_text or "null")
    except json.JSONDecodeError:
        return _FailureKind.MALFORMED_RESPONSE
    if response != _READY_RESPONSE:
        return _FailureKind.MALFORMED_RESPONSE
    return None


def _run_probe_turn(
    thread: object,
    effort: SdkReasoningEffort,
    *,
    account_present: bool,
):
    """Consume the turn stream so structured terminal errors are preserved."""

    handle = thread.turn(
        "Return the provider-health readiness object now.",
        effort=effort,
        output_schema=_READY_SCHEMA,
        sandbox=Sandbox.read_only,
    )
    final_response: str | None = None
    fallback_response: str | None = None
    completed = None

    stream = handle.stream()
    try:
        for event in stream:
            payload = event.payload
            if isinstance(payload, ItemCompletedNotification) and payload.turn_id == handle.id:
                item = payload.item.root if hasattr(payload.item, "root") else payload.item
                if isinstance(item, AgentMessageThreadItem):
                    if item.phase == MessagePhase.final_answer:
                        final_response = item.text
                    elif item.phase is None:
                        fallback_response = item.text
            elif isinstance(payload, TurnCompletedNotification) and payload.turn.id == handle.id:
                completed = payload.turn
    finally:
        stream.close()

    if completed is None:
        return None, _FailureKind.MALFORMED_RESPONSE
    if completed.status == TurnStatus.failed:
        return None, _turn_failure_kind(completed.error, account_present=account_present)
    if completed.status != TurnStatus.completed:
        return None, _FailureKind.UNKNOWN
    return final_response if final_response is not None else fallback_response, None


class _NativeCodexSdkBackend:
    """One role-owned SDK process boundary with no credential accessor."""

    def __init__(self, audit: object, cwd: Path) -> None:
        self._audit = audit
        self._cwd = cwd

    def audit_runtime(self):
        return self._audit

    def qualify_read_only(self, request):
        from roundwright.provider_health import ProbeOutcome

        try:
            effort = SdkReasoningEffort(request.reasoning_effort)
        except ValueError:
            return ProbeOutcome(False, _roundwright_failure(_FailureKind.SDK_INCOMPATIBLE))

        try:
            with Codex() as codex:
                account = codex.account()
                account_present = account.account is not None
                if account.requires_openai_auth and not account_present:
                    return ProbeOutcome(False, _roundwright_failure(_FailureKind.AUTH_MISSING))
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
                response_text, failure = _run_probe_turn(
                    thread,
                    effort,
                    account_present=account_present,
                )
            if failure is not None:
                return ProbeOutcome(False, _roundwright_failure(failure))
            readiness_failure = _readiness_failure(response_text)
            if readiness_failure is not None:
                return ProbeOutcome(False, _roundwright_failure(readiness_failure))
            return ProbeOutcome(True)
        except Exception as error:
            return ProbeOutcome(False, _roundwright_failure(_exception_failure_kind(error)))


def native_factory():
    """Return the candidate's exact ``(store, contract, configuration)`` tuple.

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
    with Codex() as codex:
        capability_pairs = _capability_pairs(codex.models())
    capabilities = tuple(CodexCapability(model, effort) for model, effort in capability_pairs)
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
