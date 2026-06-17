from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from ..config import AppConfig
from ..utils import parse_headers, parse_rate_limit


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


def apply_common_options(config: AppConfig, **kwargs: Any) -> None:
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
