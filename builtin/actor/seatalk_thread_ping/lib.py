from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ACTOR_DIR = Path(__file__).resolve().parent
SWIFT_HELPER = ACTOR_DIR / "seatalk_ping_helper.swift"
HELPER_BINARY = Path("/private/tmp/radar_seatalk_ping_helper")


def run_helper(command: str, *args: str, timeout: int = 20) -> dict[str, Any]:
    binary_result = ensure_helper_binary()
    if not binary_result.get("success"):
        return binary_result

    result = subprocess.run(
        [str(HELPER_BINARY), command, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {"success": False, "error": f"invalid helper JSON: {stdout}"}
    else:
        payload = {"success": False, "error": result.stderr.strip() or "helper returned no output"}

    payload.setdefault("exit_code", result.returncode)
    if result.stderr.strip():
        payload.setdefault("stderr", result.stderr.strip())
    return payload


def ensure_helper_binary() -> dict[str, Any]:
    try:
        source_mtime = SWIFT_HELPER.stat().st_mtime
        binary_mtime = HELPER_BINARY.stat().st_mtime if HELPER_BINARY.exists() else 0
    except OSError as error:
        return {"success": False, "error": str(error)}

    if HELPER_BINARY.exists() and binary_mtime >= source_mtime:
        return {"success": True}

    env = os.environ.copy()
    env.setdefault("CLANG_MODULE_CACHE_PATH", str(Path(tempfile.gettempdir()) / "radar-clang-cache"))
    try:
        result = subprocess.run(
            [
                "swiftc",
                str(SWIFT_HELPER),
                "-framework",
                "AppKit",
                "-framework",
                "ApplicationServices",
                "-framework",
                "CoreGraphics",
                "-o",
                str(HELPER_BINARY),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timed out compiling SeaTalk AX helper"}
    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr.strip() or "failed to compile SeaTalk AX helper",
            "exit_code": result.returncode,
        }
    return {"success": True}
