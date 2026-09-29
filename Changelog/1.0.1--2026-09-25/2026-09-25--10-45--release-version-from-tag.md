# Changed
- Release workflows take the package version from the `vX.Y.Z` tag
  - `pyproject.toml` and `uv.lock` are set with `uv version`, committed to the default branch, and the tag is moved onto that commit
  - The reported version follows that metadata, so tests no longer pin `0.1.0`
