"""Open a locally deployed Minecraft env to this machine. The env publishes only its MCP port, so a small
socat container on the env's Docker network forwards the game server and the 3D views to loopback."""

from __future__ import annotations

import subprocess

FORWARDER = "agent-games-minecraft-watch"
SOCAT_IMAGE = "alpine/socat:1.8.0.3"
VIEW_PORT = 3000
HOST_VIEW_PORT = 8300
CAMERA_PORT = 3099
HOST_CAMERA_PORT = 8399


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def find_env() -> str:
    """The id of the newest running container of the Minecraft env image."""
    for line in _docker("ps", "--format", "{{.ID}} {{.Image}}").splitlines():
        cid, image = line.split(" ", 1)
        if "agent-games-minecraft" in image:
            return cid
    raise RuntimeError("no Minecraft env is running; start one with `agent-env run native-minecraft --task duo`")


def forward(players: int = 8) -> list[str]:
    cid = find_env()
    network, ip = _docker("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}\n{{end}}",
                          cid).splitlines()[0].split()
    subprocess.run(["docker", "rm", "-f", FORWARDER], capture_output=True)
    ports = {25565: 25565, HOST_CAMERA_PORT: CAMERA_PORT, **{HOST_VIEW_PORT + i: VIEW_PORT + i for i in range(players)}}
    script = " & ".join(f"socat TCP-LISTEN:{p},fork,reuseaddr TCP:{ip}:{p}" for p in ports.values()) + " & wait"
    _docker("run", "-d", "--rm", "--name", FORWARDER, "--network", network,
            *[a for host, p in ports.items() for a in ("-p", f"127.0.0.1:{host}:{p}")], "--entrypoint", "sh", SOCAT_IMAGE,
            "-c", script)
    return [f"Forwarding env container {cid[:12]} ({ip} on {network}):",
            "  Minecraft Java 1.21.4: Multiplayer > Direct Connection > localhost:25565",
            f"  The camera being recorded: http://127.0.0.1:{HOST_CAMERA_PORT}",
            f"  3D views: http://127.0.0.1:{HOST_VIEW_PORT} to http://127.0.0.1:{HOST_VIEW_PORT + players - 1}, one per seat",
            f"Stop with: docker rm -f {FORWARDER}"]
