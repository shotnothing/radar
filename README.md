# radar

Radar is a macOS user-intention system. The future desktop app will be built
with the desktop runtime in `desktop/` and will coordinate local collectors,
processors, and actors over Socket.IO.

For now, `desktop/` is an early Tauri shell. Use the lightweight Python debug
harness to exercise the collector, processor, and actor runtime contract.

```bash
pip install -r requirements.txt
python3 debug/app.py
```

By default the debug harness listens on `http://localhost:5000` and stores
collector data under `RADAR_HOME`, which defaults to `~/.radar`.
Coordinator runtime state is stored under `RADAR_HOME/run`; on startup the
coordinator uses those files to recover stale managed collectors from a previous
crashed coordinator session.

The project env file is `.env`. The Makefile loads it automatically:

```bash
make install
make test-sample-collector
make test-chat-transcript-collector
make test-chat-skill-processor
make run-coordinator
```

To run the built-in sample collector against the debug harness:

```bash
python3 builtin/collector/sample/collector.py
```

It registers as `builtin.sample`, writes one spec-shaped JSONL event under the
coordinator-assigned collector work folder, sends heartbeats for the requested
duration, and then disconnects. Set `RADAR_SAMPLE_COLLECTOR_DURATION=0` to keep
it running until the coordinator stops it. If it loses the coordinator for
longer than `RADAR_COLLECTOR_ORPHAN_GRACE_SECONDS`, it exits as an orphan.

For coordinator-managed testing, run `make run-coordinator`. The debug
coordinator reads `RADAR_COLLECTOR_META` and starts the configured collector
process itself.

## Chat Transcript Collection

The first production collector should be a file-backed chat transcript collector
for Codex and Claude. It should discover transcript JSONL files, register with
the coordinator, write replayable collected events under its assigned
`work_dir`, and preserve source provenance so every downstream processor result
can trace back to the original transcript line, message ID, tool call, or
artifact.

See `builtin/collector/spec/chat_transcript_collector.md` for the Codex/Claude
collection process and `builtin/collector/spec/collected_data.md` for the shared
provenance and artifact contract.

To test the collector against fixture transcripts:

```bash
make test-chat-transcript-collector
```

The fixture test writes synthetic source transcripts under
`RADAR_HOME/test_sources/chat_transcript` and collected output under
`RADAR_HOME/collectors/chat_transcript`.

To test the transcript-to-skill processor:

```bash
make test-chat-skill-processor
```

At runtime the processor scans `RADAR_HOME/collectors`, discovers collector data
it can use, and writes the skill-like folder under `RADAR_HOME/skill`. The
fixture test prepares data in the regular `chat_transcript` collector folder;
the processor does not own or create a processor-specific collector folder.

The processor supports the same two-tier model shape as Wingman Radar: a cheaper
filter model (`RADAR_FILTER_LLM_*`) decides whether a transcript is worth
processing, and a stronger extraction model (`RADAR_EXTRACTION_LLM_*`) distills
skill entries. Legacy `RADAR_LLM_*` values remain as fallbacks.

## Socket.IO Roles

Every module connects to the coordinator process and registers its role. During
development that coordinator is `debug/app.py`; later it will be the desktop
process.

- Collectors emit `collector:register` and `collector:heartbeat`; registration
  returns a `work_dir` where collected data is written.
- Processors emit `processor:register`, `processor:heartbeat`, and
  `processor:result`.
- Actors emit `actor:register`, `actor:heartbeat`, and `actor:result`.
- The coordinator manages module registration and work folders, then routes
  processor results to actors, debug clients, and eventually desktop UI state.

There is no separate coordinator layer outside the desktop/debug process.
