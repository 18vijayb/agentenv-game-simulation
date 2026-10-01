"""Open a locally deployed Minecraft env to this machine. The env publishes only its MCP port, so a small
socat container on the env's Docker network forwards the game server and the 3D views to loopback."""

from __future__ import annotations

import subprocess

FORWARDER = "agent-games-minecraft-watch"
SOCAT_IMAGE = "alpine/socat:1.8.0.3"


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
    ports = [25565, *range(3000, 3000 + players)]
    script = " & ".join(f"socat TCP-LISTEN:{p},fork,reuseaddr TCP:{ip}:{p}" for p in ports) + " & wait"
    _docker("run", "-d", "--rm", "--name", FORWARDER, "--network", network,
            *[a for p in ports for a in ("-p", f"127.0.0.1:{p}:{p}")], "--entrypoint", "sh", SOCAT_IMAGE, "-c", script)
    return [f"Forwarding env container {cid[:12]} ({ip} on {network}):",
            "  Minecraft Java 1.21.4: Multiplayer > Direct Connection > localhost:25565",
            f"  3D views: http://localhost:3000 to http://localhost:{3000 + players - 1}, one per seat",
            f"Stop with: docker rm -f {FORWARDER}"]
