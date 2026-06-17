from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .downloaders import DirectDownloader, M3U8Downloader
from .downloaders.m3u8 import M3U8Parser
from .exceptions import DownloadError, PlaywrightMissingError
from .models import DownloadTask, StreamType, TaskStatus, VideoStream
from .network import NetworkConfig
from .utils import create_progress, logger, print_streams_table, probe_duration, format_duration, humanize_size, console


class URLRecognizer:
    DIRECT_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".ts"}

    @staticmethod
    def recognize(url: str) -> StreamType:
        parsed = urlparse(url)
        path = parsed.path.lower()

        if path.endswith(".m3u8") or path.endswith(".m3u"):
            return StreamType.M3U8

        for ext in URLRecognizer.DIRECT_EXTENSIONS:
            if path.endswith(ext):
                return StreamType.DIRECT

        if re.search(r"\.(m3u8?|mp4|webm|ts)\b", url):
            if ".m3u8" in url or ".m3u" in url:
                return StreamType.M3U8
            return StreamType.DIRECT

        return StreamType.WEBPAGE

    @staticmethod
    def extract_title(url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path:
            filename = path.split("/")[-1]
            if "." in filename:
                filename = filename.rsplit(".", 1)[0]
            return filename or "video"
        return parsed.netloc or "video"


class DownloadOrchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.network_config = self._build_network_config()

    def _build_network_config(self) -> NetworkConfig:
        cookies = self.config.get_cli("cookies")
        headers = self.config.get_cli("headers", {})
        profile_headers = self.config.get_cli("profile_headers", {})
        all_headers = {**profile_headers, **headers}

        return NetworkConfig.from_options(
            cookie_file=cookies,
            headers=all_headers,
            proxy=self.config.proxy,
            rate_limit_bytes=self.config.get_cli("rate_limit_bytes"),
            timeout=30,
            retries=3,
        )

    def analyze_url(self, url: str) -> tuple[StreamType, list[VideoStream]]:
        stream_type = URLRecognizer.recognize(url)

        if stream_type == StreamType.M3U8:
            parser = M3U8Parser(self.network_config)
            try:
                streams = parser.parse_master(url)
                parser.close()
                return StreamType.M3U8, streams
            except Exception as e:
                parser.close()
                raise DownloadError(f"Failed to parse m3u8: {e}") from e

        if stream_type == StreamType.DIRECT:
            ext = Path(urlparse(url).path).suffix.lstrip(".") or "mp4"
            stream = VideoStream(
                url=url,
                stream_type=StreamType.DIRECT,
                quality="unknown",
                ext=ext,
            )
            return StreamType.DIRECT, [stream]

        return StreamType.WEBPAGE, []

    def sniff_webpage(self, url: str, download: bool = False) -> list[VideoStream]:
        try:
            from .sniff import WebSniffer
        except ImportError:
            raise PlaywrightMissingError()

        sniffer = WebSniffer(self.network_config)
        try:
            streams = sniffer.sniff(url)
        finally:
            sniffer.close()

        return streams

    def list_formats(self, url: str) -> list[VideoStream]:
        stream_type, streams = self.analyze_url(url)
        if stream_type == StreamType.WEBPAGE:
            streams = self.sniff_webpage(url)
        return streams

    def _select_stream(self, streams: list[VideoStream], quality: str) -> VideoStream:
        if not streams:
            raise DownloadError("No streams available")

        if len(streams) == 1 and streams[0].stream_type == StreamType.DIRECT:
            return streams[0]

        downloader = M3U8Downloader(self.network_config, self.config.workers, self.config.ffmpeg_path)
        try:
            return downloader.select_stream(streams, quality)
        finally:
            downloader.close()

    def download(
        self,
        url: str,
        output_dir: Path | None = None,
        output_name: str | None = None,
        quality: str = "best",
        merge: bool = True,
        merge_format: str = "mp4",
        keep_segments: bool = False,
        dry_run: bool = False,
        list_formats: bool = False,
    ) -> DownloadTask:
        output_dir = output_dir or self.config.output_dir
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        stream_type, streams = self.analyze_url(url)

        if stream_type == StreamType.WEBPAGE:
            logger.info(f"Detected webpage, attempting to sniff streams...")
            streams = self.sniff_webpage(url)
            stream_type = streams[0].stream_type if streams else StreamType.UNKNOWN

        if list_formats:
            print_streams_table(streams)
            task = DownloadTask(url=url, output_dir=str(output_dir), output_name=output_name)
            task.status = TaskStatus.PENDING
            return task

        if dry_run:
            selected = self._select_stream(streams, quality) if streams else None
            console.print("[bold]Dry Run - Selected Stream:[/bold]")
            if selected:
                print_streams_table([selected])
            task = DownloadTask(url=url, output_dir=str(output_dir), output_name=output_name)
            task.selected_stream = selected
            task.title = URLRecognizer.extract_title(url)
            task.status = TaskStatus.PENDING
            return task

        if not streams:
            raise DownloadError(f"No streams found for {url}")

        selected_stream = self._select_stream(streams, quality)

        if selected_stream.is_encrypted:
            from .exceptions import EncryptedStreamError
            raise EncryptedStreamError()

        task = DownloadTask(
            url=url,
            output_dir=str(output_dir),
            output_name=output_name,
            filename_template=self.config.filename_template,
            title=URLRecognizer.extract_title(url),
        )
        task.save()

        try:
            with create_progress() as progress:
                overall_task = progress.add_task(f"[green]{task.title}", total=0)

                if selected_stream.stream_type == StreamType.M3U8:
                    m3u8_downloader = M3U8Downloader(
                        self.network_config,
                        workers=self.config.workers,
                        ffmpeg_path=self.config.ffmpeg_path,
                    )
                    try:
                        output_path = m3u8_downloader.download(
                            task,
                            selected_stream,
                            quality=quality,
                            merge=merge,
                            merge_format=merge_format,
                            keep_segments=keep_segments,
                            progress=progress,
                            overall_task=overall_task,
                        )
                    finally:
                        m3u8_downloader.close()
                else:
                    direct_downloader = DirectDownloader(self.network_config)
                    output_path = direct_downloader.download(
                        task,
                        selected_stream,
                        progress=progress,
                        overall_task=overall_task,
                    )

            if task.status == TaskStatus.COMPLETED:
                duration = probe_duration(output_path, self.config.ffmpeg_path)
                size = output_path.stat().st_size if output_path.exists() else task.downloaded_bytes
                console.print()
                console.print("[bold green]✓ Download Complete[/bold green]")
                console.print(f"  Path: [cyan]{output_path}[/cyan]")
                console.print(f"  Size: {humanize_size(size)}")
                console.print(f"  Duration: {format_duration(duration)}")

            return task

        except KeyboardInterrupt:
            task.status = TaskStatus.PAUSED
            task.save()
            logger.info("Download paused. Use 'vidgrab resume' to continue.")
            raise
