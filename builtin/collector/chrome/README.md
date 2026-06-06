# Chrome Browser Collector

This collector observes Google Chrome and writes collected browser observations
to the `work_dir` assigned by the Socket.IO coordinator.

The collector uses Socket.IO only for registration, heartbeat, configuration,
pause, and resume control. It does not stream collected event payloads to the
coordinator.

## Run

Start the debug coordinator:

```bash
python3 debug/app.py --work-dir debug/work
```

Start the collector:

```bash
python3 -m builtin.collector.chrome.cli \
  --coordinator-url http://127.0.0.1:5000 \
  --event-host 127.0.0.1 \
  --event-port 47321
```

## Source Adapters

- Active-tab polling uses AppleScript on macOS to capture Chrome title and URL
  changes.
- Browser or extension events can be posted to `POST /event` on the local event
  server. The collector converts them to snake_case observations and writes JSONL
  under the assigned `work_dir`.

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
{work_dir}/
  yyyymmdd/
    artifacts/
      <time_bucket_ms>.jsonl
```
