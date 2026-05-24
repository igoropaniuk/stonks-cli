"""Tests for ``stonks_cli.__init__`` version-suffix logic."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from stonks_cli import _git_dev_suffix
from stonks_cli.doctor import _version_tuple


def _set_repo_root(mock_path: MagicMock, root) -> None:
    """Wire ``mock_path`` so ``Path(__file__).resolve().parents[2]`` returns *root*."""
    mock_path.return_value.resolve.return_value.parents.__getitem__.return_value = root


def _fake_git_describe(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a mock ``subprocess.run`` return for a successful ``git describe``."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


class TestGitDevSuffix:
    @patch("stonks_cli.Path")
    def test_no_git_dir_returns_empty(self, mock_path, tmp_path):
        _set_repo_root(mock_path, tmp_path)
        # No ``.git`` dir under tmp_path -- the git probe must be skipped
        # entirely so PyPI/wheel installs never pay for subprocess.run.
        assert _git_dev_suffix("0.6.3") == ""

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_on_exact_tag_clean_returns_empty(self, mock_path, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("v0.6.3")
        assert _git_dev_suffix("0.6.3") == ""

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_on_exact_tag_dirty_returns_plus_dirty(self, mock_path, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("v0.6.3.dirty")
        assert _git_dev_suffix("0.6.3") == "+dirty"

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_past_tag_clean_returns_dev_suffix(self, mock_path, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("v0.6.3-2-gabc1234")
        assert _git_dev_suffix("0.6.3") == "+dev.2.gabc1234"

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_past_tag_dirty_returns_dev_dirty_suffix(
        self, mock_path, mock_run, tmp_path
    ):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("v0.6.3-2-gabc1234.dirty")
        assert _git_dev_suffix("0.6.3") == "+dev.2.gabc1234.dirty"

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_no_reachable_tag_returns_sha_only(self, mock_path, mock_run, tmp_path):
        # Shallow clone or pre-first-tag history: git describe --always
        # falls back to just the short SHA.
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("abc1234.dirty")
        assert _git_dev_suffix("0.6.3") == "+dev.abc1234.dirty"

    @patch("stonks_cli.subprocess.run", side_effect=FileNotFoundError("no git"))
    @patch("stonks_cli.Path")
    def test_missing_git_binary_returns_empty(self, mock_path, _run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        assert _git_dev_suffix("0.6.3") == ""

    @patch(
        "stonks_cli.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2),
    )
    @patch("stonks_cli.Path")
    def test_subprocess_timeout_returns_empty(self, mock_path, _run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        assert _git_dev_suffix("0.6.3") == ""

    @patch("stonks_cli.subprocess.run")
    @patch("stonks_cli.Path")
    def test_nonzero_returncode_returns_empty(self, mock_path, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        _set_repo_root(mock_path, tmp_path)
        mock_run.return_value = _fake_git_describe("", returncode=128)
        assert _git_dev_suffix("0.6.3") == ""


class TestVersionTupleStripsLocalSegment:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("0.6.3", (0, 6, 3)),
            ("0.6.3+dirty", (0, 6, 3)),
            ("0.6.3+dev.abc1234.dirty", (0, 6, 3)),
            ("0.6.3+dev.2.gabc1234", (0, 6, 3)),
            ("1.0.0", (1, 0, 0)),
        ],
    )
    def test_strips_local_segment(self, raw, expected):
        # Without the strip, "0.6.3+dev.abc1234.dirty" would split into
        # ["0","6","3+dev","abc1234","dirty"] and collapse to (0, 6),
        # causing doctor to falsely report the dev build as out of date
        # against PyPI's 0.6.3.
        assert _version_tuple(raw) == expected
