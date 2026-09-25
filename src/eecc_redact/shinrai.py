"""The ShinrAI client.

Two routes give the same thing, every finding with its pixel boxes; the boxes
are drawn locally:

* the ShinrAI PII API v2, `POST /v2/detect` with an image input. One contract
  for the hosted API, an in-cluster service and offline installations; every
  type the model offers, boxes in source pixels, no vendor mapping in between.
* the Google-DLP-compatible `image:redact`, kept for deployments that do not
  serve the v2 API yet.

`api = auto` (the default) reads `GET /v2/capabilities` once and takes v2
whenever the deployment serves images on it. Calls are never retried
automatically: neither route takes an idempotency key, and a dropped
connection does not prove the call was not charged. The key goes only to the
configured base URL, and redirects are not followed.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from eecc_redact import APP_NAME
from eecc_redact.errors import AppError
from eecc_redact.models import Box, Detection, Finding

DEFAULT_BASE_URL = "https://api.shinrai.innovius.io"
IMAGE_REDACT_PATHS = (
    "/v2/projects/{project}/locations/{location}/image:redact",
    "/v2/projects/{project}/image:redact",
)
API_CHOICES = ("auto", "v2", "google")
#: The v2 error codes that mean "your key or plan", not "this deployment".
KEY_CODES = {
    "invalid_key",
    "unauthorized",
    "forbidden_scope",
    "key_limit",
    "no_active_plan",
    "tier_not_allowed",
}


@dataclass(frozen=True)
class Capabilities:
    """What one key and deployment allow. Read-only calls; no records spent."""

    image_redact: bool = False
    plan: str = ""
    records: int | None = None
    models: tuple[str, ...] = ()
    info_types: tuple[str, ...] = ()
    #: The PII API v2 answers, and serves images on the synchronous endpoints.
    pii_api_v2: bool = False
    image_v2: bool = False
    ocr_languages: tuple[str, ...] = ()

    @property
    def route(self) -> str:
        """Which route `api = auto` takes on this deployment."""
        if self.image_v2:
            return "v2"
        return "google" if self.image_redact else "none"


class Shinrai:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        project: str = APP_NAME,
        location: str = "global",
        api: str = "auto",
        http: httpx.Client | None = None,
    ) -> None:
        if api not in API_CHOICES:
            raise AppError(f"Unknown api setting {api!r}; use one of {', '.join(API_CHOICES)}.")
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.location = location
        self.api = api
        self._key = key
        self._http = http or httpx.Client(timeout=60, follow_redirects=False)
        self._image_v2: bool | None = None

    def __enter__(self) -> "Shinrai":
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _send(
        self, method: str, path: str, *, google: bool = False, timeout: float = 60, **kwargs: Any
    ) -> httpx.Response:
        auth = {"x-goog-api-key": self._key} if google else {"Authorization": f"Bearer {self._key}"}
        try:
            return self._http.request(
                method, self.base_url + path, headers=auth, timeout=timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise AppError("Could not reach ShinrAI.", detail=type(exc).__name__) from exc

    # -- capabilities ----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        resp = self._send("GET", "/v1/models")
        if resp.status_code != 200:
            _raise(resp)
        body = resp.json() if resp.content else {}
        models = tuple(
            str(m.get("id") or m.get("name")) if isinstance(m, dict) else str(m)
            for m in body.get("models") or body.get("data") or []
        )

        plan, records = "", None
        usage = self._send("GET", "/v1/usage")
        if usage.status_code == 200:
            data = usage.json()
            plan = str(data.get("plan", ""))
            records = _int((data.get("balances") or {}).get("available_records"))

        image_redact = False
        spec = self._send("GET", "/openapi.json", timeout=30)
        if spec.status_code == 200:
            paths = (spec.json() or {}).get("paths") or {}
            image_redact = any(path in paths for path in IMAGE_REDACT_PATHS)

        info_types: tuple[str, ...] = ()
        types = self._send("GET", "/v2/infoTypes", google=True)
        if types.status_code == 200:
            info_types = tuple(
                sorted(
                    row["name"]
                    for row in (types.json() or {}).get("infoTypes") or []
                    if isinstance(row, dict) and row.get("name")
                )
            )

        pii_api_v2, image_v2, ocr_languages = self._v2_capabilities()
        self._image_v2 = image_v2
        return Capabilities(
            image_redact, plan, records, models, info_types, pii_api_v2, image_v2, ocr_languages
        )

    def _v2_capabilities(self) -> tuple[bool, bool, tuple[str, ...]]:
        """(v2 served, images served on v2, OCR languages). A missing route is a plain no."""
        resp = self._send("GET", "/v2/capabilities", timeout=30)
        if resp.status_code != 200:
            return False, False, ()
        try:
            body = resp.json() or {}
        except ValueError:
            return False, False, ()
        image = ((body.get("inputs") or {}).get("image") or {}).get("standard")
        ocr = body.get("ocr") or {}
        served = image in ("ga", "beta") and bool(ocr.get("available", True))
        return True, served, tuple(str(tag) for tag in ocr.get("languages") or ())

    # -- detection -------------------------------------------------------------

    def detect(self, png: bytes) -> Detection:
        if self.api == "google":
            return self._detect_google(png)
        if self.api == "v2":
            return self._detect_v2(png)
        if self._image_v2 is None:
            self._image_v2 = self._v2_capabilities()[1]
        return self._detect_v2(png) if self._image_v2 else self._detect_google(png)

    def _detect_v2(self, png: bytes) -> Detection:
        payload = {
            "inputs": [
                {
                    "id": "capture",
                    "kind": "image",
                    "media_type": "image/png",
                    "data_b64": base64.b64encode(png).decode(),
                }
            ],
            # one box per recognised word, as the Google route reports them;
            # the review toggles findings, not boxes
            "output": {"box_granularity": "word"},
        }
        resp = self._send("POST", "/v2/detect", json=payload, timeout=180)
        if resp.status_code != 200:
            _raise(resp)
        body = resp.json() or {}
        results = body.get("results") or []
        entities = (results[0].get("entities") or []) if results else []
        findings = tuple(
            Finding(
                str(entity.get("type") or "UNKNOWN"),
                tuple(_box(box) for box in ((entity.get("coords") or {}).get("boxes") or [])),
            )
            for entity in entities
        )
        usage = body.get("usage") or {}
        remaining = resp.headers.get("x-records-remaining") or usage.get("balance_after")
        return Detection(
            findings=findings,
            records_remaining=_int(remaining),
            warnings=resp.headers.get("x-shinrai-warnings", ""),
        )

    def _detect_google(self, png: bytes) -> Detection:
        location = f"/locations/{self.location}" if self.location else ""
        payload = {
            "byteItem": {"type": "IMAGE_PNG", "data": base64.b64encode(png).decode()},
            "includeFindings": True,
        }
        resp = self._send(
            "POST",
            f"/v2/projects/{self.project}{location}/image:redact",
            google=True,
            json=payload,
            timeout=120,
        )
        if resp.status_code != 200:
            _raise(resp)
        return Detection(
            findings=_findings(resp.json() or {}),
            records_remaining=_int(resp.headers.get("x-records-remaining")),
            warnings=resp.headers.get("x-shinrai-warnings", ""),
        )


def _box(box: dict[str, Any]) -> Box:
    """A v2 `{page, box: [x, y, w, h]}` in source pixels; a box never shrinks when rounded."""
    x, y, w, h = (float(v) for v in (box.get("box") or [0, 0, 0, 0]))
    left, top = int(x), int(y)
    return Box(left, top, max(0, int(-(-(x + w) // 1)) - left), max(0, int(-(-(y + h) // 1)) - top))


def _raise(resp: httpx.Response) -> None:
    """Turn a bad response into an error with a message worth showing."""
    status = resp.status_code
    code = _error_code(resp)
    detail = f"HTTP {status}: {_error_text(resp)}"
    if request_id := resp.headers.get("x-request-id"):
        detail += f" (request {request_id})"
    if status in (401, 403) or code in KEY_CODES:
        raise AppError(
            "ShinrAI rejected your key. Check it is the right kind: a sandbox key does not "
            "work against production.",
            detail=detail,
        )
    if status == 402:
        raise AppError("Your ShinrAI plan is out of records.", detail=detail)
    if status == 413 or code == "too_large":
        raise AppError(
            "This capture is larger than the deployment accepts; capture a smaller region.",
            detail=detail,
        )
    if status in (404, 501, 503):
        raise AppError("This ShinrAI deployment does not serve image redaction.", detail=detail)
    if status == 429:
        wait = min(max(_float(resp.headers.get("retry-after"), 1.0), 0.5), 30.0)
        raise AppError(
            f"ShinrAI is rate limiting this key. Wait {wait:g} seconds and try again.",
            detail=detail,
        )
    raise AppError(f"ShinrAI returned HTTP {status}.", detail=detail)


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("code", "")) if isinstance(error, dict) else ""


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200].strip()
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        parts = [str(error[k]) for k in ("code", "status", "message") if error.get(k)]
        return " | ".join(parts) or json.dumps(error)[:200]
    return json.dumps(body)[:200]


def _findings(body: dict[str, Any]) -> tuple[Finding, ...]:
    findings = []
    for raw in (body.get("inspectResult") or {}).get("findings") or []:
        boxes = tuple(
            Box(
                int(box.get("left", 0)),
                int(box.get("top", 0)),
                int(box.get("width", 0)),
                int(box.get("height", 0)),
            )
            for location in (raw.get("location") or {}).get("contentLocations") or []
            for box in (location.get("imageLocation") or {}).get("boundingBoxes") or []
        )
        findings.append(Finding((raw.get("infoType") or {}).get("name", "UNKNOWN"), boxes))
    return tuple(findings)


def _int(value: object) -> int | None:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float(value: str | None, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
