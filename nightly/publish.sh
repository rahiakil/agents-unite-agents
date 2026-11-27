#!/usr/bin/env bash
# Zero-token nightly publisher. Collects one or more US sessions with
# nightly/collect_nightly.py (no LLM) and lands each as a batch PR on the ledger.
#
#   SESSIONS="auto" | "2026-11-23 2026-11-24"   (default auto)
#   BACKFILL=auto|true|false                    (auto: true when run more than 12h after the close)
#   LIMIT=N                                     (optional, first N tickers)
#   DRY_RUN=true                                (collect and validate, no push)
#   MAX_PER_PR=N                                (split very large batches; default 600)
#   LEDGER_TOKEN, SEC_CONTACT                   (secrets)
#
# The ledger's main requires an up-to-date branch, so every batch is built on
# the latest main and refreshed with update-branch until it merges.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
R="${LEDGER_REPO:-rahiakil/agents-unite}"
SLUG="${NIGHTLY_SLUG:-rahiakil-grokbot}"
GH_USER="${NIGHTLY_GH_USER:-rahiakil}"
HASH8="$(printf '%s' "$GH_USER" | tr 'A-Z' 'a-z' | sha256sum | cut -c1-8)"
WORK="${NIGHTLY_WORK:-$(mktemp -d)}"
MAX_PER_PR="${MAX_PER_PR:-600}"
PY="${PYTHON:-python3}"
export GH_TOKEN="${LEDGER_TOKEN:-${GH_TOKEN:-}}"
log() { echo "[$(date -u '+%H:%M:%SZ')] $*"; }
STATUS=0

clone() {
  rm -rf "$WORK/ledger"
  git clone -q --depth 50 "https://x-access-token:${GH_TOKEN}@github.com/${R}.git" "$WORK/ledger" || return 1
  git -C "$WORK/ledger" config user.name "Parag Paul"
  git -C "$WORK/ledger" config user.email "parag.paul@gmail.com"
}

resolve_sessions() {
  if [[ "${SESSIONS:-auto}" != "auto" ]]; then echo "$SESSIONS"; return; fi
  # Most recent completed sessions (up to 3) with no batch from this slug on main.
  "$PY" - "$WORK/ledger" "$SLUG" <<'PY'
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, "nightly")
import collect_nightly as C
root, slug = Path(sys.argv[1]), sys.argv[2]
now = datetime.now(timezone.utc)
d, out = now.astimezone(C.NY).date(), []
while len(out) < 3:
    if C.is_trading_day(d) and C.close_dt(d) <= now:
        out.append(d)
    d -= timedelta(days=1)
todo = [x for x in out if not any((root / "data" / x.isoformat()).glob(f"*/report.{slug}.md"))]
print(" ".join(x.isoformat() for x in sorted(todo)))
PY
}

wait_merge() {  # $1 = PR url
  local url="$1" state=""
  gh pr merge -R "$R" "$url" --auto --squash >/dev/null 2>&1 || true
  for i in $(seq 1 90); do
    sleep 20
    state=$(gh pr view -R "$R" "$url" --json state,mergeStateStatus -q '.state+" "+.mergeStateStatus' 2>/dev/null)
    [[ "$state" == MERGED* || "$state" == CLOSED* ]] && break
    if [[ "$state" == *BEHIND* ]]; then gh pr update-branch -R "$R" "$url" >/dev/null 2>&1 || true; fi
    if [[ "$state" == "OPEN CLEAN" ]]; then gh pr merge -R "$R" "$url" --squash >/dev/null 2>&1 || true; fi
    if [[ "$state" == *DIRTY* ]]; then log "PR has conflicts"; break; fi
  done
  echo "$state"
}

