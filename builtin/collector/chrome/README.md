# Chrome Browser Collector

This collector observes Google Chrome and writes collected browser observations
to the `work_dir` assigned by the Socket.IO coordinator.

The collector uses Socket.IO only for registration, heartbeat, configuration,
pause, and resume control. It does not stream collected event payloads to the
coordinator.

## Quick Start

Use a virtual environment on macOS. Homebrew Python may reject global `pip`
installs.

```bash
cd /Users/SIPSS0578/Desktop/Hackathon
python3 -m venv /tmp/radar-venv
/tmp/radar-venv/bin/python -m pip install -r requirements.txt
```

Build the installable Chrome extension:

```bash
make PYTHON=/tmp/radar-venv/bin/python package-chrome-extension
```

This creates:

- `dist/radar-extension/`: unpacked folder for local Chrome Developer Mode.
- `dist/radar-extension.zip`: zip package for sharing or Chrome Web Store upload.

Install `dist/radar-extension/` in your normal Chrome:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select `/Users/SIPSS0578/Desktop/Hackathon/dist/radar-extension`.

Run the debug coordinator and let it launch the Chrome collector from
`meta.json`:

```bash
PATH=/tmp/radar-venv/bin:$PATH \
RADAR_COLLECTOR_META=builtin/collector/chrome/meta.json \
make PYTHON=/tmp/radar-venv/bin/python run-coordinator
```

Keep this process running while testing Chrome. The `PATH` prefix is important:
the managed collector is launched as `python3`, so this makes it use the same
virtual environment as the coordinator.

## Source Adapters

- Active-tab polling uses AppleScript on macOS to capture Chrome title and URL
  changes.
- Browser or extension events can be posted to `POST /event` on the local event
  server. The collector converts them to snake_case observations and writes JSONL
  under the assigned `work_dir`.
- The Chrome extension app lives in `extensions/radar-chrome`. It captures
  page navigation, clicks, focus, form submits, copy/paste, selection, and
  debounced input events, then forwards them to `http://127.0.0.1:47321/event`.

## Test Collection

Check that the collector event server is healthy:

```bash
curl -s http://127.0.0.1:47321/health | /tmp/radar-venv/bin/python -m json.tool
```

Open a regular `http://` or `https://` page in Chrome, then click, focus an
input, type, select text, copy, paste, or submit a form. Do not test on
`chrome://` pages; Chrome extensions do not inject content scripts there.

Input events are debounced by the extension, so wait about one second after
typing before checking output.

Find recorded Chrome data:

```bash
find debug/work/collectors/chrome_browser -type f -name '*.jsonl' -print
```

Inspect the latest events:

```bash
find debug/work/collectors/chrome_browser -type f -name '*.jsonl' -exec tail -n 3 {} \;
```

If you run the coordinator with another work directory, replace `debug/work`
with that directory. For example, `--work-dir ~/.radar` stores under
`~/.radar/collectors/chrome_browser`.

## Example Event

```json
{
  "event_name": "click",
  "document_title": "ChatGPT",
  "document_url": "https://chatgpt.com",
  "window_title": "ChatGPT",
  "text": "How do I deploy a Go service to Kubernetes?",
  "element": {
    "tag": "button",
    "role": "button",
    "text": "Send",
    "aria_label": "Send"
  },
  "focused_element": {
    "tag": "textarea",
    "role": "textbox",
    "value": "How do I deploy a Go service to Kubernetes?"
  }
}
```

## Storage

Collected data follows `builtin/collector/spec/collected_data.md`:

```text
{work_dir}/collectors/chrome_browser/
  YYYYMMDD/
    artifacts/
      <bucket_ms>.jsonl
```

With the default Makefile settings, `work_dir` is `debug/work`.

## Troubleshooting

- No JSONL files: make sure the coordinator command is still running and
  `curl http://127.0.0.1:47321/health` returns JSON.
- Extension delivery errors: open the **Radar Extension** popup and confirm the
  endpoint is `http://127.0.0.1:47321/event`.
- No page events: reload the page after installing the extension, and test on a
  regular `http://` or `https://` page.
- `ModuleNotFoundError`: install dependencies in a virtual environment and run
  the coordinator with `PATH=/tmp/radar-venv/bin:$PATH`.
- Port `47321` already in use: stop the old collector process or run the Chrome
  collector with another `--event-port` and update the extension popup endpoint.
