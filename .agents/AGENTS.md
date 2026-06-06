# Radar Agent Guide

Radar is a macOS user-intention system. It observes user activity across macOS and apps, turns noisy signals into normalized actions, finds repeated patterns, and offers or runs useful next actions.

This file is for agents and humans working in parallel. Keep changes scoped, respect module boundaries, and update this guide when the project shape changes.

## Product Shape

Radar has three layers:

- `desktop`: macOS UI for showing suggestions, controls, permissions, history, and feedback.
- `engine`: Python runtime that coordinates collectors, processors, actors, storage, policies, and desktop communication.
- `builtin`: first-party collectors, processors, and actors shipped with Radar.

Radar must work in two modes:

- Engine-only mode: runs as a CLI or background process without the desktop app.
- Desktop mode: runs with the macOS app and can display options, request feedback, and offer automation controls.

The desktop app is an interface and capability surface. It should not be the only place where core behavior lives.

## Current Repository Map

- `radar/desktop/`: desktop app shell and UI surfaces.
- `radar/engine/`: Python coordination process.
- `radar/builtin/collector/`: built-in collector contracts and implementations.
- `radar/builtin/processor/`: built-in processors for filtering, normalization, prediction, and learning.
- `radar/.agents/`: coordination notes for concurrent agents.

## Core Data Flow

The shared conceptual flow is:

```text
Collector -> Observation -> NormalizedAction -> PredictedNextAction
          -> UserSuggestion -> UserFeedback
```

Collectors decide when and what to capture. Processors decide what is useful, normalize raw events, infer patterns, and learn from feedback. Actors decide how to safely offer or execute a candidate action.

Use `radar/builtin/collector/spec.md` as the current source of truth for the event and prediction shapes until formal package-level schemas exist.

## Module Responsibilities

### Desktop

Desktop should:

- Display suggestions and automation choices.
- Let the user approve, dismiss, edit, pause, or disable suggestions.
- Show collector health, permission state, and recent activity summaries.
- Request macOS permissions through user-visible flows.
- Send user feedback to the engine.

Desktop should avoid:

- Owning collector logic.
- Owning prediction or learning logic.
- Executing automation without an engine policy decision.
- Treating UI state as the system of record for user preferences or learned patterns.

### Engine

Engine should:

- Own lifecycle management for collectors, processors, and actors.
- Route observations through filtering, normalization, prediction, suggestion, feedback, and storage.
- Enforce privacy, permission, retention, and automation policies.
- Provide a CLI-compatible control surface.
- Provide a desktop-compatible API or IPC surface.
- Degrade gracefully when desktop is unavailable.

Engine should avoid:

- Depending on desktop-only APIs for core behavior.
- Storing raw content unless privacy policy explicitly allows it.
- Running actor automation without a policy check and traceable reason.

### Builtin

Builtin should:

- Provide first-party collectors for macOS, browsers, chat apps, filesystem, clipboard, notifications, and other common sources.
- Provide first-party processors for filtering, redaction, normalization, pattern detection, LLM-assisted inference, and feedback learning.
- Provide first-party actors for safe user-visible suggestions and approved automations.

Builtin should avoid:

- App-specific assumptions leaking into shared schemas.
- Collector implementations directly invoking actors.
- Processor implementations bypassing engine privacy policy.

## Contracts

Prefer explicit contracts between modules.

- Collectors emit `Observation`.
- Processors produce `NormalizedAction`, `PredictionSet`, and `UserSuggestion`.
- Actors consume approved or eligible `PredictedNextAction` values and return execution status.
- Desktop sends feedback as `UserFeedback`.
- Engine owns persistence, policy checks, and routing.

When adding new fields:

- Make optional fields optional unless every existing source can produce them.
- Preserve source-specific data in `extraData` or metadata fields.
- Keep stable identifiers separate from display text.
- Include confidence and privacy metadata when transforming user data.

## Privacy And Safety Rules

Radar observes sensitive user activity. Default to local-first, minimal, and reversible behavior.

- Collect the minimum useful data.
- Prefer summaries, hashes, references, and redacted text over raw content.
- Never store passwords, authentication tokens, private keys, payment data, or recovery codes.
- Treat chat, email, clipboard, screenshots, and documents as high-risk sources.
- Keep raw-content retention short and configurable.
- Make automation opt-in unless a feature explicitly defines a safe background policy.
- Keep a visible audit trail for automated actions.
- Provide a kill switch for collection and automation.
- Design for per-source and per-app disable controls.

## Automation Policy

Actors can show options, prepare actions, or run approved actions.

Automation levels should be explicit:

- Suggest only: show a possible next action.
- Prepare: draft or stage the action but wait for user confirmation.
- Execute with confirmation: run after user approval.
- Execute in background: run only when user policy, confidence, and safety checks allow it.

Any background action should have:

- A matched pattern or clear trigger.
- A confidence score.
- A reason suitable for logs and UI.
- A permission and policy decision.
- A way for the user to undo, stop, or disable future runs when possible.

## Development Guidelines

- Keep engine APIs usable without the desktop app.
- Keep app-specific collector code behind collector boundaries.
- Keep schemas stable and versionable.
- Add tests around shared contracts, privacy policy, and routing behavior as soon as implementation lands.
- Prefer small adapters over cross-module imports.
- Avoid adding global mutable state for collector or actor registration.
- Document new collectors with required permissions, anchors, emitted observation shapes, and privacy risks.

## Concurrent Work Rules

Multiple agents may edit this repository at the same time.

- Check `git status` before editing.
- Do not overwrite unrelated changes.
- Keep changes small and tied to the requested module.
- If adding or changing a public contract, update this file or the relevant spec.
- If two modules need to communicate, define the interface first and keep implementation on the owning side.
- If a directory only has a placeholder file, preserve it unless replacing it with real content in the same directory.

## Open Decisions

These need explicit decisions before implementation hardens:

- Engine IPC protocol for desktop communication.
- Local storage format and encryption strategy.
- Schema versioning and migration strategy.
- Collector permission model and user-facing permission copy.
- LLM provider policy, local-vs-remote processing rules, and redaction boundary.
- Actor execution sandbox and rollback model.
- How learned patterns are reviewed, disabled, exported, and deleted.

## Suggested First Milestones

1. Define engine plugin interfaces for collectors, processors, and actors.
2. Define durable schema modules based on `builtin/collector/spec.md`.
3. Build engine-only ingestion and normalization loop.
4. Add a macOS active-app or active-window collector.
5. Add a processor that creates simple pattern-based suggestions.
6. Add desktop UI for suggestion display, feedback, and pause controls.
7. Add an actor that only prepares actions until approval and audit flows are ready.
