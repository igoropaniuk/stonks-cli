# Release Process

This document describes the steps for cutting a new stonks-cli release.

## Prerequisites

- Write access to the repository
- Poetry installed and configured
- A PyPI account (and optionally a TestPyPI account)
- PyPI API tokens stored in your local Poetry config (see [Configure tokens](#5-configure-tokens))

---

## 1. Update the Changelog

Before tagging, document everything that changed since the last release in
[CHANGELOG.md](CHANGELOG.md):

1. Add a new `## [X.Y.Z] - YYYY-MM-DD` section at the top (below the header).
2. Group entries under `### Added`, `### Changed`, `### Deprecated`,
   `### Removed`, `### Fixed`, and/or `### Security` as appropriate.
3. Add a reference link at the bottom of the file:

   ```text
   [X.Y.Z]: https://github.com/igorpaniuk/stonks-cli/compare/vA.B.C...vX.Y.Z
   ```

   For the first release (e.g., v0.1.0), link to the release tag directly, as there
   is no previous version to compare with:

   ```text
   [0.1.0]: https://github.com/igorpaniuk/stonks-cli/releases/tag/v0.1.0
   ```

4. Commit the changelog update:

   ```bash
   git add CHANGELOG.md
   git commit -s -m "docs(changelog): update for vX.Y.Z"
   ```

---

## 2. Bump the Version Number

The version must be updated in the `pyproject.toml`:

```toml
[project]
version = "X.Y.Z"
```

Commit the updated file:

```bash
git add pyproject.toml
git commit -s -m "chore(release): bump version to X.Y.Z"
```

---

## 3. Create an Annotated Tag

Annotated tags (unlike lightweight tags) carry a tagger identity, date, and
message, and are the recommended way to mark releases.

```bash
git tag -a vX.Y.Z -m "stonks-cli vX.Y.Z"
```

Verify the tag was created correctly:

```bash
git show vX.Y.Z
```

Push the commit and the tag to the remote:

```bash
git push origin main
git push origin vX.Y.Z
```

---

## 4. Build the Distribution

Build both a source distribution and a wheel:

```bash
poetry build
```

Artifacts are written to `dist/`:

```text
dist/
  stonks_cli-X.Y.Z.tar.gz
  stonks_cli-X.Y.Z-py3-none-any.whl
```

---

## 5. Configure Tokens

Store your PyPI tokens in the Poetry keyring so they are not embedded in any
file:

```bash
# TestPyPI
poetry config pypi-token.testpypi <your-testpypi-token>

# PyPI (production)
poetry config pypi-token.pypi <your-pypi-token>
```

Alternatively, export them as environment variables before publishing:

```bash
export POETRY_PYPI_TOKEN_TESTPYPI=<your-testpypi-token>
export POETRY_PYPI_TOKEN_PYPI=<your-pypi-token>
```

---

## 6. Publish to TestPyPI (recommended first)

[TestPyPI](https://test.pypi.org/) is a separate instance intended for testing
the release process without affecting the production index.

Add it as a repository if you have not done so already:

```bash
poetry config repositories.testpypi https://test.pypi.org/legacy/
```

Publish:

```bash
poetry publish --repository testpypi
```

Verify the upload at `https://test.pypi.org/project/stonks-cli/` and optionally
install from TestPyPI to confirm:

```bash
pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  stonks-cli==X.Y.Z
```

---

## 7. Publish to PyPI (production)

Once you are satisfied with the TestPyPI release:

```bash
poetry publish
```

Verify the release at `https://pypi.org/project/stonks-cli/`.

---

## 8. Create a GitHub Release

After pushing the tag you can also create a GitHub release for visibility:

```bash
notes=$(awk -v v="[X.Y.Z]" '$2==v{f=1;next} /^## \[/{f=0} f' CHANGELOG.md)
gh release create vX.Y.Z dist/* \
  --title "stonks-cli vX.Y.Z" \
  --notes "$notes"
```

Or create it manually through the GitHub UI using the changelog section as the
release notes.

---

## Release Checklist

- [ ] Changelog updated and committed
- [ ] Version bumped in `pyproject.toml`
- [ ] Version bump committed
- [ ] Annotated tag `vX.Y.Z` created
- [ ] Tag and commits pushed to remote
- [ ] Distribution built with `poetry build`
- [ ] Published to TestPyPI and verified
- [ ] Published to PyPI (production)
- [ ] GitHub release created
