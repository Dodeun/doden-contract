#!/usr/bin/env bash
#
# Release the contract.
#
#   ./release.sh --dry-run    # say what would happen, touch nothing
#   ./release.sh              # do it
#
# A release of this repository lives in three places that have to agree, and
# nothing complains at the time when they do not:
#
#   VERSION                                    v1.0.1
#   .github/workflows/contract-check.yml       ref: v1.0.1
#   the tags                                   v1.0.1 exists, and v1 points
#                                              at the same commit
#
# The two tags do different jobs. The exact version never moves - it is the
# receipt. `v1` moves every release, and it is the address every Project
# pins (`@v1`) and the address the tier-2 audit loads itself from. Pushing
# the receipt and forgetting the address is the failure this script exists
# for: it happened on 2026-09-19, and what it looked like was the weekly
# audit dying with "can't open file audit.py" in a repository nobody was
# watching, because `v1` still pointed at a commit written before the audit
# existed.
#
# Every check below refuses rather than repairs. A release is a published
# thing, so the moment to be difficult is before the push, not after.

set -euo pipefail

cd "$(dirname "$0")"

DRY_RUN=no
case "${1:-}" in
  --dry-run) DRY_RUN=yes ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# A real run refuses at the first problem: a release is a published thing, so
# the moment to be difficult is before the push. A dry run keeps going and
# reports every problem at once, because being told one at a time is how you
# fix three things in three rounds - and because it is the only way the later
# checks can be exercised without making a release to reach them.
PROBLEMS=0
fail() {
  echo "release: $*" >&2
  PROBLEMS=$((PROBLEMS + 1))
  [ "$DRY_RUN" = yes ] || exit 1
}
# For the few that make everything after them meaningless - no VERSION to
# read, no `gh` to ask - there is nothing to keep going with.
stop() { echo "release: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

# The commit a tag on the remote resolves to.
#
# An annotated tag comes back as two lines - `refs/tags/v1` carrying the sha
# of the *tag object*, and `refs/tags/v1^{}` carrying the commit it points at
# - so taking the first line compares a tag object against a commit and
# reports a release that worked as a release that did not. This script only
# ever creates lightweight tags, where the question does not arise, so the
# bug would have waited for the first hand-made annotated one.
#
# Both patterns are passed on purpose, and this is the part that is not
# guessable: `git ls-remote --tags origin v1` filters the peeled line *out*,
# because `v1` does not match the ref name `refs/tags/v1^{}`. Asking for the
# short name alone gets the tag object and no way to tell. Measured.
remote_commit() {
  git ls-remote --tags origin "refs/tags/$1" "refs/tags/$1^{}" |
    awk '{ if ($2 ~ /\^\{\}$/) peeled = $1; else direct = $1 }
         END { print (peeled != "" ? peeled : direct) }'
}

# ---------------------------------------------------------------- the ground

step "the ground this release stands on"

command -v gh >/dev/null 2>&1 ||
  stop "the GitHub CLI is not on PATH. This script asks GitHub whether the
       commit it is about to tag went green, because a suite passing on one
       laptop is not evidence about anything else."

branch="$(git symbolic-ref --short HEAD)"
[ "$branch" = "main" ] ||
  fail "on branch '$branch'. A release is a tag on the trunk - releasing from
       a branch tags a commit that is not what anyone will read it as."

[ -z "$(git status --porcelain)" ] ||
  fail "the working tree has changes. What gets tagged is the commit, not
       your files, so an uncommitted fix would be missing from the release
       and present on your disk - the hardest kind of difference to notice."

git fetch --quiet origin main
local_head="$(git rev-parse main)"
remote_head="$(git rev-parse origin/main)"
[ "$local_head" = "$remote_head" ] ||
  fail "main and origin/main are not the same commit. Pull or push first:
       this script trusts CI's verdict on origin/main, and that verdict is
       about the remote's commit rather than yours."

# ------------------------------------------------------------ the three files

step "the three places that have to agree"

[ -f VERSION ] || stop "no VERSION file"
version="$(tr -d '[:space:]' < VERSION)"

case "$version" in
  v[0-9]*.[0-9]*.[0-9]*) ;;
  *) stop "VERSION reads '$version', which is not a vMAJOR.MINOR.PATCH tag" ;;
esac

major="${version%%.*}"
echo "  VERSION                $version"
echo "  major tag to move      $major"

workflow=".github/workflows/contract-check.yml"
grep -q "ref: $version\$" "$workflow" ||
  fail "$workflow does not pin '$version'. It reads:
       $(grep -n 'ref: v' "$workflow" | sed 's/^/         /')
       The reusable workflow pins the exact checker it runs, so a Project
       calling @$version must get the $version checker and not whatever
       $major happens to point at today. This is the line that is easy to
       forget and expensive to get wrong."
