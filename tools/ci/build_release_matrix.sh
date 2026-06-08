#!/usr/bin/env bash
#
# Build the dependency-ordered GitHub Actions release matrix from a pairs file.
#
# Reads "<lib> <version>" lines from the pairs file (arg 1, default
# release_pairs.txt), orders the libs dependencies-first via the vbu dependency
# graph, and prints a matrix JSON object to stdout:
#
#   {"include":[{"library":"engine","version":"6.1.0"}, ...]}
#
# Ordering is computed over runtime + the ``ci_github`` extra. That extra MUST
# match the ``ENV_REQS=ci_github`` the release job installs with (release.yml
# "Install <lib>" step): the order has to reflect the dependency set the install
# actually activates, or a dependent could be ordered before a dep introduced
# only by that extra and then fail the PyPI-availability wait.
#
# Fails loudly (non-zero) on a topo cycle, a topo input/output set mismatch, a
# missing version, or malformed JSON - so problems surface here in the detect job
# rather than as an opaque failure in a later release job. Requires `jq` and an
# importable `vivarium_build_utils` (both present on the CI runner).
set -euo pipefail

PAIRS_FILE="${1:-release_pairs.txt}"

# Empty/absent pairs file: nothing to release.
if [ ! -s "$PAIRS_FILE" ]; then
    echo '{"include":[]}'
    exit 0
fi

libs=$(cut -d' ' -f1 "$PAIRS_FILE")

# Unquoted $libs is deliberate: word-split the newline-separated libs into argv
# for the topo subcommand.
# shellcheck disable=SC2086
ordered=$(python -m vivarium_build_utils.dependencies topo $libs --extra ci_github)

# Guard: topo must return exactly the input set, only reordered. If the cross-repo
# contract ever changed to add/drop libs, fail here instead of silently building a
# matrix with an extra empty-version entry or a missing release.
expected=$(printf '%s\n' "$libs" | grep -v '^$' | sort -u)
got=$(printf '%s\n' "$ordered" | grep -v '^$' | sort -u)
if [ "$expected" != "$got" ]; then
    echo "::error::topo output set does not match input set" >&2
    echo "  input:  $(echo "$expected" | tr '\n' ' ')" >&2
    echo "  output: $(echo "$got" | tr '\n' ' ')" >&2
    exit 1
fi

# Build the include array with jq so each value is quoted/escaped by construction.
include='[]'
while read -r lib; do
    [ -z "$lib" ] && continue
    version=$(awk -v target="$lib" '$1 == target { print $2; exit }' "$PAIRS_FILE")
    if [ -z "$version" ]; then
        echo "::error::no version found for '$lib' in $PAIRS_FILE" >&2
        exit 1
    fi
    include=$(jq -c --arg lib "$lib" --arg version "$version" \
        '. += [{"library": $lib, "version": $version}]' <<<"$include")
done <<<"$ordered"

jq -cn --argjson include "$include" '{"include": $include}'
