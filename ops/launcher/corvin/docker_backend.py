"""Docker-based Corvin backend."""
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Optional
from pathlib import Path

from . import config as cfg
from . import ollama as oll

CONSOLE_PORT = 8000
CONSOLE_PATH = "/console/"


def console_url(port: int = CONSOLE_PORT) -> str:
    return f"http://localhost:{port}{CONSOLE_PATH}"


def wait_and_open_browser(url: str, timeout: int = 30) -> None:
    """Poll url until reachable, then open the default browser. Runs in a thread."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(0.75)


_BRIDGE_ENV_KEYS = {
    "discord":  "CORVIN_BRIDGE_DISCORD",
    "telegram": "CORVIN_BRIDGE_TELEGRAM",
    "slack":    "CORVIN_BRIDGE_SLACK",
    "whatsapp": "CORVIN_BRIDGE_WHATSAPP",
    "email":    "CORVIN_BRIDGE_EMAIL",
}


def _docker() -> str:
    return "docker"


def is_docker_available() -> bool:
    try:
        subprocess.run([_docker(), "info"], capture_output=True, check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def pull_image(image: Optional[str] = None) -> bool:
    image = image or cfg.get("image")
    print(f"  Pulling {image} …")
    result = subprocess.run([_docker(), "pull", image])
    return result.returncode == 0


def is_running(container_name: Optional[str] = None) -> bool:
    name = container_name or cfg.get("container_name")
    try:
        result = subprocess.run(
            [_docker(), "ps", "-q", "--filter", f"name=^{name}$"],
            capture_output=True, text=True,
        )
        return bool(result.stdout.strip())
    except FileNotFoundError:
        return False


def stop(container_name: Optional[str] = None) -> None:
    name = container_name or cfg.get("container_name")
    subprocess.run([_docker(), "stop", name], capture_output=True)
    subprocess.run([_docker(), "rm", "-f", name], capture_output=True)


def _build_run_cmd(
    image: str,
    ollama_url: str,
    model: str,
    bridge: Optional[str],
    data_dir: str,
    container_name: str,
    console_port: int = CONSOLE_PORT,
    extra_env: Optional[dict] = None,
) -> list[str]:
    docker_ollama_url = oll.host_url_for_docker(ollama_url)

    use_host_network = (
        sys.platform == "linux"
        and ("localhost" in ollama_url or "127.0.0.1" in ollama_url)
    )

    cmd = [
        _docker(), "run", "--rm",
        "--name", container_name,
        "-e", f"CORVIN_OLLAMA_BASE_URL={docker_ollama_url}",
        "-e", f"CORVIN_HERMES_MODEL={model}",
        "-e", "CORVIN_GATEWAY_ENABLED=true",
        "-v", f"{data_dir}:/home/corvin",
    ]

    # Port mapping for the WebUI console.
    # --network host already exposes all ports on Linux; explicit -p is needed
    # on macOS / Windows Docker Desktop.
    if not use_host_network:
        cmd += ["-p", f"{console_port}:{CONSOLE_PORT}"]

    if use_host_network:
        cmd += ["--network", "host"]

    # Enable the selected bridge
    if bridge and bridge in _BRIDGE_ENV_KEYS:
        cmd += ["-e", f"{_BRIDGE_ENV_KEYS[bridge]}=true"]

    if extra_env:
        for k, v in extra_env.items():
            cmd += ["-e", f"{k}={v}"]

    cmd.append(image)
    return cmd


def start(
    foreground: bool = True,
    open_browser: bool = True,
    console_port: int = CONSOLE_PORT,
    extra_env: Optional[dict] = None,
) -> int:
    conf = cfg.load()
    image = conf["image"]
    ollama_url = conf["ollama_url"]
    model = conf["model"]
    bridge = conf.get("bridge")
    data_dir = conf["data_dir"]
    container_name = conf["container_name"]

    Path(data_dir).mkdir(parents=True, exist_ok=True)

    if is_running(container_name):
        print(f"  Corvin is already running (container: {container_name})")
        if open_browser:
            webbrowser.open(console_url(console_port))
        return 0

    cmd = _build_run_cmd(
        image, ollama_url, model, bridge, data_dir, container_name,
        console_port=console_port, extra_env=extra_env,
    )

    if not foreground:
        cmd.insert(2, "-d")

    url = console_url(console_port)
    print(f"  Starting Corvin …")
    print(f"  Console will open at {url}")

    if open_browser:
        t = threading.Thread(
            target=wait_and_open_browser, args=(url,), daemon=True
        )
        t.start()

    result = subprocess.run(cmd)
    return result.returncode
