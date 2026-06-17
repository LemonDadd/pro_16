from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from .exceptions import FFmpegError
from .models import DownloadTask, TaskStatus

console = Console()

logger = logging.getLogger("vidgrab")


def setup_logging(verbose: bool = False, quiet: bool = False, log_file: str | None = None) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handlers: list[logging.Handler] = []

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handlers.append(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def humanize_size(size: int) -> str:
    if size is None or size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}" if size >= 100 else f"{size:.2f} {units[i]}"


def parse_rate_limit(limit: str | None) -> int | None:
    if not limit:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([kmgKMG])?([bB])?$", limit.strip())
    if not m:
        raise ValueError(f"Invalid rate limit: {limit}")
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    bits = (m.group(3) or "").lower() == "b"
    if unit == "k":
        value *= 1024
    elif unit == "m":
        value *= 1024 * 1024
    elif unit == "g":
        value *= 1024 * 1024 * 1024
    if bits:
        value /= 8
    return int(value)


def find_ffmpeg(custom_path: str | None = None) -> str | None:
    if custom_path:
        if os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
            return custom_path
        return None
    return shutil.which("ffmpeg")


def find_ffprobe(custom_path: str | None = None) -> str | None:
    if custom_path:
        ffmpeg_dir = os.path.dirname(custom_path)
        ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe")
        if os.path.isfile(ffprobe_path) and os.access(ffprobe_path, os.X_OK):
            return ffprobe_path
    return shutil.which("ffprobe")


def check_ffmpeg(custom_path: str | None = None) -> tuple[bool, str]:
    ffmpeg = find_ffmpeg(custom_path)
    if not ffmpeg:
        return False, "ffmpeg not found. Install ffmpeg or specify path in config."
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, f"ffmpeg execution failed: {result.stderr.strip()}"
        version = result.stdout.split("\n")[0].strip()
        return True, version
    except Exception as e:
        return False, f"ffmpeg check failed: {e}"


def check_disk_writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".vidgrab_write_test"
        with open(test_file, "w") as f:
            f.write("test")
        test_file.unlink()
        return True, f"Directory {path} is writable"
    except Exception as e:
        return False, f"Directory {path} is not writable: {e}"


def check_ca_certificates() -> tuple[bool, str]:
    try:
        import ssl

        ctx = ssl.create_default_context()
        if ctx.cert_store_stats().get("certs", 0) > 0:
            return True, f"CA certificates loaded: {ctx.cert_store_stats().get('certs', 0)} certs"
        return True, "SSL context created (using system CA)"
    except Exception as e:
        return False, f"CA certificate check failed: {e}"


def probe_duration(file_path: Path, ffprobe_path: str | None = None) -> float | None:
    ffprobe = find_ffprobe(ffprobe_path)
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_netscape_cookies(cookie_file: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    path = Path(os.path.expanduser(cookie_file))
    if not path.exists():
        raise FileNotFoundError(f"Cookie file not found: {cookie_file}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("# HttpOnly"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                cookies[name] = value
    return cookies


def load_url_list(file_path: str) -> list[tuple[str, str | None]]:
    path = Path(os.path.expanduser(file_path))
    if not path.exists():
        raise FileNotFoundError(f"URL list file not found: {file_path}")
    items: list[tuple[str, str | None]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                url, name = parts[0], parts[1].strip()
            else:
                url, name = parts[0], None
            items.append((url, name))
    return items


def atomic_move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))


@contextmanager
def create_progress(show_overall: bool = True) -> Iterator[Progress]:
    columns = [
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ]
    progress = Progress(*columns, console=console, transient=False)
    with progress:
        yield progress


def print_tasks_table(tasks: list[DownloadTask]) -> None:
    table = Table(title="Download Tasks", show_lines=False)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")
    table.add_column("Title", style="green")
    table.add_column("Progress", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Updated", justify="right")

    status_colors = {
        TaskStatus.COMPLETED: "green",
        TaskStatus.RUNNING: "yellow",
        TaskStatus.PENDING: "blue",
        TaskStatus.PAUSED: "yellow",
        TaskStatus.FAILED: "red",
        TaskStatus.ENCRYPTED: "red",
    }

    for task in tasks:
        if task.total_bytes > 0:
            pct = (task.downloaded_bytes / task.total_bytes) * 100
            progress_str = f"{pct:.1f}%"
        elif task.total_segments > 0:
            pct = (len(task.segments_done) / task.total_segments) * 100
            progress_str = f"{len(task.segments_done)}/{task.total_segments} ({pct:.1f}%)"
        else:
            progress_str = "N/A"

        size_str = humanize_size(task.total_bytes) if task.total_bytes > 0 else "N/A"
        updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(task.updated_at))
        status_color = status_colors.get(task.status, "white")
        table.add_row(
            task.id,
            f"[{status_color}]{task.status.value}[/{status_color}]",
            task.title,
            progress_str,
            size_str,
            updated,
        )
    console.print(table)


def print_streams_table(streams: list) -> None:
    table = Table(title="Available Streams", show_lines=False)
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Type", style="yellow")
    table.add_column("Quality", style="green")
    table.add_column("Resolution", style="blue")
    table.add_column("Bitrate", justify="right")
    table.add_column("Codecs", style="magenta")
    table.add_column("Encrypted", style="red", justify="center")

    for i, stream in enumerate(streams, 1):
        res = f"{stream.resolution[0]}x{stream.resolution[1]}" if stream.resolution else "N/A"
        bitrate = f"{stream.bandwidth // 1000} kbps" if stream.bandwidth else "N/A"
        encrypted = "✓" if stream.is_encrypted else "✗"
        table.add_row(
            str(i),
            stream.stream_type.value,
            stream.quality,
            res,
            bitrate,
            stream.codecs or "N/A",
            encrypted,
        )
    console.print(table)


def parse_headers(headers: tuple[str, ...] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not headers:
        return result
    for h in headers:
        if ":" in h:
            key, value = h.split(":", 1)
            result[key.strip()] = value.strip()
    return result
