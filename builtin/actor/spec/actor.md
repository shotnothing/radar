# Actor Spec

An actor is a script-backed action package hosted by Radar desktop. It is shaped
like a Wingman external hook: one script decides whether the action is currently
available, and another script performs the action.

Opening a URL, running AppleScript, using macOS, starting a background task,
preparing a file, or drafting a message should all be represented as scripts
with structured JSON input and output. Radar desktop decides whether an
available action runs automatically or appears as a desktop option.

Radar desktop is the host runtime. It discovers actor packages, evaluates
`should_trigger` scripts, stores pending desktop options, runs `action` scripts,
collects progress/results, and exposes a small local API that scripts can call
when they need desktop, browser, macOS, or model capabilities.

## Responsibilities

- The actor package provides a manifest with metadata, trigger config, and
  script commands.
- The actor package provides a `should_trigger` script.
- The actor package provides an `action` script.
- Radar desktop polls enabled actor packages and runs `should_trigger`.
- Radar desktop creates either automatic runs or desktop options when
  `should_trigger` returns available.
- Radar desktop runs `action` after automatic activation or user selection.

Actors should not talk directly to collectors or processors.

## Actor Manifest

Field names must use snake_case.

```json
{
    "actor_id": "builtin.release_checks",
    "version": "1.0.0",
    "enabled": true,
    "title": "Run release checks",
    "description": "Check the current branch, tests, and release readiness.",
    "activation": {
        "mode": "desktop_option",
        "priority": "normal",
        "button_label": "Run checks"
    },
    "trigger": {
        "polling_interval_seconds": 10,
        "filters": {
            "app_patterns": ["Cursor", "Code"],
            "url_patterns": []
        },
        "should_trigger": {
            "command": ["python3", "should_trigger.py"],
            "timeout_seconds": 30
        }
    },
    "action": {
        "command": ["python3", "action.py"],
        "timeout_seconds": 300
    }
}
```

Radar desktop runs scripts from the actor package directory by default. Scripts
receive JSON on stdin and return JSON on stdout.

Recommended activation modes:

- `automatic`: Radar desktop runs the action script as soon as the trigger
  condition is met.
- `desktop_option`: Radar desktop shows the action as a selectable desktop UI
  option.
- `manual`: the actor is only evaluated or run by an explicit API or user
  command.

`filters` are cheap host-side preconditions. They avoid running
`should_trigger` when the current app or URL cannot match. Pattern syntax is
glob-style, where `*` matches any number of characters and `?` matches one
character.

## Runtime Environment

Radar desktop sets these environment variables for both scripts:

```text
RADAR_ACTOR_ID=builtin.release_checks
RADAR_ACTOR_DIR=/Users/example/.radar/actors/release_checks
RADAR_API_URL=http://127.0.0.1:9231
RADAR_API_TOKEN=local_ephemeral_token
```

Scripts should use `RADAR_API_URL` when they need host capabilities that are not
available from stdin, such as reading current browser page content, opening a
view, copying text to the clipboard, or reporting progress.

## Host API

Radar desktop should expose a local HTTP API to actor scripts. The first version
can stay small:

- `GET /api/context/current`: return the latest active app, window, and browser
  context.
- `POST /api/progress`: report progress for the currently running action.
- `POST /api/desktop/open`: open a URL, file, or app.
- `POST /api/clipboard/write`: copy text to the clipboard.
- `POST /api/view/open`: open a Radar desktop view with text, markdown, HTML,
  URL, or file content.
- `POST /api/browser/page_content`: read active browser page content when a
  browser bridge is connected.
- `POST /api/browser/page_action`: perform simple browser actions when a browser
  bridge is connected.

Scripts should pass the token from `RADAR_API_TOKEN` as a bearer token:

```text
Authorization: Bearer local_ephemeral_token
```

The host API is a convenience layer for scripts. The actor contract still stays
script-based: stdin JSON in, stdout JSON out, optional progress through the host
API.

## Action Definition Shape

The older `action_definition` name may be used when referring to an actor
manifest after it has been loaded and normalized by Radar desktop.

```json
{
    "actor_id": "builtin.release_checks",
    "version": "1.0.0",
    "enabled": true,
    "title": "Run release checks",
    "description": "Check the current branch, tests, and release readiness.",
    "activation": {
        "mode": "desktop_option",
        "priority": "normal",
        "button_label": "Run checks"
    },
    "trigger": {
        "polling_interval_seconds": 10,
        "filters": {
            "app_patterns": ["Cursor", "Code"],
            "url_patterns": []
        },
        "should_trigger": {
            "command": ["python3", "should_trigger.py"],
            "timeout_seconds": 30
        }
    },
    "action": {
        "command": ["python3", "action.py"],
        "timeout_seconds": 300
    },
    "created_at": "2026-06-06T10:12:05Z"
}
```

