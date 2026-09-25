# Notes

## Layout

- `src/eecc_redact/` — the application. `__version__` comes from the installed package metadata.
- `packaging/` — Windows installer (`windows/`) and Linux AppImage (`linux/`). Both read the version at build time.
- `.github/workflows/` — tests on every push, and the two release builds on `v*` tags.
- `pyproject.toml` and `uv.lock` — the only stored copy of the package version.

## Topics

- [Release version](./release.md) — a `vX.Y.Z` tag is written into the project and committed back by the release workflows.
