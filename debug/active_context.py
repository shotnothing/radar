from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from typing import Any

from builtin.collector.chrome.runtime import ChromeStateReader


def epoch_ms_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class MacOSActiveContextReader:
    """Read the frontmost macOS app/window and focused element for debug actors."""

    DELIMITER = "|||RADAR|||"

    def __init__(self, *, chrome_reader: ChromeStateReader | None = None) -> None:
        self.chrome_reader = chrome_reader or ChromeStateReader()

    def read_current(self) -> dict[str, Any]:
        observed_at = epoch_ms_now()
        context = self._read_macos_context()
        context.setdefault("observed_at", observed_at)
        context.setdefault("signals", {})

        browser = self.read_browser()
        active_tab = browser.get("active_tab")
        if active_tab:
            context["document_path"] = context.get("document_path") or active_tab.get("url", "")
            context["signals"]["url"] = active_tab.get("url", "")
            context["signals"]["browser_title"] = active_tab.get("title", "")
            context["signals"]["browser_domain"] = active_tab.get("domain", "")

        return {
            "timestamp": observed_at,
            "active_context": context,
            "browser": browser,
        }

    def read_browser(self) -> dict[str, Any]:
        tab = self.chrome_reader.read_active_tab()
        if tab is None:
            return {"connected": False}
        return {
            "connected": True,
            "active_tab": {
                "url": tab.url,
                "title": tab.title,
                "domain": domain_from_url(tab.url),
            },
        }

    def read_accessibility(
        self,
        bundle_id: str = "",
        app_name: str = "",
        mode: str = "tree",
        max_depth: int = 3,
    ) -> dict[str, Any]:
        current = self.read_current()["active_context"]
        target_bundle_id = bundle_id or ""
        target_app_name = app_name or ""
        if target_bundle_id and target_bundle_id not in {current.get("bundle_id"), ""}:
            current = {"bundle_id": target_bundle_id, "app_name": target_app_name}
        elif target_app_name and target_app_name != current.get("app_name"):
            current = {"bundle_id": target_bundle_id, "app_name": target_app_name}

        if platform.system() != "Darwin":
            return {
                "success": False,
                "bundle_id": target_bundle_id or current.get("bundle_id", ""),
                "app_name": target_app_name or current.get("app_name", ""),
                "mode": mode,
                "error": "macOS accessibility is only available on Darwin",
                "data": current,
            }

        tree = self._read_macos_axtree(
            target_bundle_id or current.get("bundle_id", ""),
            target_app_name or current.get("app_name", ""),
            max_depth,
        )
        if tree.get("success"):
            return {
                "success": True,
                "bundle_id": tree.get("bundle_id") or current.get("bundle_id", ""),
                "app_name": tree.get("app_name") or current.get("app_name", ""),
                "mode": mode,
                "data": tree.get("data", {}),
            }

        return {
            "success": False,
            "bundle_id": target_bundle_id or current.get("bundle_id", ""),
            "app_name": target_app_name or current.get("app_name", ""),
            "mode": mode,
            "error": tree.get("error", "accessibility query failed"),
            "data": current,
        }

    def _read_macos_context(self) -> dict[str, Any]:
        if platform.system() != "Darwin":
            return {
                "app_name": "",
                "bundle_id": "",
                "window_title": "",
                "document_path": "",
                "provider": "unsupported",
            }

        delimiter = self.DELIMITER
        script = f'''
        set d to "{delimiter}"
        tell application "System Events"
            set frontProc to first application process whose frontmost is true
            set appName to name of frontProc
            set bundleId to ""
            set windowTitle to ""
            try
                set windowTitle to name of front window of frontProc
            end try
            set documentPath to ""
            set focusedRole to ""
            set focusedTitle to ""
            set focusedValue to ""
            set focusedDescription to ""
            set selectedText to ""
            try
                set focusedEl to value of attribute "AXFocusedUIElement" of frontProc
                try
                    set focusedRole to value of attribute "AXRole" of focusedEl as text
                end try
                try
                    set focusedTitle to value of attribute "AXTitle" of focusedEl as text
                end try
                try
                    set focusedValue to value of attribute "AXValue" of focusedEl as text
                end try
                try
                    set focusedDescription to value of attribute "AXDescription" of focusedEl as text
                end try
            end try
            return appName & d & bundleId & d & windowTitle & d & documentPath & d & focusedRole & d & focusedTitle & d & focusedValue & d & focusedDescription & d & selectedText
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"provider": "macos_accessibility", "error": str(exc), "signals": {}}

        if result.returncode != 0:
            return {
                "provider": "macos_accessibility",
                "error": result.stderr.strip(),
                "signals": {},
            }

        parts = result.stdout.rstrip("\n").split(delimiter)
        while len(parts) < 9:
            parts.append("")

        (
            app_name,
            bundle_id,
            window_title,
            document_path,
            focused_role,
            focused_title,
            focused_value,
            focused_description,
            selected_text,
        ) = (part.strip() for part in parts[:9])

        return {
            "app_name": app_name,
            "bundle_id": bundle_id,
            "window_title": window_title,
            "document_path": document_path,
            "label": focused_title or focused_description or focused_role,
            "provider": "macos_accessibility",
            "signals": {
                "focused_role": focused_role,
                "focused_title": focused_title,
                "focused_value": focused_value,
                "focused_description": focused_description,
                "selected_text": selected_text,
            },
        }

    def _read_macos_axtree(self, bundle_id: str, app_name: str, max_depth: int) -> dict[str, Any]:
        script = r'''
        on esc(s)
            set s to s as text
            set AppleScript's text item delimiters to "\\"
            set xs to text items of s
            set AppleScript's text item delimiters to "\\\\"
            set s to xs as text
            set AppleScript's text item delimiters to "\""
            set xs to text items of s
            set AppleScript's text item delimiters to "\\\""
            set s to xs as text
            set AppleScript's text item delimiters to linefeed
            set xs to text items of s
            set AppleScript's text item delimiters to "\\n"
            set s to xs as text
            set AppleScript's text item delimiters to return
            set xs to text items of s
            set AppleScript's text item delimiters to "\\r"
            set s to xs as text
            set AppleScript's text item delimiters to tab
            set xs to text items of s
            set AppleScript's text item delimiters to "\\t"
            return xs as text
        end esc

        on attr(el, attrName)
            try
                if attrName is "name" then return name of el as text
                if attrName is "role" then return role of el as text
                if attrName is "subrole" then return subrole of el as text
                if attrName is "title" then return title of el as text
                if attrName is "value" then return value of el as text
                if attrName is "description" then return description of el as text
            end try
            return ""
        end attr

        on boolAttr(el, attrName)
            try
                if attrName is "enabled" then
                    if enabled of el then return "true"
                    return "false"
                end if
            end try
            return ""
        end boolAttr

        on dumpNode(el, depth, maxDepth)
            set n to my attr(el, "name")
            set r to my attr(el, "role")
            set sr to my attr(el, "subrole")
            set t to my attr(el, "title")
            set v to my attr(el, "value")
            set desc to my attr(el, "description")
            set en to my boolAttr(el, "enabled")
            set out to "{"
            set out to out & "\"name\":\"" & my esc(n) & "\","
            set out to out & "\"role\":\"" & my esc(r) & "\","
            set out to out & "\"subrole\":\"" & my esc(sr) & "\","
            set out to out & "\"title\":\"" & my esc(t) & "\","
            set out to out & "\"value\":\"" & my esc(v) & "\","
            set out to out & "\"description\":\"" & my esc(desc) & "\","
            set out to out & "\"enabled\":\"" & my esc(en) & "\","
            set out to out & "\"children\":["
            if depth < maxDepth then
                try
                    tell application "System Events" to set kids to UI elements of el
                    set n to count of kids
                    if n > 80 then set n to 80
                    repeat with i from 1 to n
                        if i > 1 then set out to out & ","
                        set out to out & my dumpNode(item i of kids, depth + 1, maxDepth)
                    end repeat
                end try
            end if
            return out & "]}"
        end dumpNode

        on run argv
            set targetBundle to item 1 of argv
            set targetName to item 2 of argv
            set maxDepth to item 3 of argv as integer
            tell application "System Events"
                set targetProc to missing value
                if targetBundle is not "" then
                    try
                        set targetProc to first application process whose name is targetBundle
                    end try
                end if
                if targetProc is missing value and targetName is not "" then
                    try
                        set targetProc to first application process whose name is targetName
                    end try
                end if
                if targetProc is missing value then
                    set targetProc to first application process whose frontmost is true
                end if
                set appName to name of targetProc
                set bundleId to ""
                set rootJson to my dumpNode(targetProc, 0, maxDepth)
                return "{\"app_name\":\"" & my esc(appName) & "\",\"bundle_id\":\"" & my esc(bundleId) & "\",\"tree\":" & rootJson & "}"
            end tell
        end run
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script, bundle_id, app_name, str(max(max_depth, 0))],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": str(exc)}

        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"invalid accessibility JSON: {exc}"}

        return {
            "success": True,
            "bundle_id": data.get("bundle_id", bundle_id),
            "app_name": data.get("app_name", app_name),
            "data": data,
        }


