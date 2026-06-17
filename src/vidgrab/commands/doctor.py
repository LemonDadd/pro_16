from __future__ import annotations

import sys

import click

from ..config import AppConfig
from ..utils import check_ca_certificates, check_disk_writable, check_ffmpeg, console


@click.command("doctor")
@click.pass_obj
def cmd_doctor(config: AppConfig) -> None:
    """Check system requirements: ffmpeg, CA certs, disk writable"""
    console.print("[bold]vidgrab Doctor[/bold]")
    console.print()

    errors = []

    console.print("[bold]1. ffmpeg check:[/bold]")
    ffmpeg_ok, ffmpeg_msg = check_ffmpeg(config.ffmpeg_path)
    if ffmpeg_ok:
        console.print(f"   [green]✓[/green] {ffmpeg_msg}")
    else:
        console.print(f"   [yellow]![/yellow] {ffmpeg_msg}")
        console.print("     [yellow]Direct downloads work, but m3u8 merging requires ffmpeg.[/yellow]")

    console.print()
    console.print("[bold]2. CA certificates check:[/bold]")
    ca_ok, ca_msg = check_ca_certificates()
    console.print(f"   {'[green]✓[/green]' if ca_ok else '[red]✗[/red]'} {ca_msg}")
    if not ca_ok:
        errors.append(f"CA certificates: {ca_msg}")

    console.print()
    console.print("[bold]3. Output directory check:[/bold]")
    disk_ok, disk_msg = check_disk_writable(config.output_dir)
    console.print(f"   {'[green]✓[/green]' if disk_ok else '[red]✗[/red]'} {disk_msg}")
    if not disk_ok:
        errors.append(f"Output directory: {disk_msg}")

    console.print()
    if not errors:
        console.print("[bold green]✓ All critical checks passed![/bold green]")
        if not ffmpeg_ok:
            console.print("[yellow]  (ffmpeg is recommended for m3u8 merging)[/yellow]")
    else:
        console.print("[bold red]✗ Some critical checks failed:[/bold red]")
        for e in errors:
            console.print(f"  [red]•[/red] {e}")
        sys.exit(1)
