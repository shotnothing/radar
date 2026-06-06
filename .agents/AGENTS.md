# Radar Agent Guide

Radar is a macOS user-intention system. It observes user activity across macOS and apps, turns noisy signals into normalized actions, finds repeated patterns, and offers or runs useful next actions.

This file is for agents and humans working in parallel. Keep changes scoped, respect module boundaries, and update this guide when the project shape changes.

## Product Shape

Radar has three project areas:

- `desktop`: early Tauri macOS app shell. The desktop process will eventually
  replace the debug harness as the production coordinator.
- `debug`: lightweight Python Socket.IO harness for local development and
  manual management of collectors, processors, and actors.
- `builtin`: first-party collectors, processors, and actors shipped with Radar.

The desktop process will be the production coordinator. Until that coordinator
is ready, the debug harness owns the same local Socket.IO contract so module work
can proceed.

## Current Repository Map

- `radar/desktop/`: early Tauri desktop app.
- `radar/debug/`: Python debug harness for running the Socket.IO coordinator.
- `radar/builtin/collector/`: built-in collector contracts and implementations.
- `radar/builtin/collector/spec/`: collector contracts, event shapes, and
  Socket.IO registration expectations.
- `radar/builtin/processor/`: built-in processors for filtering, normalization, prediction, and learning.
- `radar/builtin/actor/`: built-in actors for safe user-visible suggestions and approved automations.
- `radar/.agents/`: coordination notes for concurrent agents.

## Core Data Flow

The shared conceptual flow is:

```text
Collector -> Observation -> NormalizedAction -> PredictedNextAction
          -> UserSuggestion -> UserFeedback
```

Collectors decide when and what to capture. Processors decide what is useful, normalize raw events, infer patterns, and learn from feedback. Actors decide how to safely offer or execute a candidate action.

The coordinator process hosts the Socket.IO server, assigns collectors a local
work folder during registration, and manages module lifecycle. Collectors write
their own collected data into that folder. Processors work from
coordinator-provided folder or file references and return normalized actions,
predictions, suggestions, or feedback-learning results to the coordinator.
Actors receive eligible action requests from the coordinator and return execution
status.

Use `radar/builtin/collector/spec/collected_data.md` as the current source of
truth for collected event shapes until formal package-level schemas exist. Use
`radar/builtin/collector/spec/collector.md` as the source of truth for
collector metadata and Socket.IO registration.

## Module Responsibilities

### Desktop

Desktop should eventually:

- Own lifecycle management for collectors, processors, and actors.
- Host the local Socket.IO server used by collectors, processors,
  and actors.
- Route folder and file references through filtering, normalization, prediction,
  suggestion, feedback, and storage.
- Enforce privacy, permission, retention, and automation policies.
- Display suggestions and automation choices.
- Let the user approve, dismiss, edit, pause, or disable suggestions.
- Show collector health, permission state, and recent activity summaries.
- Request macOS permissions through user-visible flows.
- Persist user feedback and route it to processors.

Desktop should avoid:

- Owning collector logic.
- Owning prediction or learning logic.
- Executing automation without a desktop policy decision.
- Treating UI state as the system of record for user preferences or learned patterns.

### Debug

Debug should:

- Provide a small Python Socket.IO coordinator for local development.
- Register and list collectors, processors, and actors.
- Assign collector work folders.
- Route folder and file references to processors.
- Route processor action requests to actors and processor results to debug
  clients.
- Stay light and disposable; production behavior belongs in the future Wails
  desktop process.

Debug should avoid:

- Becoming a production runtime.
- Owning macOS UI or permission flows.
- Adding heavy framework dependencies.

### Builtin

Builtin should:

- Provide first-party collectors for macOS, browsers, chat apps, filesystem, clipboard, notifications, and other common sources.
- Provide first-party processors for filtering, redaction, normalization, pattern detection, LLM-assisted inference, and feedback learning.
- Provide first-party actors for safe user-visible suggestions and approved automations.

Builtin should avoid:

- App-specific assumptions leaking into shared schemas.
- Collector implementations directly invoking actors.
- Processor implementations bypassing coordinator privacy policy.

## Contracts

Prefer explicit contracts between modules.

- Collectors produce `Observation` records on disk.
- Collectors include `meta.json` metadata with a stable ID, runtime command,
  permissions, capabilities, anchors, emitted shapes, and default config.
- Collectors connect to the coordinator Socket.IO server and emit
  `collector:register` and `collector:heartbeat`.
- Collector registration returns a `work_dir`; collectors write collected JSONL
  and artifacts there without streaming collected data through Socket.IO.
- Processors produce `NormalizedAction`, `PredictionSet`, and `UserSuggestion`.
- Processors connect to the coordinator Socket.IO server and emit
  `processor:register`, `processor:heartbeat`, and `processor:result`.
- Actors consume approved or eligible `PredictedNextAction` values and emit
  `actor:register`, `actor:heartbeat`, and `actor:result`.
- Desktop records feedback as `UserFeedback` and routes it to processors.
- The coordinator owns persistence, policy checks, Socket.IO routing, and
  lifecycle. Today this is `debug/`; later this is `desktop/`.

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

- Keep Socket.IO coordination APIs explicit and versionable.
- Keep app-specific collector code behind collector boundaries.
- Keep schemas stable and versionable.
- Add tests around shared contracts, privacy policy, and routing behavior as soon as implementation lands.
- Collector tests should write collected output under `RADAR_HOME`
  (default `~/.radar`) so humans and agents can inspect the same files after a
  run. Use synthetic source fixtures under `RADAR_HOME/test_sources/...` when a
  test should not read real user data.
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

- Socket.IO authentication, authorization, and transport hardening.
- Local storage format and encryption strategy.
- Schema versioning and migration strategy.
- Collector permission model and user-facing permission copy.
- LLM provider policy, local-vs-remote processing rules, and redaction boundary.
- Actor execution sandbox and rollback model.
- How learned patterns are reviewed, disabled, exported, and deleted.

## Suggested First Milestones

1. Define coordinator Socket.IO contracts for collectors, processors, and actors.
2. Define durable schema modules based on `builtin/collector/spec/`.
3. Build debug-hosted ingestion and normalization routing.
4. Add a macOS active-app or active-window collector.
5. Add a processor that creates simple pattern-based suggestions.
6. Introduce the Wails desktop shell.
7. Add an actor that only prepares actions until approval and audit flows are ready.
