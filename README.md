# radar

Radar is a macOS user-intention system. The future desktop app will be built
with Wails and will coordinate local collectors, processors, and actors over
Socket.IO.

For now, `desktop/` is intentionally empty. Use the lightweight Python debug
harness to exercise the runtime contract.

```bash
pip install -r requirements.txt
python3 debug/app.py --work-dir debug/work
```

By default the debug harness listens on `http://localhost:5000`.

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
