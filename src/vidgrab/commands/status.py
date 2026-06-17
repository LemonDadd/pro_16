from __future__ import annotations

import sys
from typing import Optional

import click

from ..models import DownloadTask
from ..utils import console, print_tasks_table


@click.command("status")
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
