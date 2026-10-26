# Working in this repository

## Commits

- **Explain why, concisely.** A commit message answers "why was this change
  needed?", not "what was changed" (the diff already shows that). Use the
  conventional-commit subject style already in the history, e.g.
  `fix(fetcher): ...`, `feat(cli): ...`, and keep the body short.
- **Keep commits small.** A commit must change fewer than 500 lines in total
  (additions plus deletions, as reported by `git show --shortstat`). If a
  change is larger, split it into several commits that each build and pass
  the tests on their own, for example: model, then fetcher, then UI, then
  docs.
- **Sign-off only.** Every commit carries a `Signed-off-by:` trailer with the
  author's real name and email (`git commit -s`). Never add
  `Co-Authored-By:` or any other tool-attribution trailer.

## Before committing

- Run `make check-all` (ASCII check, ruff format and lint, mypy, pytest).
- Every bug fix includes a regression test; every feature includes tests
  and, when user-visible, a README section and a CHANGELOG entry under
  `[Unreleased]`.
- Source and docs are ASCII-only (`make ascii` enforces this).
