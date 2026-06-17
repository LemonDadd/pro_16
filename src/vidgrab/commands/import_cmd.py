from __future__ import annotations

import json
import os
from pathlib import Path

import click

from ..batch_runner import run_batch_downloads
from ..config import AppConfig
from ..utils import console
from .common import apply_common_options, with_common_options


@click.command("import")
@click.argument("queue_file", type=click.Path(exists=True))
@click.option("--parallel", type=int, default=1, help="Number of parallel downloads")
@with_common_options
@click.pass_obj
def cmd_import(
    config: AppConfig,
    queue_file: str,
    parallel: int,
    **kwargs,
) -> None:
    """Import download queue from JSON file"""
    apply_common_options(config, **kwargs)

    actual_output_dir = Path(kwargs.get("output_dir")) if kwargs.get("output_dir") else config.output_dir
    quality = kwargs.get("quality") or kwargs.get("quality_flag") or config.default_quality

    queue_path = Path(os.path.expanduser(queue_file))
    with open(queue_path, "r", encoding="utf-8") as f:
        queue_data = json.load(f)

    items: list[tuple[str, str | None]] = []
    if isinstance(queue_data, list):
        for item in queue_data:
            if isinstance(item, dict):
                url = item.get("url") or item.get("download_url")
                name = item.get("output_name") or item.get("name") or item.get("title")
                if url:
                    items.append((url, name))
            elif isinstance(item, str):
                items.append((item, None))

    console.print(f"[bold]Imported {len(items)} items from {queue_file}[/bold]")
    if parallel > 1:
        console.print(f"[bold]Using {parallel} parallel downloads[/bold]")
    console.print()

    run_batch_downloads(
        items=items,
        config=config,
        actual_output_dir=actual_output_dir,
        quality=quality,
        parallel=parallel,
        merge_format=kwargs.get("merge_format", "mp4"),
        keep_segments=kwargs.get("keep_segments", False),
        dry_run=kwargs.get("dry_run", False),
        list_formats=kwargs.get("list_formats", False),
        continue_task=kwargs.get("continue_task", False),
        label="Import",
    )