def domain_from_url(url: str) -> str:
    if "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0]


def render_accessibility_tree(payload: dict[str, Any], *, max_value_length: int = 120) -> str:
    """Render the accessibility query response as compact, grep-friendly text."""

    lines: list[str] = []
    success = bool(payload.get("success"))
    app_name = payload.get("app_name") or payload.get("data", {}).get("app_name") or ""
    bundle_id = payload.get("bundle_id") or payload.get("data", {}).get("bundle_id") or ""
    status = "ok" if success else "error"
    header = f"AXTree {status}"
    if app_name:
        header += f" app={app_name}"
    if bundle_id:
        header += f" bundle={bundle_id}"
    lines.append(header)

    if not success:
        error = payload.get("error")
        if error:
            lines.append(f"error: {error}")
        return "\n".join(lines)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    tree = data.get("tree") if isinstance(data, dict) else None
    if not isinstance(tree, dict):
        lines.append("(no tree)")
        return "\n".join(lines)

    def visit(node: dict[str, Any], depth: int, index_path: str) -> None:
        role = first_non_empty(node, "role", "role_description") or "AXElement"
        label = first_non_empty(node, "title", "name", "description", "value", "identifier")
        parts = [f"{index_path} {role}"]
        if label:
            parts.append(quote_for_tree(label, max_value_length))
        identifier = node.get("identifier")
        if isinstance(identifier, str) and identifier.strip() and identifier != label:
            parts.append(f"id={quote_for_tree(identifier, max_value_length)}")
        enabled = node.get("enabled")
        if enabled in {"true", "false"}:
            parts.append(f"enabled={enabled}")
        size = node.get("size")
        if isinstance(size, dict) and size.get("width") is not None and size.get("height") is not None:
            parts.append(f"size={size.get('width')}x{size.get('height')}")
        lines.append(f"{'  ' * depth}{' '.join(parts)}")

        children = node.get("children")
        if not isinstance(children, list):
            return
        for child_index, child in enumerate(children, start=1):
            if isinstance(child, dict):
                visit(child, depth + 1, f"{index_path}.{child_index}")

    visit(tree, 0, "1")
    return "\n".join(lines)


def first_non_empty(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def quote_for_tree(value: str, max_length: int) -> str:
    compact = " ".join(value.split())
    if len(compact) > max_length:
        compact = f"{compact[: max(max_length - 1, 0)]}..."
    return json.dumps(compact, ensure_ascii=False)
