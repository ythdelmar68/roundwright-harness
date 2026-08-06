from __future__ import annotations

from types import SimpleNamespace

from openai_codex.generated.v2_all import (
    CodexErrorInfo,
    CodexErrorInfoValue,
    HttpConnectionFailed,
    HttpConnectionFailedCodexErrorInfo,
    TurnError,
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


def test_capabilities_come_from_reported_model_catalog() -> None:
    model_list = SimpleNamespace(
        data=[
            SimpleNamespace(
                model="gpt-example-a",
                supported_reasoning_efforts=[
                    SimpleNamespace(reasoning_effort=SimpleNamespace(value="medium")),
                    SimpleNamespace(reasoning_effort=SimpleNamespace(value="high")),
                ],
            ),
            SimpleNamespace(
                model="gpt-example-b",
                supported_reasoning_efforts=[
                    SimpleNamespace(reasoning_effort=SimpleNamespace(value="low")),
                ],
            ),
        ]
    )

    assert native._capability_pairs(model_list) == (
        ("gpt-example-a", "high"),
        ("gpt-example-a", "medium"),
        ("gpt-example-b", "low"),
    )
