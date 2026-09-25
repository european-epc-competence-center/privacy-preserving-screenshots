# Release version

`pyproject.toml` `[project].version` is the version. `uv.lock` repeats it on the `eecc-redact` package. `eecc_redact.__version__` (`src/eecc_redact/__init__.py`) reads that metadata with `importlib.metadata`, and is `0.0.0` only when the checkout was never installed.

`packaging/set_release_version.py` runs from `.github/workflows/windows-installer.yml` and `linux-appimage.yml` on a `vX.Y.Z` tag. It sets the version with `uv version` (project file and lock only), commits that to the default branch, and moves the tag onto the new commit. The branch push is a fast-forward. The tag ref is force-updated only when the commits it gains change `pyproject.toml` and `uv.lock`.

The installers do not store a second version. `packaging/windows/build.ps1` and `packaging/linux/build.sh` read `__version__`. Inno Setup's `AppVersion` is passed in on the `ISCC` command line; `0.0.0` in `packaging/windows/eecc-redact.iss` applies only when that define is missing.
