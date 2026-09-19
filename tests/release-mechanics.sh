#!/usr/bin/env bash
# Exercise release.sh's git mechanics against a real bare remote:
#  - remote_commit() on a lightweight tag and on an annotated one
#  - the tag / move-the-major / push sequence
#  - the read-back that decides whether the release happened
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)/release.sh"
LAB="$(mktemp -d)"
trap 'rm -rf "$LAB"' EXIT

git init -q --bare "$LAB/origin.git"
git clone -q "$LAB/origin.git" "$LAB/work"
cd "$LAB/work"
git config user.email t@example.com
git config user.name Test
echo one > f && git add -A && git commit -qm one && git push -q origin HEAD:main
git checkout -q -B main

# --- remote_commit(), the part that was wrong -------------------------------
eval "$(sed -n '/^remote_commit()/,/^}/p' "$SRC")"

head="$(git rev-parse HEAD)"
git tag light && git push -q origin light
git tag -a heavy -m annotated && git push -q origin heavy

echo "raw ls-remote for the annotated tag:"
git ls-remote --tags origin heavy | sed 's/^/    /'

light_got="$(remote_commit light)"
heavy_got="$(remote_commit heavy)"
[ "$light_got" = "$head" ] || { echo "FAIL lightweight: $light_got != $head"; exit 1; }
[ "$heavy_got" = "$head" ] || { echo "FAIL annotated:   $heavy_got != $head"; exit 1; }
echo "  lightweight resolves to the commit: ok"
echo "  annotated resolves to the commit:   ok  (the tag object sha is $(git rev-parse heavy | cut -c1-7), correctly not used)"
[ -z "$(remote_commit nothing-here)" ] || { echo "FAIL: a missing tag should be empty"; exit 1; }
echo "  a tag that does not exist is empty: ok"

# --- the release sequence ----------------------------------------------------
echo two > f && git commit -qam two && git push -q origin main
head2="$(git rev-parse HEAD)"

version=v9.0.0; major=v9
git tag "$version"
git tag -f "$major" >/dev/null
git push -q origin "$version"
git push -q --force origin "$major"
[ "$(remote_commit "$version")" = "$head2" ] || { echo "FAIL: exact tag"; exit 1; }
[ "$(remote_commit "$major")" = "$head2" ] || { echo "FAIL: major tag"; exit 1; }
echo "  first release: $version and $major both at ${head2:0:7}: ok"

# --- the second release, which is where the major has to MOVE ----------------
echo three > f && git commit -qam three && git push -q origin main
head3="$(git rev-parse HEAD)"

version=v9.0.1
git tag "$version"
git tag -f "$major" >/dev/null
git push -q origin "$version"
git push -q --force origin "$major"
[ "$(remote_commit v9.0.0)" = "$head2" ] || { echo "FAIL: the receipt moved"; exit 1; }
[ "$(remote_commit "$version")" = "$head3" ] || { echo "FAIL: exact tag"; exit 1; }
[ "$(remote_commit "$major")" = "$head3" ] || { echo "FAIL: major did not move"; exit 1; }
echo "  second release: $major moved to ${head3:0:7}, v9.0.0 still at ${head2:0:7}: ok"

# --- what a forgotten major looks like, which is the whole point -------------
echo four > f && git commit -qam four && git push -q origin main
head4="$(git rev-parse HEAD)"
git tag v9.0.2 && git push -q origin v9.0.2      # the receipt, and nothing else
if [ "$(remote_commit "$major")" = "$head4" ]; then
  echo "FAIL: the major moved without being pushed"; exit 1
fi
echo "  receipt pushed, major forgotten -> the read-back would refuse: ok"
echo
echo "all mechanics hold"
