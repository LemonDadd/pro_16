from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .config import AppConfig
from .exceptions import EncryptedStreamError, VidgrabError
from .models import DownloadTask, TaskStatus
from .network import NetworkConfig
from .orchestrator import DownloadOrchestrator
from .sniff import WebSniffer
from .utils import (
    check_ca_certificates,
    check_disk_writable,
    check_ffmpeg,
    console,
    load_url_list,
    logger,
    parse_headers,
    print_tasks_table,
    setup_logging,
)


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-V", "--version")
@click.option("-p", "--profile", "profile_name", help="Configuration profile to use")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
@click.option("-q", "--quiet", is_flag=True, help="Enable quiet mode")
@click.option("--config", "config_path", type=click.Path(), help="Path to config file")
@click.pass_context
def cli(
    ctx: click.Context,
    profile_name: Optional[str],
    verbose: bool,
    quiet: bool,
    config_path: Optional[str],
) -> None:
    """vidgrab - Batch download web videos from CLI"""
    setup_logging(verbose=verbose, quiet=quiet)

    config = AppConfig.load(Path(config_path) if config_path else None)
    if profile_name:
        try:
            config.apply_profile(profile_name)
        except ValueError as e:
            raise click.BadParameter(str(e))

    ctx.obj = config


@cli.command("doctor")
@click.pass_obj
def cmd_doctor(config: AppConfig) -> None:
    """Check system requirements: ffmpeg, CA certs, disk writable"""
    console.print("[bold]vidgrab Doctor[/bold]")
    console.print()

    all_ok = True

    console.print("[bold]1. ffmpeg check:[/bold]")
    ffmpeg_ok, ffmpeg_msg = check_ffmpeg(config.ffmpeg_path)
    console.print(f"   {'[green]✓[/green]' if ffmpeg_ok else '[red]✗[/red]'} {ffmpeg_msg}")
    all_ok = all_ok and ffmpeg_ok

    console.print()
    console.print("[bold]2. CA certificates check:[/bold]")
    ca_ok, ca_msg = check_ca_certificates()
    console.print(f"   {'[green]✓[/green]' if ca_ok else '[red]✗[/red]'} {ca_msg}")
    all_ok = all_ok and ca_ok

    console.print()
    console.print("[bold]3. Output directory check:[/bold]")
    disk_ok, disk_msg = check_disk_writable(config.output_dir)
    console.print(f"   {'[green]✓[/green]' if disk_ok else '[red]✗[/red]'} {disk_msg}")
    all_ok = all_ok and disk_ok

    console.print()
    if all_ok:
        console.print("[bold green]✓ All checks passed![/bold green]")
    else:
        console.print("[bold yellow]! Some checks failed. See above for details.[/bold yellow]")
        sys.exit(1)


_common_download_options = [
    click.option("-o", "--output-dir", type=click.Path(), help="Output directory"),
    click.option("--output-name", help="Custom output filename"),
    click.option("-O", "--filename-template", help="Filename template: {title},{id},{quality},{ext},{date}"),
    click.option("--quality", default=None, help="Video quality: best, 1080p, 720p, etc."),
    click.option("--format", "quality_flag", default=None, help="Alias for --quality"),
    click.option("--merge/--no-merge", default=None, help="Merge m3u8 segments (default: yes)"),
    click.option("--merge-format", default="mp4", help="Output format for merged file (default: mp4)"),
    click.option("--keep-segments", is_flag=True, help="Keep downloaded m3u8 segments"),
    click.option("--workers", type=int, help="Number of concurrent download workers"),
    click.option("--cookies", type=click.Path(), help="Path to Netscape cookie file"),
    click.option("--header", "headers", multiple=True, help='Custom header: "Name: Value"'),
    click.option("--proxy", help="Proxy URL: http://127.0.0.1:7890"),
    click.option("--rate-limit", help="Download rate limit: 2M, 500K, etc."),
    click.option("--dry-run", is_flag=True, help="Parse only, do not download"),
    click.option("--list-formats", is_flag=True, help="List available formats and exit"),
    click.option("--continue", "continue_task", is_flag=True, help="Continue from saved tasks.json"),
]


