"""The ShinrAI client against a mock PII API v2 deployment: no key, no records, no network."""

import base64
import json

import httpx
import pytest

from conftest import png
from eecc_redact.errors import AppError
from eecc_redact.shinrai import NO_IMAGES, Shinrai

KEY = "shr_test_NEVER_LOGGED_123"
BASE = "https://api.shinrai.innovius.io"
CAPABILITIES = {
    "api_version": "2.0.0-draft.4",
    "profile": "public",
    "mode": "global",
    "models": [{"id": "v1.4", "status": "ga", "languages": ["de", "en"]}],
    "inputs": {"image": {"standard": "beta", "realtime": "beta", "jobs": "planned"}},
    "actions": {"mask": "ga"},
    "restore": {"algorithms": []},
    "engine": {"status": "ok", "model": "v1.4"},
    "ocr": {"available": True, "languages": ["de", "en"]},
}
USAGE = {"plan": "starter", "available_records": 49944.0, "last_30_days": {}}
TYPES = {
    "canonical": [
        {"type": "IBAN", "personal": True},
        {"type": "EMAIL", "personal": True},
        {"type": "YEAR", "personal": False},
        {"type": "PERSON"},
    ]
}
RESULT = {
    "request_id": "req-v2",
    "api_version": "2.0.0-draft.4",
    "offset_unit": "codepoint",
    "engine": {"model": "v1.4", "layers_requested": [], "layers_run": [], "degraded": False},
    "results": [
        {
            "input_id": "capture",
            "status": "ok",
            "media": {"media_type": "image/png", "width": 120, "height": 40, "box_unit": "px"},
            "entities": [
                {
                    "id": "e1",
                    "type": "SURNAME",
                    "span": {"start": 6, "end": 13},
                    "confidence": 0.9,
                    "source": "model",
                    "coords": {
                        "boxes": [
                            {"page": 1, "box": [380, 30, 80, 20]},
                            {"page": 1, "box": [470.4, 30, 89.2, 20]},
                        ]
                    },
                },
                {
                    "id": "e2",
                    "type": "EMAIL",
                    "span": {"start": 20, "end": 36},
                    "confidence": 1.0,
                    "source": "pattern",
                    "coords": {"boxes": [{"page": 1, "box": [130, 90, 420, 24]}]},
                },
                {
                    "id": "e3",
                    "type": "PHONE",
                    "span": {"start": 40, "end": 52},
                    "confidence": 1.0,
                    "source": "pattern",
                },
            ],
        }
    ],
    "usage": {"records": 2, "charged": True, "tier": "standard", "balance_after": 49940},
}