Radar desktop discovers action definitions, periodically evaluates
`trigger.should_trigger`, and creates an action request when the trigger says the
action is available.

## Should Trigger Script

`should_trigger` scripts decide whether an actor action is currently available.
Radar desktop passes JSON on stdin and expects JSON on stdout.

Input:

```json
{
    "timestamp": 1780713574000,
    "actor_id": "builtin.release_checks",
    "polling_interval_seconds": 10,
    "active_context": {
        "observed_at": 1780713573900,
        "app_name": "Cursor",
        "bundle_id": "com.todesktop.230313mzl4w4u92",
        "window_title": "radar",
        "document_path": "/Users/example/project"
    },
    "browser": {
        "connected": true,
        "active_tab": {
            "url": "https://example.com/release",
            "title": "Release Dashboard",
            "domain": "example.com"
        }
    },
    "state": {
        "last_available_at": 1780713500000,
        "last_action_at": 1780713400000,
        "custom_data": {}
    }
}
```

Output:

```json
{
    "available": true,
    "reason": "A release dashboard is open for the current project.",
    "presentation": {
        "title": "Run release checks",
        "message": "Check branch, tests, and release readiness.",
        "button_label": "Run checks"
    },
    "action_context": {
        "project_path": "/Users/example/project",
        "environment": "staging"
    },
    "state_update": {
        "custom_data": {
            "last_project_path": "/Users/example/project"
        }
    },
    "debounce_seconds": 300
}
```

Radar desktop stores `action_context` with the pending action. When the action
runs, that same context is passed to the action script. `debounce_seconds`
prevents repeated availability checks from creating duplicate desktop options or
automatic executions.

## Action Request Shape

For `automatic` actions, Radar desktop sends the action request immediately.
For `desktop_option` actions, Radar desktop first stores a pending action and
sends the request only after the user chooses it in the desktop UI.

```json
{
    "id": "uuid",
    "actor_id": "builtin.release_checks",
    "title": "Run release checks",
    "activation_mode": "desktop_option",
    "script": {
        "command": ["python3", "action.py"],
        "cwd": "/Users/example/.radar/actors/release_checks",
        "timeout_seconds": 300
    },
    "input": {
        "timestamp": 1780713580000,
        "trigger_id": "trigger_uuid",
        "user_action": {
            "action_id": "run",
            "action_label": "Run checks"
        },
        "active_context": {
            "observed_at": 1780713573900,
            "app_name": "Cursor",
            "bundle_id": "com.todesktop.230313mzl4w4u92",
            "window_title": "radar",
            "document_path": "/Users/example/project"
        },
        "browser": {
            "connected": true,
            "active_tab": {
                "url": "https://example.com/release",
                "title": "Release Dashboard",
                "domain": "example.com"
            }
        },
        "action_context": {
            "project_path": "/Users/example/project",
            "environment": "staging"
        },
        "state": {
            "last_available_at": 1780713574000,
            "last_action_at": 1780713400000,
            "custom_data": {}
        }
    }
}
```

## Script Output

```json
{
    "success": true,
    "message": "Release checks passed.",
    "state_update": {
        "custom_data": {
            "last_successful_environment": "staging"
        }
    },
    "artifacts": []
}
```

Scripts should write progress events to stdout as JSON Lines when they need
streaming progress, or call `POST /api/progress`. The final JSON object is used
as the action result.

## Runtime Events

Actor packages do not need to open Socket.IO connections. They are files in an
actor directory, and Radar desktop runs their scripts.

When Radar desktop uses a separate script-host process, that host may use these
events for observability and control:

- `actor_host:register`: register the script host identity and capabilities.
- `actor_host:heartbeat`: report script host liveness and current status.
- `actor:progress`: report running progress for an actor action request.
- `actor:result`: report prepared, completed, failed, or cancelled actor action
  status.

Radar desktop may emit these events to a separate script host:

- `coordinator:action_request`: request that the host run an actor action.
- `coordinator:actor_config`: send updated actor package config.
- `coordinator:actor_pause`: pause actor action execution.
- `coordinator:actor_resume`: resume actor action execution.

## Progress Shape

```json
{
    "id": "uuid",
    "actor_id": "builtin.release_checks",
    "request_id": "action_request_uuid",
    "status": "running",
    "created_at": "2026-06-06T10:12:30Z",
    "progress": {
        "current": 2,
        "total": 5,
        "label": "Running tests"
    },
    "output": {
        "stdout_tail": "pytest tests/...",
        "stderr_tail": ""
    }
}
```

## Result Shape

Status values should be one of:

- `prepared`
- `running`
- `completed`
- `failed`
- `cancelled`

```json
{
    "id": "uuid",
    "actor_id": "builtin.release_checks",
    "request_id": "action_request_uuid",
    "status": "completed",
    "created_at": "2026-06-06T10:13:02Z",
    "payload": {
        "summary": "Release checks passed.",
        "artifacts": []
    }
}
```