def apply_common_options(config: AppConfig, **kwargs) -> None:
    headers = parse_headers(kwargs.get("headers"))
    rate_limit_str = kwargs.get("rate_limit")
    rate_limit_bytes = parse_rate_limit(rate_limit_str) if rate_limit_str else None

    cli_headers = config.get_cli("headers", {})
    merged_headers = {**cli_headers, **headers}

    config.apply_cli_overrides(
        output_dir=Path(kwargs["output_dir"]) if kwargs.get("output_dir") else None,
        filename_template=kwargs.get("filename_template"),
        workers=kwargs.get("workers"),
        merge=kwargs.get("merge"),
        proxy=kwargs.get("proxy"),
        cookies=kwargs.get("cookies"),
        headers=merged_headers,
        rate_limit_bytes=rate_limit_bytes,
    )


def with_common_options(func):
    for opt in reversed(_common_download_options):
        func = opt(func)
    return func


@cli.command("download")
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
        task = orchestrator.download(
            url=url,
            output_dir=actual_output_dir,
            output_name=output_name,
            quality=actual_quality,
            merge=config.merge,
            merge_format=merge_format,
            keep_segments=keep_segments,
            dry_run=dry_run,
            list_formats=list_formats,
        )
    except EncryptedStreamError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(e.exit_code)
    except VidgrabError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(e.exit_code)


@cli.command("batch")
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
    console.print()

    orchestrator = DownloadOrchestrator(config)
    success = 0
    failed = 0

    for i, (url, name) in enumerate(items, 1):
        console.print(f"[bold][{i}/{len(items)}] Downloading: {url}[/bold]")
        if name:
            console.print(f"  Output name: {name}")

        try:
            task = orchestrator.download(
                url=url,
                output_dir=actual_output_dir,
                output_name=name,
                quality=quality,
                merge=config.merge,
                merge_format=kwargs.get("merge_format", "mp4"),
                keep_segments=kwargs.get("keep_segments", False),
                dry_run=kwargs.get("dry_run", False),
                list_formats=kwargs.get("list_formats", False),
            )
            if task.status == TaskStatus.COMPLETED:
                success += 1
        except EncryptedStreamError as e:
            console.print(f"[bold red]{e}[/bold red]")
            failed += 1
        except VidgrabError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            failed += 1
        except KeyboardInterrupt:
            console.print("\n[yellow]Batch download interrupted[/yellow]")
            break
        console.print()

    console.print(f"[bold]Batch complete: {success} succeeded, {failed} failed[/bold]")
    if failed > 0:
        sys.exit(1)


@cli.command("import")
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
    console.print()

    orchestrator = DownloadOrchestrator(config)
    success = 0
    failed = 0

    for i, (url, name) in enumerate(items, 1):
        console.print(f"[bold][{i}/{len(items)}] Downloading: {url}[/bold]")
        if name:
            console.print(f"  Output name: {name}")

        try:
            task = orchestrator.download(
                url=url,
                output_dir=actual_output_dir,
                output_name=name,
                quality=quality,
                merge=config.merge,
                merge_format=kwargs.get("merge_format", "mp4"),
                keep_segments=kwargs.get("keep_segments", False),
                dry_run=kwargs.get("dry_run", False),
                list_formats=kwargs.get("list_formats", False),
            )
            if task.status == TaskStatus.COMPLETED:
                success += 1
        except EncryptedStreamError as e:
            console.print(f"[bold red]{e}[/bold red]")
            failed += 1
        except VidgrabError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
            failed += 1
        except KeyboardInterrupt:
            console.print("\n[yellow]Import download interrupted[/yellow]")
            break
        console.print()

    console.print(f"[bold]Import complete: {success} succeeded, {failed} failed[/bold]")
    if failed > 0:
        sys.exit(1)