publish_part() {  # $1 date, $2 part label, $3 file listing ticker dirs, $4 summary json
  local D="$1" PART="$2" LIST="$3" S="$4" BR="batch/${1}-${HASH8}"
  cd "$WORK/ledger"
  git fetch -q --depth 50 origin main && git checkout -q -B "$BR" origin/main
  while read -r t; do mkdir -p "data/$D/$t"; cp "$WORK/out/data/$D/$t/"*".${SLUG}."* "data/$D/$t/"; done < "$LIST"
  git add "data/$D"
  local N; N="$(git diff --cached --name-only | grep -c "report\.${SLUG}\.md" || true)"
  [[ "$N" -gt 0 ]] || { log "$D$PART nothing new"; return 0; }
  local KIND="nightly"; [[ "$("$PY" -c "import json;print(json.load(open('$S'))['backfill'])")" == "True" ]] && KIND="backfill"
  git commit -q -m "sentiment: ${KIND} $D${PART} ($N tickers, lexicon, zero-token)" \
    -m "Deterministic collector (agents-unite-agents nightly/collect_nightly.py). No LLM call. Google News RSS, Yahoo daily bars, SEC EDGAR. Generated $(date -u +%FT%TZ)."
  if [[ "${DRY_RUN:-false}" == "true" ]]; then log "dry run: would push $BR with $N reports"; return 0; fi
  git push -q -f -u origin "$BR" || { log "push failed $BR"; return 1; }
  local BODY; BODY="$("$PY" "$HERE/pr_body.py" "$S" "$N" "$PART")"
  local URL=""
  for k in 1 2 3 4 5; do
    URL=$(gh pr list -R "$R" --head "$BR" --state open --json url -q '.[0].url' 2>/dev/null)
    [[ "$URL" == https://* ]] && break
    URL=$(gh pr create -R "$R" --base main --head "$BR" --title "sentiment: ${KIND} $D${PART} ($N tickers, lexicon)" --body "$BODY" 2>&1 | tail -1)
    [[ "$URL" == https://github.com/* ]] && break
    log "pr create retry $k: $URL"; sleep 15
  done
  log "PR $URL"
  local STATE; STATE="$(wait_merge "$URL")"
  log "$D$PART: $STATE"
  echo "$URL $STATE" >> "$WORK/prs.txt"
  if [[ "$STATE" == MERGED* ]]; then
    git push -q origin --delete "$BR" 2>/dev/null || true
    return 0
  fi
  return 1
}

clone || { log "ledger clone failed"; exit 1; }
LIST_SESSIONS="$(resolve_sessions)"
[[ -n "$LIST_SESSIONS" ]] || { log "no session to collect"; exit 0; }
log "sessions: $LIST_SESSIONS"
: > "$WORK/prs.txt"
for D in $LIST_SESSIONS; do
  rm -rf "$WORK/out"; mkdir -p "$WORK/out/data"
  [[ -d "$WORK/ledger/data/$D" ]] && cp -r "$WORK/ledger/data/$D" "$WORK/out/data/" 2>/dev/null
  BF="${BACKFILL:-auto}"
  if [[ "$BF" == "auto" ]]; then
    BF=$("$PY" -c "
import sys; sys.path.insert(0,'$HERE'); import collect_nightly as C
from datetime import date, datetime, timedelta, timezone
print('true' if datetime.now(timezone.utc) - C.close_dt(date.fromisoformat('$D')) > timedelta(hours=12) else 'false')")
  fi
  ARGS=(--session "$D" --out "$WORK/out" --slug "$SLUG" --github-user "$GH_USER" --summary "$WORK/summary-$D.json")
  [[ "$BF" == "true" ]] && ARGS+=(--backfill)
  [[ -n "${LIMIT:-}" ]] && ARGS+=(--limit "$LIMIT")
  "$PY" "$HERE/collect_nightly.py" "${ARGS[@]}" | tail -45
  S="$WORK/summary-$D.json"
  [[ -f "$S" ]] || { log "$D: no summary (not a session?)"; continue; }
  # Cost row: lexicon only, zero tokens. Logged even when publishing fails.
  CARGS=(--harness nightly-script --provider lexicon --model lexicon-vader-fin-v1 --from-summary "$S")
  [[ "${DRY_RUN:-false}" == "true" ]] && CARGS+=(--dry-run)
  [[ -n "${GITHUB_RUN_ID:-}" ]] && CARGS+=(--run-id "nightly-script-$D-gh${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT:-1}")
  (cd "$ROOT" && PYTHONPATH=src "$PY" -m harness.costs log "${CARGS[@]}") || true
  cp "$S" "$ROOT/data/runs/nightly/" 2>/dev/null || { mkdir -p "$ROOT/data/runs/nightly" && cp "$S" "$ROOT/data/runs/nightly/"; }
  # New report dirs only (the collector skips tickers already on main).
  ( cd "$WORK/out" && for f in data/"$D"/*/report."$SLUG".md; do
      t="$(basename "$(dirname "$f")")"
      [[ -f "$WORK/ledger/$f" ]] || echo "$t"
    done ) | sort > "$WORK/new-$D.txt"
  TOTAL=$(wc -l < "$WORK/new-$D.txt")
  log "$D: $TOTAL new reports"
  [[ "$TOTAL" -gt 0 ]] || continue
  # Validate with the ledger's own validator before pushing (CI runs it again).
  (cd "$WORK/ledger" && mkdir -p "data/$D" && while read -r t; do mkdir -p "data/$D/$t" && cp "$WORK/out/data/$D/$t/"*".${SLUG}."* "data/$D/$t/"; done < "$WORK/new-$D.txt" \
     && "$PY" scripts/validate_report.py $(sed "s#^#data/$D/#" "$WORK/new-$D.txt") | tail -2; rc=$?; git checkout -q -- . ; git clean -qfd data/; exit $rc) \
     || { log "$D: local validation failed"; STATUS=1; continue; }
  split -l "$MAX_PER_PR" -d "$WORK/new-$D.txt" "$WORK/part-$D-"
  PARTS=( "$WORK"/part-"$D"-* ); I=0
  for P in "${PARTS[@]}"; do
    I=$((I + 1)); LABEL=""; [[ ${#PARTS[@]} -gt 1 ]] && LABEL=" part $I of ${#PARTS[@]}"
    publish_part "$D" "$LABEL" "$P" "$S" || { STATUS=1; break; }
  done
done
cat "$WORK/prs.txt"
exit "$STATUS"
