---
title: "fix: Audit mithai docs for drift and cut release 0.8.2"
type: fix
status: active
date: 2026-06-08
depth: standard
---

# fix: Audit mithai docs for drift and cut release 0.8.2

## Summary

Audit the mithai documentation set against the current code, fix the places where docs no longer match reality, document two user-facing behaviors that shipped since `v0.8.1` but were never written up, bump the package version, and cut release `v0.8.2`. Drift detection — not prose polish — is the scope: commands, config keys, exports, and described behavior that diverge from the code get corrected. The release is gated behind a merged docs PR because the tag-triggered release workflow verifies that `pyproject.toml` matches the pushed tag.

This is a documentation-correctness change with a release tail. No application code behavior changes; the only non-doc edit is the version string in `pyproject.toml`.

**Baseline (critical).** The release baseline is `origin/master` (tip `6e3fcdc`). The working tree was on an unrelated feature branch (`fix/managed-mode-503-split`, commit `afcba4a` — a `/slack/events` 503 behavior change) when this plan was drafted; that commit is **unmerged and out of scope** for `v0.8.2` (it ships in its own PR). All doc-audit comparisons and the release branch base are relative to `origin/master`. The three behaviors that *are* on master since `v0.8.1` — stale-event surfacing, observable reflection logging, the max_tokens fix — are what this plan accounts for.

---

## Problem Frame

The docs have drifted from the code in two confirmed places, and two recently shipped behaviors are undocumented:

- **Documented commands that don't exist.** `mithai skill show` appears in `docs/getting-started.md` and `docs/skills-reference.md`, but the CLI exposes only `list`, `install`, `remove`, `upgrade`, `create`, `validate` (per `src/mithai/cli/skill_cmd.py`). A reader following the docs hits a hard "no such command" error.
- **Ambiguous example-skill references.** A `services` skill (`list_services`, `check_health`, `restart_service`) is referenced across `docs/your-first-skill.md`, `docs/testing.md`, and `docs/troubleshooting.md`. No such skill ships in `skills/`. This is *probably* legitimate tutorial scaffolding (the reader builds it in `your-first-skill.md`), but the references in `testing.md` and `troubleshooting.md` need to be checked for whether they read as "this skill exists" vs "this is the tutorial example."
- **Undocumented new behavior since `v0.8.1`.** Stale-event age surfacing to the LLM (`src/mithai/adapters/slack.py`) and observable reflection trigger/outcome logging (`src/mithai/core/reflection.py`) shipped after the last release and are not mentioned anywhere in the docs. (The `max_tokens` mid-tool_use truncation fix is already documented under `docs/solutions/` and in `docs/configuration.md` — no action needed there.)

Then: ship the corrections and release `v0.8.2`.

---

## Requirements

- **R1.** Every confirmed factual drift in the audited docs is corrected to match the current code.
- **R2.** The `services` example-skill references are reconciled — either confirmed as clearly-framed tutorial content or corrected so no doc implies a non-existent skill ships.
- **R3.** Two undocumented post-`v0.8.1` behaviors (stale-event surfacing, reflection observability logging) are documented where users/operators would look for them.
- **R4.** `pyproject.toml` version is bumped `0.8.1 → 0.8.2` in the same PR as the doc changes, because `release.yml` fails the build on a tag/`pyproject` version mismatch.
- **R5.** Tag `v0.8.2` is created and pushed **only after** the docs PR merges to `master`, triggering the release workflow exactly once.

**Success criteria:** A reader can follow any audited doc end to end without hitting a command/export/path that the code rejects; the two new behaviors are discoverable; `v0.8.2` release artifacts build and publish from a clean tag push.

---

## Key Technical Decisions

- **One PR for all doc fixes + version bump.** The version bump and doc corrections ship together so the merge that closes the PR leaves `master` in a tag-ready state. Splitting them risks tagging against a `pyproject` that doesn't match. (Rationale: `release.yml` verifies tag == `pyproject.toml` version and fails otherwise.)
- **Tag is a gated, post-merge step — not part of the PR.** Pushing `v*` is irreversible and fires the full release pipeline (binary builds for darwin-arm64, linux-amd64, linux-arm64 + GitHub release). It happens after merge, against `master` at the merge commit, per the user's confirmed auto-tag-after-merge choice.
- **Drift bar = factual, not stylistic.** Corrections target commands, config keys, exports, paths, and behavior that diverge from code. Prose tightening and completeness gaps are out of scope (see Scope Boundaries).
- **`services` skill treated as tutorial-by-default.** Unless a reference clearly asserts the skill ships, it is left as tutorial scaffolding. Only assertions of existence get corrected. This avoids gutting a working tutorial over a false-positive drift signal.

