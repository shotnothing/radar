# radar

Radar is a macOS user-intention system. The future desktop app will be built
with Wails and will coordinate local collectors, processors, and actors over
Socket.IO.

For now, `desktop/` is intentionally empty. Use the lightweight Python debug
harness to exercise the runtime contract.

```bash
pip install -r requirements.txt
python3 debug/app.py
```

By default the debug harness listens on `http://localhost:5000` and stores
collector data under `RADAR_HOME`, which defaults to `~/.radar`.

The project env file is `.env`. The Makefile loads it automatically:

```bash
make install
make test-sample-collector
make run-coordinator
```

To run the built-in sample collector against the debug harness:

```bash
python3 builtin/collector/sample/collector.py
```

It registers as `builtin.sample`, writes one spec-shaped JSONL event under the
coordinator-assigned collector work folder, sends heartbeats for the requested
duration, and then disconnects.

For coordinator-managed testing, run `make run-coordinator`. The debug
coordinator reads `RADAR_COLLECTOR_META` and starts the configured collector
process itself.

## Socket.IO Roles

Every module connects to the coordinator process and registers its role. During
development that coordinator is `debug/app.py`; later it will be the Wails
desktop process.

- Collectors emit `collector:register` and `collector:heartbeat`; registration
  returns a `work_dir` where collected data is written.
- Processors emit `processor:register`, `processor:heartbeat`, and
  `processor:result`.
- Actors emit `actor:register`, `actor:heartbeat`, and `actor:result`.
- The coordinator manages module registration and work folders, then routes
  processor results to actors, debug clients, and eventually desktop UI state.

There is no separate coordinator layer outside the desktop/debug process.
