import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_state(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/debug/state", timeout=2) as response:
        return json.load(response)


def wait_for_state(port, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        try:
            last_state = read_state(port)
            if predicate(last_state):
                return last_state
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}; last_state={last_state}")


def wait_for_process_exit(process, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}")


def wait_for_path_missing(path, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}: {path}")


def resolve_radar_home():
    configured = os.environ.get("RADAR_HOME", "~/.radar")
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def write_runtime_state(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, indent=2, sort_keys=True)
        output_file.write("\n")


def find_jsonl(radar_home, started_at):
    files = sorted(
        path
        for path in Path(radar_home).glob("collectors/builtin_sample/**/*.jsonl")
        if path.stat().st_mtime >= started_at
    )
    if not files:
        raise RuntimeError(
            f"sample collector did not write new JSONL under RADAR_HOME={radar_home}"
        )
    return files[-1]


def validate_jsonl(path):
    with path.open(encoding="utf-8") as data_file:
        event = json.loads(data_file.readline())
    required = ["id", "collector_id", "source", "time"]
    missing = [field for field in required if field not in event]
    if missing:
        raise RuntimeError(f"sample event missing fields: {missing}")
    if event["collector_id"] != "builtin.sample":
        raise RuntimeError(f"unexpected collector_id: {event['collector_id']}")
    if "observed_at" not in event["time"]:
        raise RuntimeError("sample event missing time.observed_at")
    return event


def terminate(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main():
    port = free_port()
    radar_home = resolve_radar_home()
    started_at = time.time()
    stale_command = [sys.executable, "-c", "import time; time.sleep(60)"]
    stale_process = subprocess.Popen(stale_command)
    stale_state_path = radar_home / "run" / "collectors" / "fake_stale.json"
    sample_state_path = radar_home / "run" / "collectors" / "builtin_sample.json"
    write_runtime_state(
        stale_state_path,
        {
            "collector_id": "fake.stale",
            "command": stale_command,
            "pid": stale_process.pid,
            "session_id": "stale-test-session",
            "started_at": "2026-06-06T00:00:00Z",
        },
    )

    env = os.environ.copy()
    env["RADAR_HOME"] = str(radar_home)
    env["RADAR_HOST"] = "127.0.0.1"
    env["RADAR_PORT"] = str(port)
    env["RADAR_COORDINATOR_URL"] = f"http://127.0.0.1:{port}"
    env["RADAR_SAMPLE_COLLECTOR_DURATION"] = "3"
    env["RADAR_SAMPLE_COLLECTOR_HEARTBEAT_INTERVAL"] = "1"

    process = subprocess.Popen(
        [
            sys.executable,
            "debug/app.py",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--collector-meta",
            "builtin/collector/sample/meta.json",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    failure = None
    try:
        wait_for_state(port, lambda state: True, 10, "debug coordinator")
        wait_for_process_exit(stale_process, 10, "stale collector recovery")
        wait_for_path_missing(stale_state_path, 10, "stale collector state cleanup")
        registered = wait_for_state(
            port,
            lambda state: any(
                entry["id"] == "builtin.sample" for entry in state["collector"]
            ),
            15,
            "sample collector registration",
        )
        work_dir = registered["collector"][0]["work_dir"]
        if not sample_state_path.exists():
            raise RuntimeError(f"missing managed collector state: {sample_state_path}")
        sample_path = find_jsonl(radar_home, started_at)
        event = validate_jsonl(sample_path)
        wait_for_state(
            port,
            lambda state: not state["collector"],
            10,
            "sample collector shutdown",
        )
        wait_for_path_missing(
            sample_state_path,
            10,
            "managed collector state cleanup",
        )
        print(f"RADAR_HOME: {radar_home}")
        print(f"recovered stale collector pid: {stale_process.pid}")
        print(f"registered collector: builtin.sample")
        print(f"collector work_dir: {work_dir}")
        print(f"sample jsonl: {sample_path}")
        print(f"sample event id: {event['id']}")
    except Exception as error:
        failure = error
        raise
    finally:
        terminate(process)
        terminate(stale_process)
        output = process.stdout.read() if process.stdout else ""
        if failure is not None and output.strip():
            print(output)
        if process.returncode not in (0, -15, None):
            print(output)
            raise RuntimeError(f"debug coordinator exited with {process.returncode}")


if __name__ == "__main__":
    main()
