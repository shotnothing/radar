the data collected

/collector_name
    /yyyymmdd
        /artifacts
            1780713574{.jsonl that auto split by time}

data collected, each map can be expanded for more fields, mostly no compulsory
```json
{
    "id": "uuid",
    "collectorId": "macos.axtree",
    "source": {
        "type": "macos",
        "app": "Google Chrome",
        "bundleId": "com.google.Chrome"
    },
    "time": {
        "observedAt": 1780713574000
    },
    "anchor": {
        "id": "anchor_456",
        "type": "user_action",
        "name": "mouse_click",
        "occurredAt": "2026-06-06T10:12:04Z",
        "target": {
            "app": "Google Chrome",
            "windowTitle": "ChatGPT",
            "elementRole": "AXButton",
            "elementTitle": "Send"
        }
    },
    "subject": {
        "kind": "window",
        "title": "ChatGPT",
        "url": "https://chatgpt.com"
    },
    "content": {
        "text": "How do I deploy a Go service to Kubernetes?"
    },
    "context": {
        "activeApp": "Google Chrome",
        "activeWindowTitle": "ChatGPT",
        "userAction": "clicked"
    },
    "artifacts": [
        {
            "id": "artifact_1",
            "kind": "screenshot",
            "uri": "vault://artifacts/artifact_1",
            "mimeType": "image/png",
            "sizeBytes": 1234567
        }
    ],
    "extraData": {
        "axtree": {
            "app": "Google Chrome",
            "windowTitle": "ChatGPT",
            "selectedText": "",
            "visibleTexts": [
                "How do I deploy a Go service to Kubernetes?",
                "Send"
            ],
            "focusedElement": {
                "role": "AXTextArea",
                "value": "How do I deploy a Go service to Kubernetes?"
            }
        }
    }
}
```