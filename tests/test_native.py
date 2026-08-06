from __future__ import annotations

from types import SimpleNamespace

import pytest
from openai_codex.generated.v2_all import (
    CodexErrorInfo,
    CodexErrorInfoValue,
    HttpConnectionFailed,
    HttpConnectionFailedCodexErrorInfo,
    ModelListResponse,
    ReasoningEffort,
    Turn,
    TurnCompletedNotification,
    TurnError,
    TurnStatus,
)

from roundwright_harness import native


def _turn_error(info: object | None, message: str = "provider-controlled text") -> TurnError:
    return TurnError(
        message=message,
        codex_error_info=None if info is None else CodexErrorInfo(root=info),
    )


def test_native_module_has_stable_public_factory() -> None:
    assert callable(native.native_factory)
    assert native._digest({"role": "worker"}).startswith("sha256:")
    assert len(native._digest({"role": "worker"})) == 71


def test_untyped_exception_text_never_drives_classification() -> None:
    error = RuntimeError("unauthorized 401 quota rate limit model unavailable")

    assert native._exception_failure_kind(error) is native._FailureKind.UNKNOWN


def test_unstructured_turn_error_text_never_drives_classification() -> None:
    error = _turn_error(None, message="unauthorized 401")

    assert (
        native._turn_failure_kind(error, account_present=True)
        is native._FailureKind.UNKNOWN
    )


def test_structured_unauthorized_distinguishes_missing_and_expired_auth() -> None:
    error = _turn_error(CodexErrorInfoValue.unauthorized)

    assert (
        native._turn_failure_kind(error, account_present=False)
        is native._FailureKind.AUTH_MISSING
    )
    assert (
        native._turn_failure_kind(error, account_present=True)
        is native._FailureKind.AUTH_EXPIRED
    )


def test_structured_usage_and_outage_values_use_stable_categories() -> None:
    usage = _turn_error(CodexErrorInfoValue.usage_limit_exceeded)
    outage = _turn_error(CodexErrorInfoValue.server_overloaded)

    assert (
        native._turn_failure_kind(usage, account_present=True)
        is native._FailureKind.QUOTA_OR_RATE_LIMIT
    )
    assert (
        native._turn_failure_kind(outage, account_present=True)
        is native._FailureKind.TRANSPORT_OR_PROVIDER_OUTAGE
    )


def test_structured_http_status_is_used_without_provider_message() -> None:
    detail = HttpConnectionFailedCodexErrorInfo(
        http_connection_failed=HttpConnectionFailed(http_status_code=401)
    )
    error = _turn_error(detail, message="deliberately misleading quota text")

    assert (
        native._turn_failure_kind(error, account_present=True)
        is native._FailureKind.AUTH_EXPIRED
    )


def test_probe_stream_preserves_structured_terminal_failure() -> None:
    error = _turn_error(CodexErrorInfoValue.unauthorized)
    completed = TurnCompletedNotification(
        thread_id="thread-1",
        turn=Turn(
            id="turn-1",
            items=[],
            status=TurnStatus.failed,
            error=error,
        ),
    )

    def events():
        yield SimpleNamespace(payload=completed)

    handle = SimpleNamespace(id="turn-1", stream=events)
    thread = SimpleNamespace(turn=lambda *_args, **_kwargs: handle)

    response, failure = native._run_probe_turn(
        thread,
        ReasoningEffort.high,
        account_present=True,
    )

    assert response is None
    assert failure is native._FailureKind.AUTH_EXPIRED


def test_readiness_response_must_be_exact_and_valid_json() -> None:
    assert native._readiness_failure('{"status":"ready"}') is None
    assert (
        native._readiness_failure('{"status":"not-ready"}')
        is native._FailureKind.MALFORMED_RESPONSE
    )
    assert (
        native._readiness_failure("provider prose")
        is native._FailureKind.MALFORMED_RESPONSE
    )


def test_capabilities_are_configured_pairs_confirmed_by_reported_catalog() -> None:
    model_list = ModelListResponse.model_validate(
        {
            "data": [
                {
                    "id": "preset-a",
                    "model": "gpt-example-a",
                    "displayName": "Example A",
                    "description": "",
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium", "description": ""},
                        {"reasoningEffort": "high", "description": ""},
                    ],
                    "hidden": False,
                    "isDefault": True,
                },
                {
                    "id": "preset-b",
                    "model": "gpt-example-b",
                    "displayName": "Example B",
                    "description": "",
                    "defaultReasoningEffort": "low",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": ""},
                    ],
                    "hidden": False,
                    "isDefault": False,
                },
            ]
        }
    )

    configured_pairs = (
        ("gpt-example-a", "high"),
        ("gpt-example-b", "low"),
        ("gpt-missing", "high"),
    )

    assert native._capability_pairs(model_list, configured_pairs) == (
        ("gpt-example-a", "high"),
        ("gpt-example-b", "low"),
    )


def test_configured_capabilities_are_deduplicated_across_roles() -> None:
    worker = SimpleNamespace(model="gpt-worker", reasoning_effort=SimpleNamespace(value="high"))
    primary = SimpleNamespace(model="gpt-supervisor", reasoning_effort=SimpleNamespace(value="xhigh"))
    fallback = SimpleNamespace(model="gpt-worker", reasoning_effort=SimpleNamespace(value="high"))
    configuration = SimpleNamespace(
        worker=SimpleNamespace(value=worker),
        supervisor_attempt_profiles=SimpleNamespace(value=(primary, fallback)),
    )

    assert native._configured_capability_pairs(configuration) == (
        ("gpt-supervisor", "xhigh"),
        ("gpt-worker", "high"),
    )


def test_incomplete_catalog_fails_closed_instead_of_guessing_capabilities() -> None:
    model_list = ModelListResponse(data=[], next_cursor="next-page")

    with pytest.raises(native._IncompleteCatalogError):
        native._capability_pairs(model_list, (("gpt-example", "high"),))
