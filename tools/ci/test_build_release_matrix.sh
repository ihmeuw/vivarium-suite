#!/usr/bin/env bash
#
# Tests for build_release_matrix.sh. Side-effect-free: builds throwaway monorepo
# fixtures under a temp dir and asserts the emitted matrix JSON.
#
# Requires `jq` and an importable `vivarium_build_utils` (set PYTHONPATH to vbu's
# src/ when running outside an env where vbu is installed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="${SCRIPT_DIR}/build_release_matrix.sh"
failures=0

_make_lib() {  # <libs_root> <dir> <dist-name> [dep-dist-name ...]
    local root="$1" dir="$2" name="$3"
    shift 3
    mkdir -p "${root}/${dir}"
    {
        echo "[project]"
        echo "name = \"${name}\""
        echo "dependencies = ["
        for dep in "$@"; do echo "    \"${dep}\","; done
        echo "]"
    } >"${root}/${dir}/pyproject.toml"
}

_fixture() {  # prints the fixture monorepo root
    local tmp
    tmp="$(mktemp -d)"
    _make_lib "${tmp}/libs" a vivarium-a
    _make_lib "${tmp}/libs" b vivarium-b vivarium-a
    echo "$tmp"
}

_check() {  # <description> <expected> <actual>
    if [ "$2" = "$3" ]; then
        echo "ok   - $1"
    else
        echo "FAIL - $1"
        echo "       expected: $2"
        echo "       actual:   $3"
        failures=$((failures + 1))
    fi
}

# 1. Dependency-ordered matrix, JSON valid, library set == input set.
root="$(_fixture)"
( cd "$root" && printf 'b 2.0.0\na 1.0.0\n' >release_pairs.txt
  matrix="$(bash "$BUILD" release_pairs.txt)"
  echo "$matrix" | jq -e . >/dev/null  # valid JSON or this fails under set -e
  libs_order="$(echo "$matrix" | jq -r '.include[].library' | tr '\n' ' ')"
  versions="$(echo "$matrix" | jq -r '.include[] | "\(.library)=\(.version)"' | sort | tr '\n' ' ')"
  _check "deps ordered first (a before b)" "a b " "$libs_order"
  _check "versions mapped correctly" "a=1.0.0 b=2.0.0 " "$versions"
)
rm -rf "$root"

# 2. Empty pairs file -> empty include.
root="$(_fixture)"
( cd "$root" && : >release_pairs.txt
  _check "empty pairs -> empty matrix" '{"include":[]}' "$(bash "$BUILD" release_pairs.txt)"
)
rm -rf "$root"

# 3. Missing version -> loud failure (non-zero exit).
root="$(_fixture)"
( cd "$root" && printf 'a\n' >release_pairs.txt  # no version column
  if bash "$BUILD" release_pairs.txt >/dev/null 2>&1; then
    _check "missing version aborts" "nonzero-exit" "zero-exit"
  else
    _check "missing version aborts" "nonzero-exit" "nonzero-exit"
  fi
)
rm -rf "$root"

if [ "$failures" -ne 0 ]; then
    echo "${failures} test(s) failed"
    exit 1
fi
echo "All build_release_matrix tests passed"
