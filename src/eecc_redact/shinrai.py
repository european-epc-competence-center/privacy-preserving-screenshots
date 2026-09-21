"""The ShinrAI client.

One call to `image:redact` returns every finding with its pixel boxes; the
boxes are drawn locally. The call is never retried automatically: the route
takes no idempotency key, and a dropped connection does not prove the call was
not charged. The key goes only to the configured base URL, and redirects are
not followed.
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


@dataclass(frozen=True)
class Capabilities:
    """What one key and deployment allow. Read-only calls; no records spent."""

    image_redact: bool = False
    plan: str = ""
    records: int | None = None
    models: tuple[str, ...] = ()
    info_types: tuple[str, ...] = ()


class Shinrai:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        project: str = APP_NAME,
        location: str = "global",
        http: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.location = location
        self._key = key
        self._http = http or httpx.Client(timeout=60, follow_redirects=False)

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

        return Capabilities(image_redact, plan, records, models, info_types)

    def detect(self, png: bytes) -> Detection:
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
            request_id=resp.headers.get("x-request-id", ""),
            warnings=resp.headers.get("x-shinrai-warnings", ""),
        )


def _raise(resp: httpx.Response) -> None:
    """Turn a bad response into an error with a message worth showing."""
    status = resp.status_code
    detail = f"HTTP {status}: {_error_text(resp)}"
    if request_id := resp.headers.get("x-request-id"):
        detail += f" (request {request_id})"
    if status in (401, 403):
        raise AppError(
            "ShinrAI rejected your key. Check it is the right kind: a sandbox key does not "
            "work against production.",
            detail=detail,
        )
    if status == 402:
        raise AppError("Your ShinrAI plan is out of records.", detail=detail)
    if status in (404, 501, 503):
        raise AppError("This ShinrAI deployment does not serve image redaction.", detail=detail)
    if status == 429:
        wait = min(max(_float(resp.headers.get("retry-after"), 1.0), 0.5), 30.0)
        raise AppError(
            f"ShinrAI is rate limiting this key. Wait {wait:g} seconds and try again.",
            detail=detail,
        )
    raise AppError(f"ShinrAI returned HTTP {status}.", detail=detail)


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