echo "  $workflow"
echo "                         ref: $version"

document_version="$(sed -n 's/^Contract version: `\(v[0-9]*\)`.*/\1/p' CONTRACT.md | head -1)"
[ -n "$document_version" ] ||
  fail "CONTRACT.md has no 'Contract version:' line to read"
[ "$document_version" = "$major" ] ||
  fail "CONTRACT.md says it is '$document_version' and this release is
       '$major'. Every Project carries a copy of that file and the tier-1
       check compares it against the checker, so releasing this pair would
       fail every Project at once."
echo "  CONTRACT.md            $document_version"

# ------------------------------------------------------------------- the tags

step "the tags"

if git rev-parse -q --verify "refs/tags/$version" >/dev/null ||
   git ls-remote --exit-code --tags origin "$version" >/dev/null 2>&1; then
  fail "$version already exists. Either this release has been made - in which
       case bump VERSION - or a previous run failed halfway, in which case
       delete the tag deliberately and rerun rather than having this script
       guess which."
else
  echo "  $version does not exist yet, here or on the remote"
fi

current_major="$(remote_commit "$major")"
if [ -n "$current_major" ]; then
  echo "  $major currently points at ${current_major:0:7}, and will move to ${local_head:0:7}"
else
  echo "  $major does not exist yet, and will be created at ${local_head:0:7}"
fi

# ---------------------------------------------------------------- CI's verdict

step "what CI said about ${local_head:0:7}"

repository="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" ||
  stop "gh could not say which repository this is. It needs a GitHub remote
       and an authenticated gh - check \`gh auth status\`. Without CI's
       verdict this script has nothing to check the commit against, and it
       would rather stop than release on the strength of nothing."

checks="$(gh api "repos/$repository/commits/$local_head/check-runs" \
  --jq '.check_runs[] | "\(.name)\t\(.status)\t\(.conclusion // "-")"')" ||
  stop "gh could not read the check runs for ${local_head:0:7} in $repository."

if [ -z "$checks" ]; then
  fail "no check run has reported on ${local_head:0:7}. Either CI has not
       started yet, or this commit reached main without running - and a tag
       is the one thing that should never point at an untested commit."
fi

[ -z "$checks" ] || echo "$checks" | sed 's/^/  /'

# `skipped` is deliberately accepted and `neutral` is not: a job with an `if:`
# that did not apply has said something, and a neutral conclusion has not.
while IFS=$'\t' read -r name status conclusion; do
  [ "$status" = "completed" ] ||
    fail "check '$name' is still $status. Wait for it - tagging now publishes
         a commit whose verdict nobody has."
  case "$conclusion" in
    success|skipped) ;;
    *) fail "check '$name' concluded '$conclusion'. Fix it before releasing:
            a red tag breaks every Project that pins @$major, on a morning
            when nobody touched either." ;;
  esac
done < <([ -z "$checks" ] || echo "$checks")

# ------------------------------------------------------------------- the push

step "releasing $version"

if [ "$DRY_RUN" = yes ]; then
  if [ "$PROBLEMS" -gt 0 ]; then
    echo
    echo "  --dry-run found $PROBLEMS problem(s), listed above. Nothing was changed."
    exit 1
  fi
  cat <<EOF
  --dry-run, so nothing was changed. What a real run would do:

    git tag $version
    git tag -f $major
    git push origin $version
    git push -f origin $major

  Both tags would land on ${local_head:0:7}.
EOF
  exit 0
fi

git tag "$version"
git tag -f "$major"
git push origin "$version"
# The only force in this script, and it is what makes `@$major` mean the
# current release rather than the first one. This repository deliberately has
# no `refs/tags/v*` ruleset for exactly this reason, unlike every Project.
git push --force origin "$major"

# ------------------------------------------------------------ what is actually
#                                                                      out there
step "what the remote says now"

remote_exact="$(remote_commit "$version")"
remote_major="$(remote_commit "$major")"

echo "  $version -> ${remote_exact:0:7}"
echo "  $major    -> ${remote_major:0:7}"

# Read back from the remote rather than trusting the pushes above. A push can
# fail after the local tag exists, and the difference between "released" and
# "released on my machine" is the whole of what went wrong the last time.
[ "$remote_exact" = "$local_head" ] ||
  fail "$version did not land on ${local_head:0:7}. Nothing else was verified;
       fix this before assuming the release happened."
[ "$remote_major" = "$local_head" ] ||
  fail "$major did not move to ${local_head:0:7}. This is the half that is
       easy to miss: the receipt exists and the address everything loads is
       still the old commit."

echo
echo "released: @$major and @$version are the same bytes, at ${local_head:0:7}"
