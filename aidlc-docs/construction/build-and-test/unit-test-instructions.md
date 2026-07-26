# Unit Test Execution

## Run Unit Tests

### 1. Execute all unit tests

```powershell
pip install -r requirements-dev.txt     # pytest + hypothesis
python -m pytest tests/ -q
```

### 2. Review results

- **Expected**: **59 passed, 0 failures**
  - 45 pre-existing (`test_specs.py`, `test_audit.py`)
  - 14 new (`test_preview_engagement.py`)
- **Test report**: stdout only — no coverage tooling is configured in this repo, and none was
  added for this feature. Coverage is therefore **not measured**; the suite is deliberately
  scoped, not comprehensive (see below).
- **Runtime**: ~2–3 seconds.

### 3. What these tests do and do not cover

Property-Based Testing is set to **Partial** for this project: PBT applies to **pure functions
only**. For the simulator feature that means `engagement()`, `_fnv1a32`, `_band` and
`_rel_time`. The four invariants tested are the ones named in the functional design:

| Invariant | Why it matters |
|---|---|
| **Determinism** | Counts must not change between builds. Includes a test that spawns a *fresh interpreter* and compares — the test that would catch a regression to `hash()`, which is salted per process. |
| **Coherence** | `comments <= likes <= views`. An incoherent mockup reads as broken. |
| **In-band** | Values stay inside their declared per-platform bands. |
| **Totality** | Never raises for any row-id string, including empty and non-ASCII. |

**Not covered by pytest**: everything in the browser — the overlay, tab routing, cloning, the
five surface builders, hover metadata, carousel rebinding, and video playback. That is
JavaScript inside a generated page, and this repo has no JS test runner. It is covered instead
by the page-contract check and the manual checklist in
[integration-test-instructions.md](integration-test-instructions.md).

### 4. If tests fail

1. Read the failing assertion — hypothesis prints the minimal falsifying input.
2. For a determinism failure, check nothing swapped `_fnv1a32` for `hash()` or introduced
   randomness/time into `engagement()`.
3. For an in-band failure, check whether `_ENGAGE_LIKES` / `_ENGAGE_VIEWS` were re-scaled
   without updating the tests that read those same constants.
4. Fix and re-run until clean. Do not adjust a test to match broken output.
