"""The ShinrAI client, on the ShinrAI PII API v2.

`POST /v2/detect` with an image input returns every entity the model offers
with its boxes in the pixels of the capture; the boxes are drawn locally. One
contract serves the hosted API, an in-cluster service and offline
installations; the spec is `GET /v2/openapi.json` on the deployment.

Calls are never retried automatically: a dropped connection does not prove the
call was not charged. The key goes only to the configured base URL, as a bearer
token, and redirects are not followed.
"""

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from eecc_redact.config import DEFAULT_BASE_URL
from eecc_redact.errors import AppError
from eecc_redact.models import Box, Detection, Finding

#: The statuses in `GET /v2/capabilities` under which a feature can be used today.
SERVED = ("ga", "beta")
NO_IMAGES = "This ShinrAI deployment does not serve image detection on the PII API v2."
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
    """What one key and deployment allow. Free, read-only calls; no records spent."""

    #: `POST /v2/detect` takes images on the standard tier and the OCR is up.
    serves_images: bool = False
    plan: str = ""
    records: int | None = None
    models: tuple[str, ...] = ()
    #: The canonical types that are personal data (`GET /v2/types`).
    types: tuple[str, ...] = ()
    ocr_languages: tuple[str, ...] = ()


class Shinrai:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = key
        self._http = http or httpx.Client(timeout=60, follow_redirects=False)

    def __enter__(self) -> "Shinrai":
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _send(
        self, method: str, path: str, *, timeout: float = 60, **kwargs: Any
    ) -> httpx.Response:
        try:
            return self._http.request(
                method,
                self.base_url + path,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise AppError("Could not reach ShinrAI.", detail=type(exc).__name__) from exc

    # -- capabilities ----------------------------------------------------------

    def capabilities(self) -> Capabilities:
        """Also the key check: a key the deployment rejects raises here."""
        resp = self._send("GET", "/v2/capabilities", timeout=30)
        if resp.status_code != 200:
            _raise(resp)
        body = _json(resp)
        image = ((body.get("inputs") or {}).get("image") or {}).get("standard")
        ocr = body.get("ocr") or {}
        usage = self._optional("/v2/usage")
        types = self._optional("/v2/types")
        return Capabilities(
            serves_images=image in SERVED and bool(ocr.get("available", True)),
            plan=str(usage.get("plan") or ""),
            records=_int(usage.get("available_records")),
            models=_models(body),
            types=tuple(
                sorted(
                    str(row["type"])
                    for row in types.get("canonical") or ()
                    if isinstance(row, dict) and row.get("type") and row.get("personal", True)
                )
            ),
            ocr_languages=tuple(str(tag) for tag in ocr.get("languages") or ()),
        )

    def _optional(self, path: str) -> dict[str, Any]:
        """A free extra; a deployment without it (no metering, say) is not an error."""
        resp = self._send("GET", path, timeout=30)
        return _json(resp) if resp.status_code == 200 else {}

    # -- detection -------------------------------------------------------------

    def detect(self, png: bytes) -> Detection:
        payload = {
            "inputs": [
                {
                    "id": "capture",
                    "kind": "image",
                    "media_type": "image/png",
                    "data_b64": base64.b64encode(png).decode(),
                }
            ],
            # one box per recognised word; the review toggles findings, not boxes
            "output": {"box_granularity": "word"},
        }
        resp = self._send("POST", "/v2/detect", json=payload, timeout=180)
        if resp.status_code != 200:
            _raise(resp)
        body = _json(resp)
        result = next(iter(body.get("results") or ()), {})
        if result.get("status") != "ok":
            # Never "nothing detected" for a capture that was not read.
            reason = str(result.get("status", "no result"))
            if error := result.get("error"):
                reason += f": {_describe(error)}"
            raise AppError(
                "ShinrAI could not check this capture.",
                detail=f"{reason} (request {body.get('request_id', '?')})",
            )
        findings = tuple(
            Finding(
                str(entity.get("type") or "UNKNOWN"),
                tuple(_box(box) for box in ((entity.get("coords") or {}).get("boxes") or [])),
            )
            for entity in result.get("entities") or []
        )
        usage = body.get("usage") or {}
        remaining = resp.headers.get("x-records-remaining") or usage.get("balance_after")
        return Detection(findings=findings, records_remaining=_int(remaining))


def _models(capabilities: dict[str, Any]) -> tuple[str, ...]:
    """The models the deployment lists; the public profile may name only its default."""
    listed = tuple(
        str(model["id"])
        for model in capabilities.get("models") or ()
        if isinstance(model, dict) and model.get("id")
    )
    default = (capabilities.get("engine") or {}).get("model")
    return listed or ((str(default),) if default else ())


def _box(box: dict[str, Any]) -> Box:
    """A v2 `{page, box: [x, y, w, h]}` in source pixels; a box never shrinks when rounded."""
    x, y, w, h = (float(v) for v in (box.get("box") or [0, 0, 0, 0]))
    left, top = int(x), int(y)
    return Box(left, top, max(0, int(-(-(x + w) // 1)) - left), max(0, int(-(-(y + h) // 1)) - top))


def _raise(resp: httpx.Response) -> None:
    """Turn a v2 error envelope into an error with a message worth showing."""
    status = resp.status_code
    error = _json(resp).get("error")
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    reason = _describe(error) if isinstance(error, dict) else resp.text[:200].strip()
    detail = f"HTTP {status}: {reason}"
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
    if status in (404, 501):
        raise AppError(NO_IMAGES, detail=detail)
    if status == 503:
        raise AppError("ShinrAI is busy or partly down. Try again in a moment.", detail=detail)
    if status == 429:
        wait = min(max(_float(resp.headers.get("retry-after"), 1.0), 0.5), 30.0)
        raise AppError(
            f"ShinrAI is rate limiting this key. Wait {wait:g} seconds and try again.",
            detail=detail,
        )
    raise AppError(f"ShinrAI returned HTTP {status}.", detail=detail)


def _json(resp: httpx.Response) -> dict[str, Any]:
    """The body as an object; anything else counts as empty."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _describe(error: dict[str, Any]) -> str:
    parts = [str(error[k]) for k in ("code", "message") if error.get(k)]
    return " | ".join(parts) or json.dumps(error)[:200]


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
