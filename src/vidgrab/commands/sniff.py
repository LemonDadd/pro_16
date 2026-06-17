from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from ..config import AppConfig
from ..exceptions import VidgrabError
from ..models import StreamType
from ..network import NetworkConfig
from ..orchestrator import DownloadOrchestrator
from ..sniff import WebSniffer
from ..utils import console, parse_headers
from .common import apply_common_options


@click.command("sniff")
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
        rate_limit_bytes=config.get_cli("rate_limit_bytes"),
        rate_limit=config.rate_limit,
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
        import sys

        m3u8_streams = [s for s in streams if s.stream_type == StreamType.M3U8]
        direct_streams = [s for s in streams if s.stream_type == StreamType.DIRECT]
        if m3u8_streams:
            best_stream = m3u8_streams[0]
            console.print(f"[green]Selected m3u8 stream (will pick highest quality internally):[/green] {best_stream.url}")
        elif direct_streams:
            best_stream = direct_streams[0]
            console.print(f"[green]Selected direct stream:[/green] {best_stream.url}")
        else:
            best_stream = streams[0]

        apply_common_options(config, output_dir=output_dir, cookies=cookies, headers=headers, proxy=proxy)
        orchestrator = DownloadOrchestrator(config)
        actual_output_dir = Path(output_dir) if output_dir else config.output_dir

        orchestrator.download(
            url=best_stream.url,
            output_dir=actual_output_dir,
            quality=quality,
            merge=config.merge,
        )