---

## Implementation Units

### U1. Fix non-existent `mithai skill show` command references

**Goal:** Remove or correct every reference to the non-existent `mithai skill show` command.
**Requirements:** R1
**Dependencies:** none
**Files:** `docs/getting-started.md`, `docs/skills-reference.md`
**Approach:** Confirm the real subcommand surface from `src/mithai/cli/skill_cmd.py` (`list`/`install`/`remove`/`upgrade`/`create`/`validate`). Replace `mithai skill show <name>` usages with the correct command — likely `mithai skill list` (or document that per-skill tool inspection isn't a CLI command if no equivalent exists). If the intent was "inspect a skill's tools" and no command provides it, remove the claim rather than inventing one.
**Patterns to follow:** Match the command-block style already used elsewhere in `getting-started.md`.
**Verification:** `grep -rn "skill show" docs/` returns no hits; every `mithai skill` invocation in the two files maps to a real subcommand in `skill_cmd.py`.

### U2. Confirm `VERIFY` documentation is accurate (no deletion)

**Goal:** Verify the existing `VERIFY` skill-export docs match the implementation. **`VERIFY` is a real, supported export — do not delete it.** This unit was originally scoped to remove `VERIFY` as drift; review against the codebase proved that wrong (`skill_loader.py:70` reads `getattr(mod, "VERIFY", False)`; `engine.py:106` and `src/mithai/core/verifier.py` consume it). The corrected task is to confirm accuracy, not to remove.
**Requirements:** R1
**Dependencies:** none
**Files:** `docs/skills-reference.md`, `docs/concepts.md`, `docs/configuration.md` (read-only verification; edit only if a specific claim is found inaccurate)
**Approach:** Read the `VERIFY` documentation in all three files against the code. Specifically check the `docs/configuration.md` claim that "built-in skills `aws` and `kubernetes` opt in by default" — confirm against the actual skill `tools.py` files. If every claim matches, this unit is a no-op confirmation. Correct only a claim that is genuinely wrong; otherwise leave the accurate docs untouched.
**Patterns to follow:** n/a — verification unit.
**Test expectation:** none — documentation-accuracy verification; no behavioral change.
**Verification:** `VERIFY` behavior described in `skills-reference.md`, `concepts.md`, and `configuration.md` matches `skill_loader.py` / `engine.py` / `verifier.py` and the actual opt-in skills; no accurate content was removed.

### U3. Reconcile `services` example-skill references

**Goal:** Ensure no doc implies a `services` skill ships, while preserving legitimate tutorial use.
**Requirements:** R2
**Dependencies:** none
**Files:** `docs/your-first-skill.md`, `docs/testing.md`, `docs/troubleshooting.md`
**Approach:** Read each reference in context. In `your-first-skill.md`, the `services` skill is almost certainly the artifact the reader builds — leave it, but verify the tutorial is internally consistent (the skill it tells you to build matches the tools it later tests). In `testing.md` and `troubleshooting.md`, decide per-reference: if the text reads as "the services skill exists / is loaded," reframe it as "the example skill from the tutorial" or swap to a real shipped skill for the example; if it's clearly illustrative, leave it. This is a judgment unit — see Open Questions.
**Patterns to follow:** Cross-link to `your-first-skill.md` when these docs lean on the tutorial example, so the reader knows where `services` comes from.
**Verification:** No audited doc states or implies `services` is a built-in/shipped skill; tutorial references are either self-evidently illustrative or explicitly linked to the tutorial.

### U4. Document stale-event surfacing and reflection observability logging

**Goal:** Document the two user/operator-facing behaviors that shipped since `v0.8.1`.
**Requirements:** R3
**Dependencies:** none
**Files:** `docs/concepts.md` and/or `docs/configuration.md` (reflection logging); `docs/concepts.md` and/or `docs/troubleshooting.md` (stale-event surfacing). Final placement decided during implementation based on where each topic already lives.
**Approach:**
- **Stale-event surfacing** (`src/mithai/adapters/slack.py`): document that the Slack adapter notes the age of an incoming event to the LLM so the model can account for stale context. Place near existing Slack-adapter or concepts content.
- **Reflection observability** (`src/mithai/core/reflection.py`): the reflection feature is already documented (`docs/concepts.md`, `docs/configuration.md` under `learning.reflection`). Add a short note that trigger decisions and outcomes are now logged, and where to observe them — extends the existing reflection docs rather than creating a new section.
**Patterns to follow:** Read the actual code for both before writing, so the documented behavior matches implementation (per the project's no-guessing rule). Mirror the existing doc voice in `concepts.md`/`configuration.md`.
**Verification:** Both behaviors are findable from the relevant doc; descriptions match the code in `slack.py` and `reflection.py`; any config keys named are real.

### U5. Bump version to 0.8.2

**Goal:** Bump the package version so the tag and code agree.
**Requirements:** R4
**Dependencies:** none (but lands in the same PR as U1–U4)
**Files:** `pyproject.toml`
**Approach:** Change `version = "0.8.1"` to `version = "0.8.2"` at `pyproject.toml:7`. This is the single source of truth — `src/mithai/` resolves version at runtime via `importlib.metadata`, and both `.spec` files use `copy_metadata('mithai')` with no hardcoded version. No other file needs the bump.
**Patterns to follow:** Prior version-bump commits.
**Test expectation:** none — single-line version metadata change; covered by U6's release-workflow version-match check.
**Verification:** `grep -rn '"0.8.1"' pyproject.toml` returns nothing; `grep -n '0.8.2' pyproject.toml` confirms the bump; no other repo file hardcodes the old version.

### U6. Ship: PR, merge, then tag and push v0.8.2

**Goal:** Land the docs PR and cut the release.
**Requirements:** R5
**Dependencies:** U1, U2, U3, U4, U5
**Files:** none (git/release operation)
**Approach:**
1. **Branch from `origin/master`, not the current working branch.** The working tree may be on an unrelated feature branch (`fix/managed-mode-503-split`); branching the docs work from it would drag `afcba4a` into the release. Start from a known-clean master: `git checkout master && git pull origin master` (expected tip `6e3fcdc` or later), then branch.
2. Push the branch, open a PR with the doc fixes + version bump. Confirm no binaries are staged (`git diff --cached --name-only`). Leave the unrelated `uv.lock` modification and the untracked `mithai-linux-aarch64.spec` out of this PR (see Scope Boundaries).
3. After review and **merge to `master`**, sync local: `git checkout master && git pull origin master`.
4. **Tag the actual master tip explicitly** — do not rely on "the merge commit" (the repo may squash-merge, in which case there is no merge commit). Capture the tip SHA, create the annotated tag on it, and verify ancestry before pushing: `git tag -a v0.8.2 <master-tip-sha>` then confirm `git merge-base --is-ancestor v0.8.2 origin/master` succeeds. `release.yml` only checks tag == `pyproject` version — it does **not** check the tag is on master — so getting the target commit right is on the operator.
5. Push the tag. This fires `release.yml`, which re-verifies tag == `pyproject` version and builds/publishes artifacts.
6. Watch the release workflow to green; confirm the GitHub release and artifacts exist.
**Execution note:** This is the irreversible, outward-facing step. Do not tag before the PR is merged. Tag exactly once — never re-run a published tag's release workflow (re-runs produce bit-different artifacts and break downstream checksums).
**Post-tag failure recovery:** If `release.yml` fails *after* the tag is pushed, branch on cause: **(a) transient infra failure** (runner outage, flaky test) with **no release assets published yet** — delete the tag locally and on the remote (`git push origin :refs/tags/v0.8.2`), then re-create and re-push the *same* `v0.8.2` on the unchanged commit; this is safe only while no downstream consumer has ingested artifacts. **(b) failure needing a code/doc fix, or assets already published** — abandon `v0.8.2`, land the fix, and cut `v0.8.3`. Check whether `gh release create` ran / assets were uploaded before choosing the branch.
**Test expectation:** none — verification is the green release workflow run, not a unit test.
**Verification:** `release.yml` run succeeds; `v0.8.2` appears in `git tag` and is an ancestor of `origin/master`; GitHub release `v0.8.2` lists the expected binaries + `checksums.txt`.

---

## Scope Boundaries

**In scope:** factual drift correction in `README.md`, `CLAUDE.md`, and the `docs/` set; documenting the two named post-`v0.8.1` behaviors; the version bump; cutting `v0.8.2`.

**Out of scope (true non-goals):**
- Prose/style rewrites, restructuring, or completeness expansion of docs that are factually correct.
- Any application code change beyond the `pyproject.toml` version string.

**Deferred to follow-up work:**
- `afcba4a` (`fix(ui): ... /slack/events 503`) — unmerged feature-branch work, not on `origin/master`. Excluded from `v0.8.2`; it carries its own PR and (if user-facing) its own doc update there.
- The untracked `mithai-linux-aarch64.spec` in the working tree — decide separately whether it belongs in the repo and the release build. Not part of this docs PR.
- The modified `uv.lock` in the working tree — unrelated; commit or discard separately.
- Per the audit, `README.md`, `CLAUDE.md`, `docs/index.md`, `docs/configuration.md`, `docs/deployment.md`, `docs/examples.md`, and `docs/security.md` showed **no drift** — no action. (`VERIFY` was also audited and found correctly documented — see U2.)

---

## Open Questions

- **U3 judgment call:** Do the `services` references in `testing.md`/`troubleshooting.md` read as "shipped skill" or "tutorial example"? Resolved during implementation by reading each in context; default is to treat as tutorial and only correct assertions of existence. Flag back if a reference is genuinely ambiguous and the fix would change tutorial flow.
- **U1 replacement command:** If `mithai skill show` was meant to inspect a skill's tools and no current command does that, confirm whether to (a) point readers at `mithai skill list`, or (b) simply remove the claim. Default: remove the non-existent command rather than invent behavior.

---

## Risks & Dependencies

- **Branching from the wrong base.** The working tree may sit on an unrelated feature branch; branching docs work from it would drag `afcba4a` into the release PR and the tag. Mitigated by U6 step 1 (branch explicitly from `origin/master`).
- **Tag pushed at the wrong commit.** `release.yml` verifies tag == `pyproject` version but never that the tag is on master; a mis-targeted tag publishes silently. Mitigated by U6 step 4 (tag the captured master-tip SHA + `merge-base --is-ancestor` check before push).
- **Tag/version mismatch fails the release.** Mitigated by U5 landing in the same PR as the docs (R4) and U6 tagging only post-merge against the matching `master`.
- **Tagging before merge** would release un-reviewed/un-merged state. Mitigated by U6's explicit ordering and execution note.
- **Re-running a published tag's workflow** produces non-deterministic artifacts and breaks downstream checksums. Mitigated by the "tag exactly once" execution note; if a fix is needed post-tag, cut `v0.8.3` instead.
- **Documenting behavior from memory** risks documenting what the code *should* do vs what it does. Mitigated by U4 requiring the implementer to read `slack.py` and `reflection.py` before writing.

---

## Sources & Research

- Drift audit (this session): `mithai skill show` non-existent (`getting-started.md:196`, `skills-reference.md:405`); `services` skill referenced but not shipped (`your-first-skill.md`, `testing.md`, `troubleshooting.md`). **Corrected during doc-review:** `VERIFY` was initially flagged as drift but proven real — `skill_loader.py:70`, `engine.py:106`, `verifier.py`; documented accurately in `skills-reference.md:222`, `concepts.md:351`, `configuration.md:280`. No `VERIFY` deletion.
- Release mechanics: `.github/workflows/release.yml` — triggers on `push.tags: v*`, verifies tag == `pyproject.toml` version (no branch/ancestry check), builds darwin-arm64 / linux-amd64 / linux-arm64 + `checksums.txt`.
- Version source of truth: `pyproject.toml:7`; runtime resolution via `importlib.metadata`; `.spec` files use `copy_metadata('mithai')` (no hardcoded version).
- Release baseline: `origin/master` tip `6e3fcdc`. Post-`v0.8.1` changes on master — stale-event note (`adapters/slack.py`), reflection logging (`core/reflection.py`), max_tokens truncation fix (`core/engine.py`, already documented). `afcba4a` (503 UI fix) is on the unmerged feature branch `fix/managed-mode-503-split`, excluded from this release.
- No external research: documentation-correctness task with strong local patterns; nothing outside the repo bears on the corrections.
