#!/usr/bin/env bash
# Fail when a class selector in frontend/src/styles/*.css is never mentioned in
# frontend/src/**/*.ts(x). Catches dead CSS left behind by removed components.
#
# Matching is a plain word search of the class name in the TS/TSX sources, so a
# class built from template parts (`pnl-${sign}`) must also appear whole
# somewhere, or be listed in scripts/css-unused.allow (one class per line).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/frontend/src"
ALLOW="$ROOT/scripts/css-unused.allow"

# Everything the TS/TSX sources contain, once, so each class lookup is a grep on one file.
CORPUS="$(mktemp)"
trap 'rm -f "$CORPUS"' EXIT
find "$SRC" \( -name '*.ts' -o -name '*.tsx' \) -print0 | xargs -0 cat > "$CORPUS"

# Class selectors: a "." followed by an identifier start, outside url()/numbers.
# Comments and declaration blocks are stripped first so "0.5s" or "1.2rem" do not match.
unused=0
while IFS= read -r cls; do
  if [[ -f "$ALLOW" ]] && grep -qxF "$cls" "$ALLOW"; then continue; fi
  if ! grep -qE "(^|[^A-Za-z0-9_-])${cls}([^A-Za-z0-9_-]|$)" "$CORPUS"; then
    echo "unused CSS class: .$cls  ($(grep -lE "\.${cls}([^A-Za-z0-9_-]|\$)" "$SRC"/styles/*.css | xargs -n1 basename | tr '\n' ' '))"
    unused=1
  fi
done < <(
  for f in "$SRC"/styles/*.css; do
    # drop comments, then keep only the selector part (text before each "{")
    perl -0777 -pe 's{/\*.*?\*/}{}gs; s{\{[^{}]*\}}{\{\}}gs; s{\{[^{}]*\}}{\{\}}gs' "$f" \
      | perl -ne 'while (/(?<![\w\d.\-])\.([A-Za-z_][\w-]*)/g) { print "$1\n" }'
  done | sort -u
)

if [[ "$unused" -ne 0 ]]; then
  echo "css-unused: dead selectors found (delete them, or list dynamic ones in scripts/css-unused.allow)" >&2
  exit 1
fi
echo "css-unused: ok"
