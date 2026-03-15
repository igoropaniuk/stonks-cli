# Contributing to stonks-cli

Thanks for your interest in contributing to **stonks-cli**!
stonks-cli is a terminal dashboard for tracking your investment portfolio
with live market prices.

This guide explains how to set up a development environment, run checks,
and submit changes.

---

## Development setup

### Prerequisites

- Python **3.11+**
- Poetry **2.0+**
- Git

### Clone the repository

```bash
git clone https://github.com/igorpaniuk/stonks-cli.git
cd stonks-cli
```

### Install dependencies

```bash
poetry install
```

### Install pre-commit hooks (recommended)

```bash
poetry run pre-commit install
```

---

## Running tests and checks

stonks-cli uses `ruff` (format + lint), `mypy`, and `pytest`.

### Run all checks at once

```bash
poetry run bash ./scripts/ci-check
```

### Run individual tools

```bash
poetry run ruff format --check .
poetry run ruff check .
poetry run mypy
poetry run pytest
```

### Auto-format

```bash
poetry run ruff format .
poetry run ruff check . --fix
```

---

## What to test

When adding or changing behaviour, please include unit tests.

Guidelines:

- Prefer **pure / deterministic** functions and small units.
- Mock external services (Yahoo Finance / yfinance network calls).
- Tests should not require network access -- stub the price refresh worker
  in TUI tests (see `tests/conftest.py` for the existing autouse fixture).

---

## Pull request workflow

We follow a **clean history** approach with **fast-forward merges**.

1. Fork the repository
2. Clone your fork:

   ```bash
   git clone https://github.com/<your-username>/stonks-cli.git -b main
   cd stonks-cli
   ```

3. Create a feature branch:

   ```bash
   git checkout -b feature/my-change
   ```

4. Make changes + add tests
5. Run all checks:

   ```bash
   poetry run bash ./scripts/ci-check
   ```

6. Commit and push to your fork:

   ```bash
   git add .
   git commit -s -m "feat: your descriptive commit message"
   git push -u origin feature/my-change
   ```

7. Open a Pull Request on GitHub

### PR guidelines

- Keep PRs **focused** (avoid mixing refactors with unrelated functional changes).
- Ensure CI passes.
- Add a clear PR description explaining **what** and **why**.

---

## Commit message style

This project uses the **Conventional Commits** specification:
<https://www.conventionalcommits.org/en/v1.0.0/>

Format:

```text
<type>(optional-scope): short summary

optional body
```

Common types:

- `feat` -- new feature
- `fix` -- bug fix
- `docs` -- documentation changes
- `test` -- adding/updating tests
- `refactor` -- internal refactoring
- `chore` -- tooling/meta changes
- `ci` -- CI-related changes (GitHub Actions)

Examples:

```bash
$ git log --oneline
5cbb7bc feat: add support for multiple portfolios
7d7beb8 feat: make total portfolio currency configurable via YAML
3869d4a feat: add cash position support (add-cash / remove-cash commands)
dd892dc refactor: rename show command to dashboard
9256446 test: stub price refresh worker to prevent flaky live-network failures
182a7ef fix: guard against worker callback firing after app teardown
```

### Signed-off-by

All commits must carry a `Signed-off-by` trailer.  It is your attestation
that you wrote the change and have the right to submit it under the project's
license (see the [Developer Certificate of Origin](https://developercertificate.org/)).

Add it automatically with the `-s` flag:

```bash
git commit -s -m "feat: your descriptive commit message"
```

This appends a line like the following to the commit body:

```text
Signed-off-by: Your Name <you@example.com>
```

Git reads your name and e-mail from `user.name` / `user.email` in your
git config, so make sure those are set correctly before you start.

### Squashing fix commits

To keep the commit history clean and easier to follow, please squash fix
commits into the original commits they relate to, instead of adding separate
"fix" commits.

Having standalone fix commits makes the history harder to read and review
later.  When each commit is logically complete (i.e. it compiles, passes
tests, and includes any follow-up fixes), it:

- Makes `git blame` more meaningful
- Keeps the history easier to understand
- Simplifies potential reverts
- Maintains a clean and intentional commit narrative

Use an interactive rebase to squash the fixes into the relevant commits:

```bash
git rebase -i <your-feature-branch>
```

---

## Questions / ideas

If you're unsure about an implementation approach or want to propose a bigger
change, open an issue first so we can discuss direction before you invest time.
