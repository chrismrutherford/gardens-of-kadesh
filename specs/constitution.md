# Gardens of Kadesh Constitution
<!-- GoK project constitution, governing all spec-driven workflows on this repo -->

## Core Principles

### I. Faithfulness to Homeworld
Gardens of Kadesh is a fan restoration of the 1999 classic Homeworld. New
features (including AI-facing tooling) MUST NOT alter core gameplay semantics,
unit behavior, or the single-player campaign relative to the existing GoK
build unless the change is explicitly part of the spec under which it is
implemented. Rationale: the project's identity is preservation plus portability.

### II. Layered, Non-Invasive Integration
AI accessibility (e.g., an MCP server layer) MUST be implemented as a separate
layer that wraps or observes the existing engine — through documented headless
mode, existing serialization (BigFiles, .level data), or IPC — and MUST NOT
twist core engine code to accommodate it. Where touching engine code is
unavoidable, the touch surface MUST be minimal and justified in the spec.

### III. Test-First (NON-NEGOTIABLE)
All implementation steps MUST be accompanied by unit tests written before or
with the code, and the tests MUST run green. For anything that cannot be unit
tested (real-time simulation determinism, GPU paths), a documented manual or
scripted verification procedure MUST be recorded in the converge step.

### IV. Determinism and Reproducibility
Anything an AI player interacts with MUST behave deterministically at a given
seed and game state: the MCP layer MUST expose game state snapshots that are
reproducible, and tool calls MUST NOT rely on wall-clock timing for
gameplay-affecting behavior. Rationale: an AI agent must be able to reason
over stable state.

### V. Simplicity (YAGNI)
Start with the smallest MCP tool surface that allows an AI to observe state and
issue commands (observe, query, order). Do not build replays of features
(tournaments, ML training loops, UI) until a spec demands them. Every added
capability MUST map to a verifiable spec criterion.

## Additional Constraints

- Technology: follow the existing GoK toolchain — C/C++ built with
  meson/ninja, SDL2. The MCP layer MUST NOT introduce a second build system;
  it belongs in the existing meson build (a new target, e.g. an executable or
  subproject).
- Licensing: all new code MUST be compatible with the licenses in COPYING and
  THIRD_PARTY_NOTICES.md (GPL family where applicable). Dependencies must be
  vendored via subprojects per existing convention where feasible.
- Platform expectations: feature MUST build on Linux first; macOS/Windows/Mingw
  parity is desirable but not gated for initial convergence.
- Security: the MCP layer MUST NOT be exposed on a network socket by default;
  local IPC (stdin/stdout or local socket) only.

## Development Workflow

- Spec-driven development via this logbook's sdd workflow: constitution,
  specify, plan, tasks (no code), then implement/converge mini-Vs until every
  spec criterion passes with evidence (test files run green, verification
  report against the spec).
- Code changes and their tests land together; a step without green tests is
  not complete.
- No new engine-wide refactors ride along with a feature; unrelated cleanup
  gets its own spec or is explicitly scoped in the plan.

## Governance

- This constitution supersedes ad-hoc practices; conflicts are resolved in
  favor of this document.
- Amendments require recording a decision in the logbook (what/why/
  alternatives) and bumping the version: MAJOR for redefinition/removal of
  principles, MINOR for new principles or materially expanded guidance, PATCH
  for wording.
- Every PR/change MUST be checkable against the principles above; reviewers
  (or the agent completing a step) MUST verify compliance before completion.

**Version**: 1.0.0 | **Ratified**: 2026-10-18 | **Last Amended**: 2026-10-18