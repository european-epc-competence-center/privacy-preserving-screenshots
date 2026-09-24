"""The ShinrAI client against a mock deployment: no key, no records, no network."""

import base64
import json

import httpx
import pytest

from conftest import png
from eecc_redact.errors import AppError
from eecc_redact.shinrai import Shinrai

KEY = "shr_test_NEVER_LOGGED_123"
BASE = "https://api.shinrai.innovius.io"
SPEC = {
    "paths": {
        "/v1/models": {},
        "/v2/projects/{project}/locations/{location}/image:redact": {},
    }
}
FINDINGS = {
    "findings": [
        {
            "infoType": {"name": "PERSON_NAME"},
            "location": {
                "contentLocations": [
                    {
                        "imageLocation": {
                            "boundingBoxes": [
                                {"top": 30, "left": 380, "width": 80, "height": 20},
                                {"top": 30, "left": 470, "width": 90, "height": 20},
                            ]
                        }
                    }
                ]
            },
        },
        {
            "infoType": {"name": "EMAIL_ADDRESS"},
            "location": {
                "contentLocations": [
                    {
                        "imageLocation": {
                            "boundingBoxes": [{"top": 90, "left": 130, "width": 420, "height": 24}]
                        }
                    }
                ]
            },
        },
    ]
}


V2_CAPABILITIES = {
    "api_version": "2.0.0-draft.2",
    "profile": "public",
    "mode": "global",
    "inputs": {"image": {"standard": "beta", "realtime": "unavailable", "jobs": "planned"}},
    "ocr": {"available": True, "engine": "tesseract 5.5.0", "languages": ["de", "en"]},
}
V2_RESULT = {
    "request_id": "req-v2",
    "api_version": "2.0.0-draft.2",
    "offset_unit": "codepoint",
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
                    "type": "YEAR",
                    "span": {"start": 40, "end": 44},
                    "confidence": 1.0,
                    "source": "pattern",
                },
            ],
        }
    ],
    "usage": {"records": 2, "charged": True, "tier": "standard", "balance_after": 49941},
}


def make_client(**cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, str(request.url)))
        assert request.url.host == "api.shinrai.innovius.io"
        if path in ("/v2/capabilities", "/v2/detect"):
            assert request.headers.get("authorization") == f"Bearer {KEY}"
        elif path.startswith("/v2/"):
            assert request.headers.get("x-goog-api-key") == KEY
        else:
            assert request.headers.get("authorization") == f"Bearer {KEY}"
        if path == "/v2/capabilities":
            if not cfg.get("v2", True):
                return httpx.Response(
                    404, json={"error": {"code": "not_found", "message": "No such resource."}}
                )
            return httpx.Response(200, json=cfg.get("v2_capabilities", V2_CAPABILITIES))
        if path == "/v2/detect":
            status = cfg.get("v2_status", 200)
            if status != 200:
                return httpx.Response(
                    status,
                    json={
                        "error": cfg.get(
                            "v2_error",
                            {
                                "code": "too_large",
                                "message": "The image exceeds the pixel limit.",
                                "use": "/v2/jobs",
                            },
                        )
                    },
                    headers={"x-request-id": "req-v2-err", "retry-after": "7"},
                )
            body = json.loads(request.content)
            assert (
                body["inputs"][0]["kind"] == "image"
                and body["inputs"][0]["media_type"] == "image/png"
            )
            assert body["output"] == {"box_granularity": "word"}
            return httpx.Response(
                200,
                headers={"x-records-remaining": "49941", "x-request-id": "req-v2"},
                json=V2_RESULT,
            )
        if path == "/v1/models":
            if cfg.get("models_status", 200) != 200:
                return httpx.Response(
                    cfg["models_status"], json={"error": {"message": "Key revoked"}}
                )
            return httpx.Response(200, json={"models": [{"id": "shinrai-latest"}]})
        if path == "/v1/usage":
            return httpx.Response(
                200, json={"plan": "starter", "balances": {"available_records": 49944.0}}
            )
        if path == "/openapi.json":
            return httpx.Response(200, json=cfg.get("spec", SPEC))
        if path == "/v2/infoTypes":
            return httpx.Response(
                200, json={"infoTypes": [{"name": "EMAIL_ADDRESS"}, {"name": "IBAN_CODE"}]}
            )
        if path.endswith("/image:redact"):
            status = cfg.get("image_status", 200)
            if status != 200:
                return httpx.Response(
                    status,
                    json={"error": {"message": "nope"}},
                    headers={"retry-after": "7", "x-request-id": "req-9"},
                )
            return httpx.Response(
                200,
                headers={
                    "x-records-remaining": "49943",
                    "x-request-id": "req-1",
                    "x-shinrai-warnings": "equivalence unverified",
                },
                json={
                    "redactedImage": base64.b64encode(png()).decode(),
                    "inspectResult": FINDINGS,
                },
            )
        return httpx.Response(404, json={"error": {"message": "no such route"}})

    http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5)
    return Shinrai(KEY, base_url=BASE, api=cfg.get("api", "auto"), http=http), calls


def test_detect_parses_findings_and_headers():
    client, _ = make_client(api="google")
    detection = client.detect(png())
    assert [f.info_type for f in detection.findings] == ["PERSON_NAME", "EMAIL_ADDRESS"]
    assert len(detection.boxes) == 3
    first = detection.findings[0].boxes[0]
    assert (first.x, first.y, first.w, first.h) == (380, 30, 80, 20)
    assert detection.records_remaining == 49943
    assert detection.warnings == "equivalence unverified"