@cli.command("sniff")
@click.argument("url")
@click.option("--cookies", type=click.Path(), help="Path to Netscape cookie file")
@click.option("--header", "headers", multiple=True, help='Custom header: "Name: Value"')
@click.option("--proxy", help="Proxy URL")
@click.option("--download", is_flag=True, help="Download highest quality after sniffing")
@click.option("-o", "--output-dir", type=click.Path(), help="Output directory (with --download)")
@click.option("--quality", default="best", help="Quality selection (with --download)")
@click.pass_obj
def cmd_sniff(
    config: AppConfig,
    url: str,
    cookies: Optional[str],
    headers: tuple[str, ...],
    proxy: Optional[str],
    download: bool,
    output_dir: Optional[str],
    quality: str,
) -> None:
    """Sniff a webpage for video streams (requires Playwright)"""
    parsed_headers = parse_headers(headers)
    network_config = NetworkConfig.from_options(
        cookie_file=cookies,
        headers=parsed_headers,
        proxy=proxy or config.proxy,
    )

    try:
        sniffer = WebSniffer(network_config)
    except VidgrabError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(e.exit_code)

    try:
        streams = sniffer.sniff_and_print(url)
    finally:
        sniffer.close()

    if download and streams:
        apply_common_options(config, output_dir=output_dir, cookies=cookies, headers=headers, proxy=proxy)
        orchestrator = DownloadOrchestrator(config)
        actual_output_dir = Path(output_dir) if output_dir else config.output_dir

        orchestrator.download(
            url=streams[0].url,
            output_dir=actual_output_dir,
            quality=quality,
            merge=config.merge,
        )


@cli.command("resume")
@click.option("--task-id", help="Resume specific task ID")
@click.pass_obj
def cmd_resume(config: AppConfig, task_id: Optional[str]) -> None:
    """Resume incomplete download tasks"""
    if task_id:
        try:
            tasks = [DownloadTask.load(task_id)]
        except FileNotFoundError:
            console.print(f"[bold red]Task {task_id} not found[/bold red]")
            sys.exit(1)
    else:
        tasks = DownloadTask.list_incomplete()

    if not tasks:
        console.print("[yellow]No incomplete tasks found[/yellow]")
        return

    console.print(f"[bold]Resuming {len(tasks)} task(s)...[/bold]")
    console.print()

    orchestrator = DownloadOrchestrator(config)
    for task in tasks:
        if not task.selected_stream:
            console.print(f"[yellow]Skipping task {task.id}: no stream selected[/yellow]")
            continue

        console.print(f"[bold]Resuming: {task.title}[/bold]")
        try:
            orchestrator.download(
                url=task.url,
                output_dir=Path(task.output_dir),
                output_name=task.output_name,
                quality=task.selected_stream.quality,
                merge=config.merge,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Resume interrupted[/yellow]")
            break
        except VidgrabError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
        console.print()


@cli.command("status")
@click.option("--all", is_flag=True, help="Show all tasks (not just recent)")
@click.option("--task-id", help="Show details for specific task")
def cmd_status(all: bool, task_id: Optional[str]) -> None:
    """Show download task status"""
    if task_id:
        try:
            task = DownloadTask.load(task_id)
            console.print(f"[bold]Task: {task.id}[/bold]")
            console.print(f"  Status: {task.status.value}")
            console.print(f"  Title: {task.title}")
            console.print(f"  URL: {task.url}")
            console.print(f"  Output: {task.output_dir}")
            if task.selected_stream:
                console.print(f"  Quality: {task.selected_stream.quality}")
            console.print(f"  Progress: {len(task.segments_done)}/{task.total_segments} segments")
            console.print(f"  Bytes: {task.downloaded_bytes}/{task.total_bytes}")
            if task.error:
                console.print(f"  Error: {task.error}")
        except FileNotFoundError:
            console.print(f"[bold red]Task {task_id} not found[/bold red]")
            sys.exit(1)
        return

    tasks = DownloadTask.list_all()
    if not all:
        tasks = tasks[:20]

    if not tasks:
        console.print("[yellow]No tasks found[/yellow]")
        return

    print_tasks_table(tasks)


def main() -> None:
    try:
        cli(standalone_mode=False)
    except click.Abort:
        console.print("\n[yellow]Aborted[/yellow]")
        sys.exit(130)
    except VidgrabError as e:
        if not isinstance(e, EncryptedStreamError):
            console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(e.exit_code)
    except Exception as e:
        logger.exception("Unexpected error")
        console.print(f"[bold red]Unexpected error: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
