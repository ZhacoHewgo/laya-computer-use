"""Run the inspector, MLX planner and a dedicated test browser with no cloud API."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def wait_ready(url, child, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Service exited with code {child.returncode}; check artifacts/local-demo/")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.2)
    raise TimeoutError(f"Service did not become ready: {url}")


def main():
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--planner-port", type=int, default=8771)
    parser.add_argument("--browser-port", type=int, default=9334)
    parser.add_argument("--planner", default="mlx-community/Qwen3-1.7B-4bit")
    parser.add_argument("--laya", default="aac6fef/laya-multilingual-mlx")
    args = parser.parse_args()
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.is_file():
        parser.error("Google Chrome is required at /Applications/Google Chrome.app")
    ports = [args.port, args.planner_port, args.browser_port]
    if len(set(ports)) != 3 or any(not 1024 <= port <= 65535 for port in ports):
        parser.error("Choose three distinct ports between 1024 and 65535")
    for port in ports:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                parser.error(f"Port {port} is already in use; choose another port")
    root = Path(__file__).resolve().parents[1]
    logs = root / "artifacts" / "local-demo"
    logs.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "DECISION_MODEL": "laya",
        "LAYA_MODEL": args.laya,
        "TEXT_MODEL_BASE_URL": f"http://127.0.0.1:{args.planner_port}/v1",
        "TEXT_MODEL": args.planner,
        "TEXT_MODEL_API_KEY": "",
        "TEXT_MODEL_REASONING": "none",
        "TYPESAFE_DEMO_PORT": str(args.port),
        "BU_NAME": f"laya-local-{args.port}",
        "BU_CDP_URL": f"http://127.0.0.1:{args.browser_port}",
        "BH_TELEMETRY": "0",
    }
    # A stale shell override must not point the dedicated harness at another browser.
    env.pop("BU_CDP_WS", None)
    env.pop("BU_BROWSER_ID", None)
    processes, handles = [], []

    def start(name, command):
        handle = (logs / f"{name}.log").open("w")
        handles.append(handle)
        child = subprocess.Popen(command, cwd=root, env=env, stdout=handle, stderr=subprocess.STDOUT)
        processes.append(child)
        return child

    try:
        browser = start("chrome", [str(chrome), "--headless=new", "--no-first-run", "--no-default-browser-check",
                                    f"--remote-debugging-port={args.browser_port}",
                                    f"--user-data-dir={logs / ('chrome-' + str(args.browser_port))}", "about:blank"])
        wait_ready(f"http://127.0.0.1:{args.browser_port}/json/version", browser)
        planner = start("planner", [sys.executable, "-m", "mlx_lm.server", "--model", args.planner,
                                    "--host", "127.0.0.1", "--port", str(args.planner_port),
                                    "--chat-template-args", '{"enable_thinking":false}'])
        wait_ready(f"http://127.0.0.1:{args.planner_port}/v1/models", planner)
        app = start("inspector", [sys.executable, "-u", "-m", "laya_ultrafast.demo"])
        wait_ready(f"http://127.0.0.1:{args.port}/api/state", app)
        print(f"Local demo ready: http://127.0.0.1:{args.port}", flush=True)
        print("Models run locally. Press Ctrl+C to stop the three services.", flush=True)
        while all(child.poll() is None for child in processes):
            time.sleep(1)
        raise RuntimeError("A service stopped; check artifacts/local-demo/")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
