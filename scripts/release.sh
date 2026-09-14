#!/usr/bin/env bash
# Release iDevice Battery: version, changelog, commit, tag and push in one shot.
#
#   ./scripts/release.sh 0.9.31              # bump, commit, tag, push
#   ./scripts/release.sh 0.9.31 "summary"    # same, explicit commit subject
#   ./scripts/release.sh                     # release the version already in config.yaml
#   DRY_RUN=1 ./scripts/release.sh 0.9.31    # print what would happen, touch nothing
#
# GitHub Actions then builds the images (<version> + latest) and publishes the
# release notes from the changelog section of that version.
set -euo pipefail

NAME="eMMeCodes"
EMAIL="68323817+eMMeCodes@users.noreply.github.com"
CONFIG="idevice_battery/config.yaml"
CHANGELOG="idevice_battery/CHANGELOG.md"
DRY_RUN="${DRY_RUN:-0}"

cd "$(git rev-parse --show-toplevel)"

die() {
	printf 'errore: %s\n' "$1" >&2
	exit 1
}
run() {
	if [ "$DRY_RUN" = "1" ]; then
		printf '[dry-run] %s\n' "$*"
	else
		"$@"
	fi
}

[ "$(git symbolic-ref --short HEAD)" = "main" ] || die "sei su un branch diverso da main"

# The hooks and the identity are part of the release contract.
git config core.hooksPath .githooks
git config user.name "$NAME"
git config user.email "$EMAIL"

current=$(sed -n 's/^version: *//p' "$CONFIG")
version="${1:-$current}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "versione '$version' non è X.Y.Z"

git fetch --quiet --tags origin
if git rev-parse -q --verify "refs/tags/$version" >/dev/null; then
	die "il tag $version esiste già: usa una versione nuova"
fi

# Changelog section is written by hand before releasing; the date is stamped here.
today=$(date +%F)
grep -q "^## $version " "$CHANGELOG" ||
	die "aggiungi prima la sezione '## $version — $today' in $CHANGELOG (in cima)"

top=$(sed -n 's/^## \([0-9.]*\) .*/\1/p' "$CHANGELOG" | head -1)
[ "$top" = "$version" ] || die "la sezione più in alto di $CHANGELOG è $top, non $version"

summary="${2:-$(sed -n "/^## $version /,/^## /{/^- /{s/^- //;s/\*\*//g;p;q;}}" "$CHANGELOG")}"
[ -n "$summary" ] || die "nessun sommario: passalo come secondo argomento"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

awk -v v="$version" '{ sub(/^version: .*/, "version: " v); print }' "$CONFIG" >"$tmp"
run cp "$tmp" "$CONFIG"

awk -v v="$version" -v d="$today" '{ if ($0 ~ "^## " v " ") print "## " v " — " d; else print }' "$CHANGELOG" >"$tmp"
run cp "$tmp" "$CHANGELOG"

printf '\n== %s — %s ==\n%s\n\n' "$version" "$today" "$summary"
git status --short

if [ "$DRY_RUN" = "1" ]; then
	printf '[dry-run] git add -A && git commit -m "%s: %s"\n' "$version" "$summary"
else
	git add -A
	if [ -n "$(git diff --cached --name-only)" ]; then
		git commit -m "$version: $summary"
	else
		printf 'niente da committare: taggo HEAD\n'
	fi
fi

run git tag -a "$version" -m "$version: $summary"
run git push origin main
run git push origin "refs/tags/$version"

printf '\nfatto. build e release: https://github.com/eMMeCodes/iDeviceBattery/actions\n'
printf 'release:            https://github.com/eMMeCodes/iDeviceBattery/releases/tag/%s\n' "$version"