def make_client(**cfg):
    """`errors` maps a path to (status, v2 error); anything outside the v2 routes fails."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        assert request.url.host == "api.shinrai.innovius.io"
        assert request.headers.get("authorization") == f"Bearer {KEY}"
        if path in cfg.get("errors", {}):
            status, error = cfg["errors"][path]
            return httpx.Response(
                status,
                json={"error": {**error, "request_id": "req-err", "retryable": False}},
                headers={"x-request-id": "req-err", "retry-after": "7"},
            )
        if path == "/v2/capabilities":
            return httpx.Response(200, json=cfg.get("capabilities", CAPABILITIES))
        if path == "/v2/usage":
            return httpx.Response(200, json=USAGE)
        if path == "/v2/types":
            return httpx.Response(200, json=TYPES)
        if path == "/v2/detect":
            sent = json.loads(request.content)
            (capture,) = sent["inputs"]
            assert capture["kind"] == "image" and capture["media_type"] == "image/png"
            assert base64.b64decode(capture["data_b64"]) == png()
            assert sent["output"] == {"box_granularity": "word"}
            return httpx.Response(
                200,
                headers=cfg.get("detect_headers", {"x-records-remaining": "49941"}),
                json=cfg.get("result", RESULT),
            )
        raise AssertionError(f"not a PII API v2 route: {request.method} {path}")

    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    return Shinrai(KEY, base_url=BASE, http=http), calls


def test_detect_posts_the_capture_once_and_reads_entities_with_their_boxes():
    client, calls = make_client()
    detection = client.detect(png())
    assert calls == [("POST", "/v2/detect")]
    assert [f.info_type for f in detection.findings] == ["SURNAME", "EMAIL", "PHONE"]
    assert [len(f.boxes) for f in detection.findings] == [2, 1, 0]
    first, second = detection.findings[0].boxes
    assert (first.x, first.y, first.w, first.h) == (380, 30, 80, 20)
    assert (second.x, second.y, second.w, second.h) == (470, 30, 90, 20)  # never shrinks
    assert detection.records_remaining == 49941


def test_records_left_come_from_the_body_when_the_header_is_missing():
    client, _ = make_client(detect_headers={})
    assert client.detect(png()).records_remaining == 49940


@pytest.mark.parametrize(
    "result, fragment",
    [
        (
            {
                **RESULT,
                "results": [
                    {
                        "input_id": "capture",
                        "status": "failed",
                        "error": {"code": "backend_unavailable", "message": "OCR failed"},
                    }
                ],
            },
            "failed: backend_unavailable | OCR failed (request req-v2)",
        ),
        ({**RESULT, "results": []}, "no result (request req-v2)"),
    ],
)
def test_a_capture_that_was_not_read_is_an_error_never_nothing_found(result, fragment):
    client, _ = make_client(result=result)
    with pytest.raises(AppError, match="could not check this capture") as caught:
        client.detect(png())
    assert fragment in caught.value.detail


def test_capabilities_are_read_without_spending_records():
    client, calls = make_client()
    caps = client.capabilities()
    assert caps.serves_images
    assert (caps.plan, caps.records) == ("starter", 49944)
    assert caps.models == ("v1.4",)
    assert caps.types == ("EMAIL", "IBAN", "PERSON")  # personal data only
    assert caps.ocr_languages == ("de", "en")
    assert calls == [("GET", "/v2/capabilities"), ("GET", "/v2/usage"), ("GET", "/v2/types")]


@pytest.mark.parametrize(
    "capabilities",
    [
        {**CAPABILITIES, "inputs": {"image": {"standard": "unavailable"}}},
        {**CAPABILITIES, "inputs": {"text": {"standard": "ga"}}},
        {**CAPABILITIES, "ocr": {"available": False}},
    ],
)
def test_images_are_not_served_without_the_image_input_or_the_ocr(capabilities):
    client, _ = make_client(capabilities=capabilities)
    assert not client.capabilities().serves_images


def test_a_deployment_that_names_only_its_default_model_reports_that_one():
    client, _ = make_client(capabilities={k: v for k, v in CAPABILITIES.items() if k != "models"})
    assert client.capabilities().models == ("v1.4",)


def test_a_deployment_without_metering_or_types_still_passes_the_check():
    missing = (501, {"code": "capability_unavailable", "message": "not here"})
    client, _ = make_client(errors={"/v2/usage": missing, "/v2/types": missing})
    caps = client.capabilities()
    assert caps.serves_images
    assert (caps.plan, caps.records, caps.types) == ("", None, ())


def test_a_bad_key_stops_the_capability_check_early():
    client, calls = make_client(
        errors={"/v2/capabilities": (401, {"code": "invalid_key", "message": "revoked"})}
    )
    with pytest.raises(AppError, match="rejected your key"):
        client.capabilities()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status, error, fragment",
    [
        (413, {"code": "too_large", "message": "too big"}, "larger than the deployment accepts"),
        (401, {"code": "invalid_key", "message": "no"}, "rejected your key"),
        (403, {"code": "tier_not_allowed", "message": "no"}, "rejected your key"),
        (402, {"code": "insufficient_records", "message": "no"}, "out of records"),
        (429, {"code": "rate_limited", "message": "slow"}, "Wait 7 seconds"),
        (404, {"code": "not_found", "message": "no such route"}, NO_IMAGES),
        (501, {"code": "capability_unavailable", "message": "later"}, NO_IMAGES),
        (503, {"code": "degraded_refused", "message": "no model"}, "Try again in a moment"),
        (500, {"code": "internal_error", "message": "oops"}, "HTTP 500"),
    ],
)
def test_v2_error_envelopes_become_messages_for_people(status, error, fragment):
    client, calls = make_client(errors={"/v2/detect": (status, error)})
    with pytest.raises(AppError) as caught:
        client.detect(png())
    assert fragment in caught.value.message
    assert error["code"] in caught.value.detail and "req-err" in caught.value.detail
    assert KEY not in caught.value.message + caught.value.detail
    assert len(calls) == 1  # never retried


def test_an_error_page_that_is_not_json_is_still_reported():
    def gateway(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    client = Shinrai(KEY, base_url=BASE, http=httpx.Client(transport=httpx.MockTransport(gateway)))
    with pytest.raises(AppError, match="HTTP 502") as caught:
        client.detect(png())
    assert "Bad Gateway" in caught.value.detail


def test_network_failures_are_reported_not_retried():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = Shinrai(KEY, base_url=BASE, http=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(AppError, match="Could not reach ShinrAI") as caught:
        client.detect(png())
    assert caught.value.detail == "ConnectError"
