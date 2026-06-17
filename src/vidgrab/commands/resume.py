from __future__ import annotations

import sys
from typing import Optional

import click

from ..config import AppConfig
from ..exceptions import VidgrabError
from ..models import DownloadTask
from ..orchestrator import DownloadOrchestrator
from ..utils import console


@click.command("resume")
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
        console.print(f"  Task ID: {task.id}")
        console.print(f"  Progress: {len(task.segments_done)}/{task.total_segments} segments, {task.downloaded_bytes}/{task.total_bytes} bytes")
        try:
            orchestrator.download(
                url=task.url,
                existing_task=task,
                merge=config.merge,
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Resume interrupted[/yellow]")
            break
        except VidgrabError as e:
            console.print(f"[bold red]Error: {e}[/bold red]")
        console.print()
