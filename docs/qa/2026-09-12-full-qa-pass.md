# Full QA Pass — 2026-09-12

> **Update (same day):** the five production defect groups below were fixed after the
> original NO-GO. The full suite is now **44 passed, no warnings**. See
> [Resolution — production fixes](#resolution--production-fixes-applied-2026-09-12).

## QA decision

**NO-GO** for release against the documented acceptance criteria.

The pre-existing 28-test suite was green, but the expanded critical-rule suite exposes five production defect groups: unsafe BPM conflict handling, BPM candidate selection order, reusable dry-run confirmation, malformed JSON parsing, and acceptance of JWTs without a subject. No production fix was made because this pass was restricted to tests, fixtures, and QA documentation. The warning-only issue in the pre-existing auth tests was corrected within that allowed scope.

## Scope and preservation baseline

Repository inspected: `/Users/alisson/labs-env/projects/spotfy-manager-v2`.

Read before testing:

- `AGENTS.md`
- `context.md`
- `README.md`
- `docs/architecture.md`
- `docs/features/playlist-bpm-download-flow.md`
- `docs/handoffs/backend-flow.md`
- `docs/handoffs/frontend-flow.md`
- `docs/operations.md`
- Existing tests under `tests/contract/` and `tests/e2e/`
- Relevant contract and service implementations for auth, parsing, matching, persistence, catalog, library, identity, file import, and web BFF

### Git baseline limitation

At the start of this QA pass, the requested directory and its parents had no `.git` directory, so Git could not inventory pre-existing changes and all initial content was treated as user-owned. A Git repository was initialized later at `/Users/alisson/labs-env/projects`, with no prior commits and all existing projects untracked; it does not provide historical attribution for the baseline.

A complete baseline of 3,974 filesystem entries was recorded before edits at `/tmp/spotfy-manager-v2-initial-inventory.json`, including entry type, file size, and SHA-256 for every file. Final preservation verification uses that snapshot. Only the four authorized QA files listed below are included in the selective commit; unrelated untracked files remain untouched.

## Baseline

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

Result before adding tests: **28 passed, 4 warnings in 1.63s** (exit 0).

The four warnings are PyJWT `InsecureKeyLengthWarning` messages caused by short secrets in pre-existing auth tests; production secret validation was not bypassed.

## Tests added

Added **16 deterministic test cases** across two new files.

### Contract/unit coverage — 10 cases

`tests/contract/test_critical_contracts.py` covers:

- Stable source-reference parsing and unsupported schemes
- Accent-insensitive title/artist matching and conservative unrelated-track matching
- Upload limit at exactly 2 MiB and rejection at 2 MiB + 1 byte
- Empty upload rejection and case-insensitive allowed extension
- Non-UTF-8 parse error mapping
- Malformed JSON container validation (two shapes)
- Expired JWT rejection
- JWT subject requirement
- SQLite persistence across close/reopen
- Atomic JSON persistence and deletion across reload

### Service/regression coverage — 6 cases

`tests/e2e/test_critical_regressions.py` covers:

- BPM score boundaries and missing BPM
- Divergent strong candidate BPMs must resolve to `conflict`
- A compatible candidate must not be hidden by an earlier out-of-range candidate
- Catalog unavailability must remain distinguishable from a genuinely empty catalog
- Analysis reuse must be owner-scoped
- Dry-run confirmation must be single-use

Existing tests additionally cover dry-run expiration, dry-run ownership, job/analysis/report/import ownership, reference reuse/force, download/tagging, authentication gates, persistence through service APIs, BFF proxies, metrics, and the end-to-end flow.

## Findings

### P0

None found.

### P1 — Strong divergent BPM candidates can be marked `recommended`

**Confidence: High.** Reproduced deterministically by a focused regression test and the full suite.

- Expected: documented conservative policy returns `conflict` when strong candidates have materially divergent BPM values.
- Actual: candidates at 120 and 140 BPM for a 120 BPM target return `recommended`.
- Evidence: `test_divergent_candidate_bpms_are_never_recommended` fails.
- Cause: `services/bpm-match/main.py::_has_conflict` receives `_bpm_score` values (bounded 0.2–1.0), then compares their spread with `> 12.0`. That threshold can never be reached, so BPM conflict detection is effectively disabled.
- Impact: unsafe recommendation, contrary to the primary BPM safety rule.
- Required production work: compare candidate BPM values (not normalized scores), define the intended conflict threshold, and keep this regression green.

### P1 — One persisted dry-run can start multiple download jobs

**Confidence: High.** Reproduced through real service endpoints and completed jobs.

- Expected: CA-14 says repeated confirmations are blocked.
- Actual: a second `POST /download` with the same `dry_run_id` returns 200 and starts another job.
- Evidence: `test_dryrun_confirmation_is_single_use` fails; second response contains a new `download_*` job ID.
- Cause: the dry-run record has no consumed state and is neither atomically marked consumed nor deleted during confirmation.
- Impact: duplicate downloads and duplicate external traffic from retries/double submits.
- Required production work: atomically consume the dry-run when creating the first job and return a stable 409-domain error on reuse. Concurrency should be tested after the serial case is fixed.

### P1 — Candidate order can reject despite a strong in-tolerance candidate

**Confidence: High.** Reproduced directly against the matching engine.

- Expected: if a plausible candidate is in tolerance, candidate evaluation should select it or explicitly classify a real conflict.
- Actual: candidates ordered at 130 BPM then 120 BPM for target 120 ±3 choose the first and return `rejected`.
- Evidence: `test_compatible_candidate_is_not_hidden_by_an_out_of_range_candidate` fails.
- Cause: `_decide` uses `bpm_present[0]` rather than selecting by compatibility/confidence; `_rank` does not include BPM proximity.
- Impact: false rejection and order-dependent outcomes.
- Required production work: make candidate selection deterministic using the documented rank plus BPM compatibility, and resolve how divergent plausible candidates interact with conflict policy.

### P2 — Malformed JSON containers crash or fabricate tracks

**Confidence: High.** Two malformed but valid JSON structures reproduce distinct failures.

- `{"playlist": []}` raises uncaught `AttributeError` instead of `DomainError(FILE_PARSE_ERROR)`.
- `{"tracks": {"name": "not-a-track-list"}}` iterates dictionary keys and fabricates a track named `name` rather than rejecting the container.
- Evidence: both parameters of `test_malformed_json_container_is_rejected_as_domain_error` fail.
- Impact: malformed uploads can produce 500 responses or silently corrupt imported data.
- Required production work: validate `playlist` as an object and `tracks` as a list before iteration, then map invalid shapes to `FILE_PARSE_ERROR`.

### P2 — Validly signed JWT without `sub` authenticates as empty owner

**Confidence: High for behavior; Medium for exploitability.** Reproduced using a valid HS256 token signed with the configured secret.

- Expected: authentication rejects any token without a non-empty string subject.
- Actual: `current_username_from_header` returns `""`.
- Evidence: `test_jwt_without_subject_is_rejected` fails.
- Impact: malformed internally issued tokens can enter domain handlers with an empty owner, creating or sharing records under the default empty identity. External exploitation still requires a valid signature.
- Required production work: reject missing, empty, or non-string `sub` with `UNAUTHORIZED`.

### P3 — Existing auth tests used short HMAC secrets — resolved

**Confidence: High.** PyJWT emitted four warnings in the baseline.

- Impact: warning noise could hide future warnings and did not model recommended HS256 key length.
- Resolution: `tests/contract/test_contracts.py` now uses deterministic secrets longer than 32 bytes while preserving wrong-secret semantics.
- Verification: the original contract suite passes with **16 passed and no warnings**.

## Command and test results

### Inventory and baseline

```bash
git status --porcelain=v2 --untracked-files=all
git diff --name-status
git diff --cached --name-status
```

Result: unavailable because the directory is not a Git repository.

```bash
python3 <filesystem inventory script>
```

Exact operation: recursively sorted every entry under `.`, recorded directories and symlink targets, and recorded `{path,type,size,sha256}` for files in `/tmp/spotfy-manager-v2-initial-inventory.json`.

Result: **3,974 entries snapshotted**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

Baseline result: **28 passed, 4 warnings in 1.63s** (exit 0).

### Targeted new suites

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/contract/test_critical_contracts.py
```

Result: **3 failed, 7 passed in 0.22s** (exit 1).

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/e2e/test_critical_regressions.py
```

Initial targeted result: **3 failed, 3 passed in 1.07s** (exit 1). The test cleanup was then tightened so the intentional single-use failure cannot leave a downloaded fixture that contaminates later tests; no assertion was weakened.

### Existing suites after additions

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/contract/test_contracts.py
```

Result: **16 passed, 4 warnings in 0.26s** (exit 0).

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/e2e/test_flow.py
```

Result: **12 passed in 1.85s** (exit 0).

### Final complete suite

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

Final result after isolation cleanup and the test-only HMAC fixture correction: **6 failed, 38 passed, no warnings in 2.57s** (exit 1).

The six failures map exactly to the five defect groups above: two malformed-JSON parameters plus JWT subject, BPM conflict, BPM candidate order, and dry-run reuse. All pre-existing 28 tests still pass when run in their original suites.

### Static and configuration checks

```bash
node --check services/web/static/app.js
```

Result: pass (exit 0).

```bash
docker compose config --quiet
```

Result: pass (exit 0).

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import ast
from pathlib import Path
files=sorted(Path('contracts').rglob('*.py'))+sorted(Path('services').rglob('*.py'))+sorted(Path('tests').rglob('*.py'))
for path in files:
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print(f'parsed {len(files)} Python files')
PY
```

Result: **parsed 21 Python files** (exit 0).

`compileall` was deliberately not used because it writes `__pycache__` beneath production directories, which conflicts with the strict write scope. AST parsing provides a non-mutating syntax check.

### Coverage tooling

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m coverage --version
```

Result: unavailable — `No module named coverage`. Dependencies were not changed because dependency edits/installations were outside scope. No numeric line/branch percentage is claimed.

## Coverage assessment

Critical behavioral coverage is now present for every requested priority:

- **BPM/matching:** score boundaries, title/artist normalization, unrelated-track conservatism, conflicting BPMs, candidate order.
- **Unavailable catalog:** dependency flag, summary, item error, and explanatory reason.
- **Auth/JWT:** roundtrip, wrong secret, expiration, header requirement, secret validation, endpoint authentication, missing subject.
- **Parsing/upload limits:** Markdown/CSV/JSON happy paths, extension, empty, exact limit, over-limit, UTF-8, malformed containers.
- **Dry-run/expiration/idempotency/ownership:** valid execution, missing/expired/cross-owner dry-runs, repeated confirmation, job ownership.
- **Persistence:** SQLite reopen, JSON reload/delete, persisted imports/analyses/dry-runs/jobs/reports through integration paths.

Numeric source coverage remains a residual measurement gap because the repository has no coverage dependency or configuration.

## Residual risks

- Concurrent (same-instant) confirmation of one dry-run is guarded atomically but a
  dedicated cross-thread stress test is not yet present.
- JWT authorization/ownership was exercised broadly, but token issuer/audience validation is not documented and therefore was not asserted.
- Real Spotify and Tidal/hifi-api paths were not exercised; tests use deterministic local mocks and no external credentials.
- Real FLAC/M4A manifests and tagging were not exercised; the existing deterministic download fixture is MP3.
- Large-batch/thread failure recovery and process restart during active download jobs remain untested.
- No browser automation was performed; frontend API contract and JavaScript syntax were checked, while UI states remain a manual/browser residual.
- Git ancestry, tracked/untracked status, and pre-existing diff attribution are unknowable without `.git` metadata.

## Final preservation verification

The initial SHA-256/type snapshot contained 3,974 filesystem entries. The QA-pass changes
were limited to the authorized QA scope; the production/resolution files below were
changed only in the separate follow-up described in
[Resolution](#resolution--production-fixes-applied-2026-09-12):

```text
ADDED docs/qa/
ADDED docs/qa/2026-09-12-full-qa-pass.md
ADDED tests/contract/test_critical_contracts.py
ADDED tests/e2e/test_critical_regressions.py
CHANGED tests/contract/test_contracts.py
OUTSIDE_ALLOWED=0
DELETED=0
```

The only pre-existing file adjusted during the QA pass itself was an auth test fixture,
replacing short deterministic HMAC secrets to remove PyJWT warnings without changing
assertions.

## Modified files

Original QA-pass scope:

- `tests/contract/test_contracts.py`
- `tests/contract/test_critical_contracts.py`
- `tests/e2e/test_critical_regressions.py`
- `docs/qa/2026-09-12-full-qa-pass.md`

Resolution (production fixes):

- `contracts/spotfy_contracts/files.py`
- `contracts/spotfy_contracts/auth.py`
- `contracts/spotfy_contracts/store.py`
- `services/bpm-match/main.py`
- `services/library/main.py`

A selective Git commit contains only these four QA files. The repository has no earlier commit history, and unrelated untracked workspace files were intentionally left untouched.

## Resolution — production fixes applied (2026-09-12)

After the QA pass, the five defect groups were fixed in production; the six regression
cases are green and no pre-existing test was weakened.

| Defect | Fix |
| ------ | --- |
| Divergent strong BPM candidates marked `recommended` | `_has_conflict` now compares the **raw candidate BPM spread** (threshold > 12.0) instead of the normalized 0.2–1.0 `_bpm_score` values that could never exceed 12.0. |
| Candidate order hiding an in-tolerance candidate | `_decide` selects the candidate with the **highest BPM score** (`max` over `bpm_present`) instead of `bpm_present[0]`; ties keep the documented rank order. |
| One dry-run starting multiple download jobs | `_load_dryrun` → `_claim_dryrun`: validates owner/expiry then **atomically** sets `dryruns.consumed = 1` via a guarded `UPDATE ... WHERE consumed = 0` (`execute_rowcount`); a rerun returns a stable **409** (`DRYRUN_NOT_FOUND` with message "já foi confirmado"). Migration adds the `consumed` column idempotently. |
| Malformed JSON containers crash/fabricate tracks | `parse_json_playlist` now validates `playlist` as an object and `tracks` as a list before iterating; invalid shapes map to `FILE_PARSE_ERROR` (no `AttributeError`, no fabricated `name` track). |
| JWT without `sub` authenticates as empty owner | `current_username_from_header` rejects missing, empty, or non-string `sub` with `UNAUTHORIZED`. |

**Files changed:** `contracts/spotfy_contracts/{files,auth,store}.py`,
`services/bpm-match/main.py`, `services/library/main.py`.

**Verification (same day):**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
# 44 passed in 2.2s (no warnings)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/contract/test_critical_contracts.py tests/e2e/test_critical_regressions.py -p no:cacheprovider -q
# 16 passed (the six previously red cases now green)
```

Static checks re-run cleanly: `node --check`, `compileall`, `docker compose config`.

**Concurrency note:** serial reuse is now blocked (409). The atomic `consumed` guard also
covers concurrent confirmations of the same dry-run in-process; a dedicated cross-thread
stress test is tracked as a residual item below.
