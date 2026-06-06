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

    def read_accessibility(self, bundle_id: str = "", mode: str = "tree", max_depth: int = 3) -> dict[str, Any]:
        current = self.read_current()["active_context"]
        if bundle_id and bundle_id not in {current.get("bundle_id"), ""}:
            current = {"bundle_id": bundle_id}

        if platform.system() != "Darwin":
            return {
                "success": False,
                "bundle_id": bundle_id or current.get("bundle_id", ""),
                "mode": mode,
                "error": "macOS accessibility is only available on Darwin",
                "data": current,
            }

        tree = self._read_macos_axtree(bundle_id or current.get("bundle_id", ""), max_depth)
        if tree.get("success"):
            return {
                "success": True,
                "bundle_id": tree.get("bundle_id") or current.get("bundle_id", ""),
                "mode": mode,
                "data": tree.get("data", {}),
            }

        return {
            "success": False,
            "bundle_id": bundle_id or current.get("bundle_id", ""),
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

    def _read_macos_axtree(self, bundle_id: str, max_depth: int) -> dict[str, Any]:
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
            return xs as text
        end esc

        on attr(el, attrName)
            try
                if attrName is "role" then return role of el as text
                if attrName is "title" then return title of el as text
                if attrName is "value" then return value of el as text
                if attrName is "description" then return description of el as text
            end try
            return ""
        end attr

        on dumpNode(el, depth, maxDepth)
            set r to my attr(el, "role")
            set t to my attr(el, "title")
            set v to my attr(el, "value")
            set desc to my attr(el, "description")
            set out to "{\"role\":\"" & my esc(r) & "\",\"title\":\"" & my esc(t) & "\",\"value\":\"" & my esc(v) & "\",\"description\":\"" & my esc(desc) & "\",\"children\":["
            if depth < maxDepth then
                try
                    set kids to UI elements of el
                    set limitCount to count of kids
                    if limitCount > 20 then set limitCount to 20
                    repeat with i from 1 to limitCount
                        if i > 1 then set out to out & ","
                        set out to out & my dumpNode(item i of kids, depth + 1, maxDepth)
                    end repeat
                end try
            end if
            return out & "]}"
        end dumpNode

        on run argv
            set targetBundle to item 1 of argv
            set maxDepth to item 2 of argv as integer
            tell application "System Events"
                set targetProc to first application process whose frontmost is true
                set appName to name of targetProc
                set bundleId to ""
                set rootJson to my dumpNode(targetProc, 0, maxDepth)
                return "{\"app_name\":\"" & my esc(appName) & "\",\"bundle_id\":\"" & my esc(bundleId) & "\",\"tree\":" & rootJson & "}"
            end tell
        end run
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script, bundle_id, str(max(max_depth, 0))],
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
            "data": data,
        }


def domain_from_url(url: str) -> str:
    if "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0]
