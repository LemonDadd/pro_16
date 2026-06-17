from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .exceptions import EncryptedStreamError, VidgrabError
from .models import DownloadTask, TaskStatus
from .orchestrator import DownloadOrchestrator
from .utils import console


@dataclass
class DownloadItem:
    url: str
    output_name: Optional[str]
    index: int
    total: int


_print_lock = threading.Lock()


def _download_single(
    item: DownloadItem,
    config: AppConfig,
    actual_output_dir: Path,
    quality: str,
    merge_format: str,
    keep_segments: bool,
    dry_run: bool,
    list_formats: bool,
    continue_task: bool,
) -> tuple[bool, Optional[DownloadTask], Optional[str]]:
    orchestrator = DownloadOrchestrator(config)

    with _print_lock:
        console.print(f"[bold][{item.index}/{item.total}] Downloading: {item.url}[/bold]")
        if item.output_name:
            console.print(f"  Output name: {item.output_name}")

    try:
        task = orchestrator.download(
            url=item.url,
            output_dir=actual_output_dir,
            output_name=item.output_name,
            quality=quality,
            merge=config.merge,
            merge_format=merge_format,
            keep_segments=keep_segments,
            dry_run=dry_run,
            list_formats=list_formats,
            continue_task=continue_task,
        )
        success = task.status == TaskStatus.COMPLETED
        return (success, task, None)
    except EncryptedStreamError as e:
        with _print_lock:
            console.print(f"[bold red]{e}[/bold red]")
        return (False, None, str(e))
    except VidgrabError as e:
        with _print_lock:
            console.print(f"[bold red]Error: {e}[/bold red]")
        return (False, None, str(e))
    except KeyboardInterrupt:
        raise
    except Exception as e:
        with _print_lock:
            console.print(f"[bold red]Error: {e}[/bold red]")
        return (False, None, str(e))


def run_batch_downloads(
    items: list[tuple[str, Optional[str]]],
    config: AppConfig,
    actual_output_dir: Path,
    quality: str,
    parallel: int = 1,
    merge_format: str = "mp4",
    keep_segments: bool = False,
    dry_run: bool = False,
    list_formats: bool = False,
    continue_task: bool = False,
    label: str = "Batch",
) -> tuple[int, int]:
    download_items = [
        DownloadItem(url=url, output_name=name, index=i, total=len(items))
        for i, (url, name) in enumerate(items, 1)
    ]

    success = 0
    failed = 0

    try:
        if parallel <= 1:
            for item in download_items:
                ok, _, _ = _download_single(
                    item,
                    config,
                    actual_output_dir,
                    quality,
                    merge_format,
                    keep_segments,
                    dry_run,
                    list_formats,
                    continue_task,
                )
                if ok:
                    success += 1
                else:
                    failed += 1
                console.print()
        else:
            with ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {
                    executor.submit(
                        _download_single,
                        item,
                        config,
                        actual_output_dir,
                        quality,
                        merge_format,
                        keep_segments,
                        dry_run,
                        list_formats,
                        continue_task,
                    ): item
                    for item in download_items
                }

                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        ok, _, _ = future.result()
                        if ok:
                            success += 1
                        else:
                            failed += 1
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        failed += 1
                        with _print_lock:
                            console.print(f"[bold red]Error downloading {item.url}: {e}[/bold red]")
    except KeyboardInterrupt:
        console.print(f"\n[yellow]{label} download interrupted[/yellow]")

    console.print(f"[bold]{label} complete: {success} succeeded, {failed} failed[/bold]")
    if failed > 0:
        sys.exit(1)

    return success, failed
