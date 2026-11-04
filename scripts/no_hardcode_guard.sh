#!/usr/bin/env bash
# Fails if identifiers from the sample packet appear in pipeline or UI code.
# Packet specifics belong only in data/ and evals/gold/.
set -euo pipefail

cd "$(dirname "$0")/.."

PATTERNS=(
  -e 'cobalt[ _-]*mesa'
  -e 'sandhu' -e 'whitfield' -e 'volkov' -e 'morales' -e 'osei' -e 'brandt'
  -e 'bayline' -e 'granite[ _-]*peak' -e 'lakeshore' -e 'okafor'
  -e '9481307' -e '1987405' -e '247-918305'
  -e '4V4NC9EH' -e '3HSDZAPR' -e '3AKJGLD' -e '1XPBD49X'
  -e 'GAL[0-9]{7}' -e 'GMC[0-9]{7}' -e 'GA109[0-9]{4}'
)

TARGETS=(backend/src backend/rules backend/prompts frontend/src)
existing=()
for t in "${TARGETS[@]}"; do [[ -e "$t" ]] && existing+=("$t"); done

if hits=$(grep -rniE "${PATTERNS[@]}" "${existing[@]}" 2>/dev/null); then
  echo "Packet-specific values found in pipeline code:" >&2
  echo "$hits" >&2
  exit 1
fi
echo "guard: no packet specifics in ${existing[*]}"
