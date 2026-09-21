"""The ShinrAI client against a mock deployment: no key, no records, no network."""

import base64

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


def make_client(**cfg):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, str(request.url)))
        assert request.url.host == "api.shinrai.innovius.io"
        if path.startswith("/v2/"):
            assert request.headers.get("x-goog-api-key") == KEY
        else:
            assert request.headers.get("authorization") == f"Bearer {KEY}"
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
    return Shinrai(KEY, base_url=BASE, http=http), calls


def test_detect_parses_findings_and_headers():
    client, _ = make_client()
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
    client, calls = make_client(image_status=status)
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
