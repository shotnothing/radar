# Chrome Collector

The Chrome collector is a standalone Python process. It stores observations in
the repository collector format and exposes a local HTTP interface for health and
browser events.

## Run

```bash
python3 -m builtin.collector.chrome.cli \
  --host 127.0.0.1 \
  --port 47321 \
  --storage-root data/collectors
```

## Endpoints

- `GET /health`: engine health check.
- `POST /event`: receive one browser event object or an array of browser event
  objects.

## Output Layout

Observations are written as JSONL:

```text
data/collectors/
  chrome.extension/
    yyyymmdd/
      artifacts/
        <time-bucket>.jsonl
```

## Example Event

```json
{
  "eventName": "click",
  "documentTitle": "ChatGPT",
  "documentUrl": "https://chatgpt.com",
  "windowTitle": "ChatGPT",
  "text": "How do I deploy a Go service to Kubernetes?",
  "element": {
    "tag": "button",
    "role": "button",
    "text": "Send",
    "ariaLabel": "Send",
    "cssPath": "button[data-testid='send-button']"
  },
  "focusedElement": {
    "tag": "textarea",
    "role": "textbox",
    "value": "How do I deploy a Go service to Kubernetes?"
  }
}
```
