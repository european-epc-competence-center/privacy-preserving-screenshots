# ShinrAI API

Client: `src/eecc_redact/shinrai.py`. Only the PII API v2 is used

## References

- v2 spec, public, no key: `https://api.shinrai.innovius.io/v2/openapi.json`. Guide: https://shinrai.innovius.io/public-docs/pii-api-v2.md

## Calls

- Every call: `Authorization: Bearer <key>` (`shr_live_…` production, `shr_test_…` sandbox).
- `GET /v2/capabilities`: the key check (401 `invalid_key` for a bad key). Images are usable when `inputs.image.standard` is `ga`/`beta` and `ocr.available`. Models: `models[].id`, else `engine.model`.
- `GET /v2/usage` (plan, `available_records`) and `GET /v2/types` (`canonical`, `personal` flag): free, optional; a deployment without them still passes.
- `POST /v2/detect`: one `image` input (base64 PNG), `output.box_granularity: word`. Boxes `[x, y, w, h]` in source pixels. `results[0].status != "ok"` raises (fail closed, never "nothing detected"). Records left: `X-Records-Remaining` header, else `usage.balance_after`.

## Behaviour worth knowing

- `processing.on_degraded` defaults to `fail`: a missing detection layer is a 503 `degraded_refused`, never a partial result.
- Non-personal findings (years, amounts) come only as `annotations`, and only when requested; the app does not request them.
- Not retried. `/v2/detect` accepts an `Idempotency-Key`, the app does not send one.
- Errors: `{"error": {"code", "message", "request_id", "retryable", ...}}`; mapped to user messages in `_raise`.

## Consumers and tests

- `ui/settings.py` checks a key with `capabilities()` before storing it; `cli.py` `doctor` prints the capabilities; `pipeline.py` `client_for` builds the client from `Config.base_url`.
- `tests/test_shinrai.py`: mock transport that fails on any route outside the four v2 calls.
- Live check without a real key: a fake key against `/v2/capabilities` returns 401 `invalid_key`; free.