def test_capabilities_are_read_without_spending_records():
    client, calls = make_client()
    caps = client.capabilities()
    assert caps.image_redact
    assert (caps.plan, caps.records) == ("starter", 49944)
    assert caps.models == ("shinrai-latest",)
    assert caps.info_types == ("EMAIL_ADDRESS", "IBAN_CODE")
    assert all(method == "GET" for method, _ in calls)


def test_a_deployment_without_image_redact_is_recognised():
    client, _ = make_client(spec={"paths": {"/v1/models": {}}})
    assert not client.capabilities().image_redact


@pytest.mark.parametrize(
    "status, fragment",
    [
        (401, "rejected your key"),
        (403, "rejected your key"),
        (402, "out of records"),
        (404, "does not serve image redaction"),
        (503, "does not serve image redaction"),
        (429, "Wait 7 seconds"),
        (500, "HTTP 500"),
    ],
)
def test_status_codes_become_messages_for_people(status, fragment):
    client, calls = make_client(image_status=status, api="google")
    with pytest.raises(AppError) as caught:
        client.detect(png())
    assert fragment in caught.value.message
    assert "req-9" in caught.value.detail
    assert KEY not in caught.value.message + caught.value.detail
    assert len(calls) == 1  # never retried


def test_a_bad_key_stops_the_capability_check_early():
    client, calls = make_client(models_status=401)
    with pytest.raises(AppError, match="rejected"):
        client.capabilities()
    assert len(calls) == 1


def test_network_failures_are_reported_not_retried():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = Shinrai(KEY, base_url=BASE, http=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(AppError, match="Could not reach ShinrAI") as caught:
        client.detect(png())
    assert caught.value.detail == "ConnectError"


def test_auto_takes_the_pii_api_v2_when_the_deployment_serves_images_on_it():
    client, calls = make_client()
    detection = client.detect(png())
    assert [(m, u.split("io")[1]) for m, u in calls] == [
        ("GET", "/v2/capabilities"),
        ("POST", "/v2/detect"),
    ]
    assert [f.info_type for f in detection.findings] == ["SURNAME", "EMAIL", "YEAR"]
    assert [len(f.boxes) for f in detection.findings] == [2, 1, 0]
    second = detection.findings[0].boxes[1]
    assert (second.x, second.y, second.w, second.h) == (
        470,
        30,
        90,
        20,
    )  # a fractional box never shrinks
    assert detection.records_remaining == 49941 and detection.warnings == ""
    client.detect(png())
    assert (
        sum(1 for _, u in calls if u.endswith("/v2/capabilities")) == 1
    )  # the route is decided once per client


def test_auto_falls_back_to_image_redact_where_v2_does_not_serve_images():
    for cfg in (
        {"v2": False},
        {"v2_capabilities": {**V2_CAPABILITIES, "inputs": {"image": {"standard": "unavailable"}}}},
        {"v2_capabilities": {**V2_CAPABILITIES, "ocr": {"available": False}}},
    ):
        client, calls = make_client(**cfg)
        detection = client.detect(png())
        assert [f.info_type for f in detection.findings] == ["PERSON_NAME", "EMAIL_ADDRESS"]
        assert calls[-1][1].endswith("/image:redact"), cfg


def test_the_api_setting_forces_a_route():
    client, calls = make_client()
    assert [
        f.info_type
        for f in Shinrai(KEY, base_url=BASE, api="google", http=client._http).detect(png()).findings
    ] == ["PERSON_NAME", "EMAIL_ADDRESS"]
    assert not any(u.endswith("/v2/capabilities") for _, u in calls)
    assert [
        f.info_type
        for f in Shinrai(KEY, base_url=BASE, api="v2", http=client._http).detect(png()).findings
    ] == ["SURNAME", "EMAIL", "YEAR"]
    with pytest.raises(AppError):
        Shinrai(KEY, base_url=BASE, api="v3", http=client._http)


def test_capabilities_report_the_v2_route_and_ocr_languages():
    client, _ = make_client()
    caps = client.capabilities()
    assert (
        caps.pii_api_v2
        and caps.image_v2
        and caps.ocr_languages == ("de", "en")
        and caps.route == "v2"
    )
    client, _ = make_client(v2=False)
    caps = client.capabilities()
    assert not caps.pii_api_v2 and not caps.image_v2 and caps.route == "google"


@pytest.mark.parametrize(
    "status, error, fragment",
    [
        (413, {"code": "too_large", "message": "too big"}, "larger than the deployment accepts"),
        (401, {"code": "invalid_key", "message": "no"}, "rejected your key"),
        (403, {"code": "tier_not_allowed", "message": "no"}, "rejected your key"),
        (402, {"code": "insufficient_records", "message": "no"}, "out of records"),
        (429, {"code": "rate_limited", "message": "slow"}, "Wait 7 seconds"),
        (
            501,
            {"code": "capability_unavailable", "message": "later", "use": "/v2/jobs"},
            "does not serve image redaction",
        ),
        (
            503,
            {"code": "degraded_refused", "message": "no model"},
            "does not serve image redaction",
        ),
    ],
)
def test_v2_error_envelopes_become_messages_for_people(status, error, fragment):
    client, _ = make_client(v2_status=status, v2_error=error)
    with pytest.raises(AppError) as info:
        client.detect(png())
    assert fragment in str(info.value)
    assert error["code"] in (info.value.detail or "") and "req-v2-err" in (info.value.detail or "")
