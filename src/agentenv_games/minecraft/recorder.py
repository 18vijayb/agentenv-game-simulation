"""Record the camera view: headless Chromium opens it and a MediaRecorder captures its canvas to WebM.

Runs as its own process beside the env server; it writes ``<out>.started`` (when recording began,
ISO UTC) and ``<out>.part`` while recording, and on SIGTERM stops, then remuxes the file to
``<out>`` so it can be seeked.

    python -m agentenv_games.minecraft.recorder http://127.0.0.1:3099 /tmp/recording.webm
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

WIDTH, HEIGHT, FPS, BITRATE = 1280, 720, 24, 2_500_000
WARMUP = 6  # seconds for the view to load the world around the camera before recording starts
CHROMIUM_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
                 "--autoplay-policy=no-user-gesture-required"]
START = """async ([fps, bitrate]) => {
  const canvas = await new Promise(resolve => {
    const find = () => { const c = document.querySelector('canvas'); c ? resolve(c) : setTimeout(find, 200) }
    find()
  })
  const rec = new MediaRecorder(canvas.captureStream(fps), { mimeType: 'video/webm;codecs=vp8', videoBitsPerSecond: bitrate })
  rec.ondataavailable = async ev => { if (ev.data.size) await window.chunk(await blobToBase64(ev.data)) }
  rec.onstop = () => window.stopped()
  window.__recorder = rec
  rec.start(2000)
  await window.began()
}"""
HELPERS = """window.blobToBase64 = blob => new Promise(resolve => {
  const r = new FileReader(); r.onloadend = () => resolve(r.result.split(',')[1]); r.readAsDataURL(blob)
})"""


async def record(url: str, out: Path) -> None:
    part, started = out.with_suffix(out.suffix + ".part"), out.with_suffix(out.suffix + ".started")
    stop, stopped = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    with part.open("wb") as f:
        def chunk(data: str) -> None:
            f.write(base64.b64decode(data))
            f.flush()

        def began() -> None:
            started.write_text(datetime.now(timezone.utc).isoformat(timespec="milliseconds"))

        async with async_playwright() as p:
            browser = await p.chromium.launch(args=CHROMIUM_ARGS)
            page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            await page.expose_function("chunk", chunk)
            await page.expose_function("stopped", stopped.set)
            await page.expose_function("began", began)
            await page.add_init_script(HELPERS)
            for _ in range(60):
                try:
                    await page.goto(url)
                    break
                except Exception:
                    await asyncio.sleep(1)
            await asyncio.sleep(WARMUP)
            await page.evaluate(START, [FPS, BITRATE])
            await stop.wait()
            await page.evaluate("() => window.__recorder.stop()")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopped.wait(), 15)
            await asyncio.sleep(1)
            await browser.close()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(part), "-c", "copy", str(out)], check=True)
    part.unlink()


if __name__ == "__main__":
    asyncio.run(record(sys.argv[1], Path(sys.argv[2])))
