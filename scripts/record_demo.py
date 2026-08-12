"""录制 Sentinel 问答演示 GIF.

流程: 打开前端 -> 滚动到问答区 -> 模拟真人输入问题 -> 提交 ->
等待答案 -> 缓慢滚动展示引用芯片/来源卡片/情感徽标 -> webm 转 gif.

用法:
    .venv-login/Scripts/python scripts/record_demo.py

产出: docs/demo/demo-ask.gif (中间产物 webm 会被删除)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT_GIF = ROOT / "docs" / "demo" / "demo-ask.gif"
VIDEO_DIR = ROOT / "docs" / "demo" / ".video-tmp"

FRONTEND_URL = "http://localhost:5173"
QUESTION = "最近原神有什么节奏?"
MIN_DURATION_S = 25.0
MAX_DURATION_S = 35.0
MAX_GIF_BYTES = 8 * 1024 * 1024


def record() -> Path:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()
        start = time.monotonic()

        page.goto(FRONTEND_URL, wait_until="networkidle")
        ask_section = page.locator("section.ask")
        ask_section.scroll_into_view_if_needed()
        page.wait_for_timeout(800)

        # 前端给输入框内置了示例问题, 先清空再模拟真人输入
        page.click("#question")
        page.fill("#question", "")
        page.keyboard.type(QUESTION, delay=45)
        page.wait_for_timeout(600)

        page.click("button[type=submit]")

        # 等答案出现(LLM 需 10-30s), 并等 loading 态(按钮"分析中…")结束
        page.wait_for_selector(".answer .answer-text", timeout=90_000)
        page.wait_for_selector("button[type=submit]:not([disabled])", timeout=90_000)
        page.wait_for_timeout(1200)  # 让观众看清答案

        # 缓慢滚动展示引用芯片、来源卡片与情感徽标
        for _ in range(3):
            page.mouse.wheel(0, 260)
            page.wait_for_timeout(1500)
        for _ in range(2):
            page.mouse.wheel(0, -260)
            page.wait_for_timeout(1200)

        # 补足最短时长, 停在答案区域
        ask_section.scroll_into_view_if_needed()
        elapsed = time.monotonic() - start
        if elapsed < MIN_DURATION_S:
            page.wait_for_timeout(int((MIN_DURATION_S - elapsed) * 1000))
        duration = time.monotonic() - start
        if duration > MAX_DURATION_S:
            print(f"warn: 录制时长 {duration:.1f}s 超出目标", file=sys.stderr)

        video_path = Path(page.video.path())
        context.close()
        browser.close()
    print(f"录制时长: {duration:.1f}s -> {video_path}")
    return video_path


def to_gif(webm: Path, fps: int = 10, width: int = 960) -> None:
    OUT_GIF.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vf = (
        f"fps={fps},scale={width}:-1:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
    )
    subprocess.run(
        [ffmpeg, "-y", "-i", str(webm), "-vf", vf, str(OUT_GIF)],
        check=True,
        capture_output=True,
    )


def main() -> None:
    webm = record()

    # 逐步降质直到 < 8MB
    for fps, width in [(10, 960), (8, 960), (8, 800), (6, 720)]:
        to_gif(webm, fps=fps, width=width)
        size = OUT_GIF.stat().st_size
        print(f"fps={fps} width={width} -> {size / 1024 / 1024:.2f}MB")
        if size < MAX_GIF_BYTES:
            break
    else:
        sys.exit("gif 压缩后仍超 8MB, 请手动处理")

    webm.unlink()
    print(f"done: {OUT_GIF}")


if __name__ == "__main__":
    main()
