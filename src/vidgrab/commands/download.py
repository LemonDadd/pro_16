from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from ..config import AppConfig
from ..exceptions import EncryptedStreamError, VidgrabError
from ..orchestrator import DownloadOrchestrator
from ..utils import console
from .common import apply_common_options, with_common_options


@click.command("download")
@click.argument("url")
@with_common_options
@click.pass_obj
def cmd_download(
    config: AppConfig,
    url: str,
    output_dir: Optional[str],
    output_name: Optional[str],
    filename_template: Optional[str],
    quality: Optional[str],
    quality_flag: Optional[str],
    merge: Optional[bool],
    merge_format: str,
    keep_segments: bool,
    workers: Optional[int],
    cookies: Optional[str],
    headers: tuple[str, ...],
    proxy: Optional[str],
    rate_limit: Optional[str],
    dry_run: bool,
    list_formats: bool,
    continue_task: bool,
) -> None:
    """Download a single video from URL"""
    apply_common_options(
        config,
        output_dir=output_dir,
        filename_template=filename_template,
        workers=workers,
        merge=merge,
        cookies=cookies,
        headers=headers,
        proxy=proxy,
        rate_limit=rate_limit,
    )

    actual_quality = quality or quality_flag or config.default_quality
    actual_output_dir = Path(output_dir) if output_dir else config.output_dir

    orchestrator = DownloadOrchestrator(config)

    try:
        orchestrator.download(
            url=url,
            output_dir=actual_output_dir,
            output_name=output_name,
            quality=actual_quality,
            merge=config.merge,
            merge_format=merge_format,
            keep_segments=keep_segments,
            dry_run=dry_run,
            list_formats=list_formats,
            continue_task=continue_task,
        )
    except EncryptedStreamError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(e.exit_code)
    except VidgrabError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(e.exit_code)
