from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from ..batch_runner import run_batch_downloads
from ..config import AppConfig
from ..utils import console, load_url_list, setup_logging
from .common import apply_common_options, with_common_options


@click.command("batch")
@click.argument("url_file", type=click.Path(exists=True))
@click.option("--parallel", type=int, default=1, help="Number of parallel downloads")
@click.option("--log", "log_file", type=click.Path(), help="Log file path")
@with_common_options
@click.pass_obj
def cmd_batch(
    config: AppConfig,
    url_file: str,
    parallel: int,
    log_file: Optional[str],
    **kwargs,
) -> None:
    """Batch download from URL list file"""
    if log_file:
        setup_logging(verbose=True, log_file=log_file)

    apply_common_options(config, **kwargs)

    actual_output_dir = Path(kwargs.get("output_dir")) if kwargs.get("output_dir") else config.output_dir
    quality = kwargs.get("quality") or kwargs.get("quality_flag") or config.default_quality

    items = load_url_list(url_file)
    console.print(f"[bold]Loaded {len(items)} URLs from {url_file}[/bold]")
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
        label="Batch",
    )
