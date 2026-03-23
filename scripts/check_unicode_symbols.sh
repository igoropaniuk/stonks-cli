#!/usr/bin/env bash
set -euo pipefail

CHECK=0
ALL_FILES=0
VERBOSE=0

usage() {
  cat <<'EOF'
Normalize common LLM-style Unicode symbols to ASCII equivalents.

Usage:
  check_unicode_symbols.sh [--check] [--all-files] [--verbose] [files...]

Options:
  --check      Report files that would change, but do not modify them.
  --all-files  Scan tracked files in the current git repository.
  --verbose    Print each file status.
  -h, --help   Show this help.

Behavior:
  - When used by pre-commit, filenames are passed automatically.
  - In normal mode, files are rewritten in place.
  - Exit code is 1 if any file would change or was changed, else 0.

Note:
  Some replacements (checkmarks, arrows, math symbols) are semantic and
  may be inappropriate in source code. Review changes before committing.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1 ;;
    --all-files) ALL_FILES=1 ;;
    --verbose) VERBOSE=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
  shift
done

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Error: required command not found: $name" >&2
    exit 2
  fi
}

require_command git
require_command perl

is_text_file() {
  local file_path="$1"

  [[ -f "$file_path" ]] || return 1

  if command -v file >/dev/null 2>&1; then
    local mime
    mime="$(file --brief --mime -- "$file_path")"

    # Clearly text: trust it immediately.
    if [[ "$mime" == text/* ]] || [[ "$mime" == *"charset=us-ascii"* ]] || [[ "$mime" == *"charset=utf-8"* ]]; then
      return 0
    fi

    # application/* types that are text-based despite the MIME prefix.
    case "$mime" in
      application/json|application/xml|application/javascript|application/x-sh|application/x-shellscript)
        return 0
        ;;
      application/*)
        # 'file' identified a binary application format - trust it.
        return 1
        ;;
    esac

    # MIME is inconclusive (e.g. "data"); fall through to extension check.
  fi

  # Fallback: 'file' is absent or returned an inconclusive MIME type.
  case "$file_path" in
    *.md|*.txt|*.rst|*.adoc|*.json|*.jsonl|*.yaml|*.yml|*.toml|*.ini|*.cfg|*.conf|*.env|*.sh|*.bash|*.zsh|*.fish|*.py|*.js|*.ts|*.tsx|*.jsx|*.java|*.c|*.cc|*.cpp|*.h|*.hpp|*.go|*.rs|*.rb|*.php|*.swift|*.kt|*.kts|*.cs|*.sql|*.xml|*.html|*.css|*.scss|*.less|Dockerfile|*.dockerfile|*.gitignore|*.gitattributes|*.editorconfig|*.properties|*.gradle|*.mk|Makefile)
      return 0
      ;;
  esac

  # 'file' is unavailable; fall back to binary-detection heuristic.
  grep -Iq . "$file_path"
}

gather_files() {
  if [[ "$ALL_FILES" -eq 1 ]]; then
    git ls-files -z
    return
  fi

  if [[ "$#" -gt 0 ]]; then
    printf '%s\0' "$@"
  fi
}

apply_replacements() {
  local file_path="$1"
  local output_path="$2"

  perl -CSDA -pe '
    s/\x{201C}|\x{201D}/"/g;
    s/\x{2018}|\x{2019}/'\''/g;
    s/\x{2014}/--/g;
    s/\x{2013}/-/g;
    s/\x{2026}/.../g;
    s/\x{2022}/-/g;
    s/\x{00B7}/./g;
    s/\x{2023}/-/g;

    s/\x{2192}/->/g;
    s/\x{21D2}/=>/g;
    s/\x{2190}/<-/g;
    s/\x{2194}/<->/g;
    s/\x{21E8}|\x{21E2}/->/g;

    s/\x{2713}|\x{2714}|\x{2705}/[OK]/g;
    s/\x{2717}|\x{274C}/[FAIL]/g;
    s/\x{26A0}/WARNING/g;
    s/\x{2B50}/STAR/g;
    s/\x{1F539}|\x{1F538}/-/g;

    s/\x{2248}/approx/g;
    s/\x{2260}/!=/g;
    s/\x{2264}/<=/g;
    s/\x{2265}/>=/g;
    s/\x{2211}/sum/g;
    s/\x{221E}/inf/g;
    s/\x{2208}/in/g;
    s/\x{2234}/therefore/g;

    s/\x{00A0}|\x{2007}|\x{202F}|\x{2003}/ /g;
    s/\x{200B}|\x{200C}|\x{200D}|\x{2060}|\x{FEFF}//g;

    s/[\x{2502}\x{2503}]/|/g;
    s/\x{2514}|\x{250C}/+/g;
    s/\x{2500}|\x{2550}/-/g;

    s/\x{1F680}/[rocket]/g;
    s/\x{1F4CC}/[pin]/g;
    s/\x{26A1}/[zap]/g;
    s/\x{1F4A1}/[idea]/g;
  ' -- "$file_path" > "$output_path"
}

changed=0
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

process_file() {
  local file_path="$1"
  local temp_path

  [[ -f "$file_path" ]] || return 0
  is_text_file "$file_path" || return 0

  temp_path=$(mktemp "$tmpdir/tmp.XXXXXX")
  apply_replacements "$file_path" "$temp_path"

  if ! cmp -s -- "$file_path" "$temp_path"; then
    changed=1

    if [[ "$CHECK" -eq 1 ]]; then
      echo "would change: $file_path"
    else
      cat "$temp_path" > "$file_path"
      echo "normalized: $file_path"
    fi
  elif [[ "$VERBOSE" -eq 1 ]]; then
    echo "clean: $file_path"
  fi
}

if [[ "$ALL_FILES" -eq 0 && "$#" -eq 0 ]]; then
  echo "Warning: no files specified and --all-files not set; nothing to do." >&2
fi

while IFS= read -r -d '' file_path; do
  process_file "$file_path"
done < <(gather_files "$@")

exit "$changed"
