from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .commands import (
    cmd_batch,
    cmd_doctor,
    cmd_download,
    cmd_import,
    cmd_resume,
    cmd_sniff,
    cmd_status,
)
from .config import AppConfig
from .exceptions import EncryptedStreamError, VidgrabError
from .utils import console, logger, setup_logging


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


cli.add_command(cmd_doctor)
cli.add_command(cmd_download)
cli.add_command(cmd_batch)
cli.add_command(cmd_import)
cli.add_command(cmd_sniff)
cli.add_command(cmd_resume)
cli.add_command(cmd_status)


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
