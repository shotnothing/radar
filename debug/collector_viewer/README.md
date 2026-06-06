# Radar Collector Viewer

Independent debug viewer for Radar collector JSONL output. It scans a collector
work folder recursively, parses collected events, and renders them as a
scrollable timeline with filters and raw event detail.

## Run

```bash
cd debug/collector_viewer
npm start
```

Open <http://127.0.0.1:5174>.

By default the viewer reads:

```text
../work/collectors
```

Point it at another collector work folder with:

```bash
npm start -- --data /path/to/collectors --port 5175
```

## API

- `GET /api/health`
- `GET /api/events?collector=builtin.sample&source=sample&q=debug&limit=500`

No npm dependencies are required.
