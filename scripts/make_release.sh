#!/usr/bin/env bash
# make_release.sh -- tag, build, publish, and create a GitHub release.
#
# Usage:
#   ./scripts/make_release.sh
#
# Prerequisites:
#   - uv installed
#   - UV_PUBLISH_TOKEN_TESTPYPI and UV_PUBLISH_TOKEN_PYPI env vars set
#   - gh CLI authenticated
#   - Working tree must be clean
#   - Current branch must be main

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "==> $*"; }
ok()    { echo "OK: $*"; }
die()   { echo "ERROR: $*" >&2; exit 1; }
skip()  { echo "SKIP: $*"; }

confirm() {
    # confirm <question>  -- exits 0 if user answers y/Y, 1 otherwise
    local prompt="$1"
    local _ans
    read -rp "${prompt} [y/N] " _ans
    [[ "${_ans}" == "y" || "${_ans}" == "Y" ]]
}

# ---------------------------------------------------------------------------
# 1. Read version from pyproject.toml
# ---------------------------------------------------------------------------
VERSION=$(python -c "
import tomllib, pathlib
d = tomllib.loads(pathlib.Path('pyproject.toml').read_text())
print(d['project']['version'])
")
[[ -z "$VERSION" ]] && die "Could not read version from pyproject.toml"
TAG="v${VERSION}"

info "Release: ${VERSION}  Tag: ${TAG}"

# ---------------------------------------------------------------------------
# 2. Sanity checks (non-interactive)
# ---------------------------------------------------------------------------
info "Running sanity checks"

[[ -z "$(git status --porcelain)" ]] || die "Working tree is dirty; commit or stash changes first"
ok "Working tree is clean"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
[[ "$BRANCH" == "main" ]] || die "Must be on main branch (currently on '${BRANCH}')"
ok "On main branch"

git rev-parse --verify "${TAG}" &>/dev/null && die "Tag ${TAG} already exists"
ok "Tag ${TAG} does not exist yet"

# ---------------------------------------------------------------------------
# 3. Create annotated tag
# ---------------------------------------------------------------------------
info "Step 1/6 -- Create annotated tag ${TAG}"
git log --oneline -5
confirm "Create annotated tag ${TAG}?" || { skip "Tag skipped -- aborting"; exit 0; }
git tag -a "${TAG}" -m "stonks-cli v${VERSION}"
ok "Tag ${TAG} created"

# ---------------------------------------------------------------------------
# 4. Push commits + tag to remote
# ---------------------------------------------------------------------------
info "Step 2/6 -- Push main branch and tag to origin"
git log --oneline origin/main..HEAD 2>/dev/null || true
confirm "Push main and ${TAG} to origin?" || { skip "Push skipped -- aborting"; exit 0; }
git push origin main
git push origin "${TAG}"
ok "Pushed main and ${TAG}"

# ---------------------------------------------------------------------------
# 5. Clean dist/ and build
# ---------------------------------------------------------------------------
info "Step 3/6 -- Build distribution"
[[ -d dist/ ]] && echo "Current dist/ contents:" && ls dist/ || true
confirm "Clean dist/ and run 'uv build'?" || { skip "Build skipped -- aborting"; exit 0; }
rm -rf dist/
uv build
ok "Build complete"
ls -lh dist/

# ---------------------------------------------------------------------------
# 6. Publish to TestPyPI
# ---------------------------------------------------------------------------
info "Step 4/6 -- Publish to TestPyPI"
echo "Artifacts to upload:"
ls dist/
confirm "Publish to TestPyPI?" || { skip "TestPyPI publish skipped -- aborting"; exit 0; }
[[ -z "${UV_PUBLISH_TOKEN_TESTPYPI:-}" ]] && die "UV_PUBLISH_TOKEN_TESTPYPI is not set"
uv publish --publish-url https://test.pypi.org/legacy/ --token "${UV_PUBLISH_TOKEN_TESTPYPI}"
ok "Published to TestPyPI"
echo "Verify at: https://test.pypi.org/project/stonks-cli/${VERSION}/"

# ---------------------------------------------------------------------------
# 7. Publish to PyPI
# ---------------------------------------------------------------------------
info "Step 5/6 -- Publish to production PyPI"
confirm "TestPyPI looks good? Publish to production PyPI?" || { skip "PyPI publish skipped -- aborting"; exit 0; }
[[ -z "${UV_PUBLISH_TOKEN_PYPI:-}" ]] && die "UV_PUBLISH_TOKEN_PYPI is not set"
uv publish --token "${UV_PUBLISH_TOKEN_PYPI}"
ok "Published to PyPI"
echo "Verify at: https://pypi.org/project/stonks-cli/${VERSION}/"

# ---------------------------------------------------------------------------
# 8. Create GitHub release
# ---------------------------------------------------------------------------
info "Step 6/6 -- Create GitHub release"
NOTES=$(awk -v v="[${VERSION}]" '
    $2 == v { found=1; next }
    /^## \[/ && found { exit }
    found { print }
' CHANGELOG.md | sed '1{/^$/d}')

echo "Release notes preview:"
echo "---"
echo "${NOTES}"
echo "---"
confirm "Create GitHub release ${TAG}?" || { skip "GitHub release skipped"; exit 0; }
gh release create "${TAG}" dist/* \
    --title "stonks-cli v${VERSION}" \
    --notes "${NOTES}"
ok "GitHub release created: $(gh release view "${TAG}" --json url -q .url)"
