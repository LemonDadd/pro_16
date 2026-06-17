from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

import m3u8
import httpx
from rich.progress import Progress, TaskID

from ..exceptions import DownloadError, EncryptedStreamError, FFmpegError
from ..models import DownloadTask, TaskStatus, VideoStream, StreamType
from ..network import NetworkConfig, RateLimiter, create_async_client, resolve_relative_url, async_get
from ..utils import find_ffmpeg, logger


@dataclass
class M3U8Segment:
    index: int
    url: str
    duration: float
    size: int = 0


class M3U8Parser:
    def __init__(self, network_config: NetworkConfig):
        self.network_config = network_config
        self.sync_client = httpx.Client(**network_config.to_httpx_client_kwargs())

    def close(self) -> None:
        self.sync_client.close()

    def _fetch_playlist(self, url: str) -> str:
        try:
            response = self.sync_client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as e:
            raise DownloadError(f"Failed to fetch m3u8 playlist: {e}") from e

    def parse_master(self, url: str) -> list[VideoStream]:
        playlist_content = self._fetch_playlist(url)
        playlist = m3u8.loads(playlist_content, uri=url)

        if playlist.is_endlist and playlist.segments:
            return [self._media_playlist_to_stream(url, playlist)]

        streams: list[VideoStream] = []
        for p in playlist.playlists:
            stream_info = p.stream_info
            resolution = None
            if stream_info.resolution:
                resolution = (stream_info.resolution[0], stream_info.resolution[1])

            quality = self._determine_quality(stream_info.bandwidth, resolution)
            segment_url = resolve_relative_url(url, p.uri) if not p.uri.startswith("http") else p.uri

            is_encrypted, method = self._check_encryption(playlist, p)

            streams.append(
                VideoStream(
                    url=segment_url,
                    stream_type=StreamType.M3U8,
                    quality=quality,
                    bandwidth=stream_info.bandwidth or 0,
                    resolution=resolution,
                    ext="ts",
                    codecs=stream_info.codecs[0] if stream_info.codecs else None,
                    is_encrypted=is_encrypted,
                    encryption_method=method,
                )
            )

        if not streams and playlist.segments:
            streams.append(self._media_playlist_to_stream(url, playlist))

        return streams

    def _media_playlist_to_stream(self, url: str, playlist: m3u8.M3U8) -> VideoStream:
        is_encrypted, method = self._check_encryption(playlist, None)
        return VideoStream(
            url=url,
            stream_type=StreamType.M3U8,
            quality="unknown",
            bandwidth=0,
            resolution=None,
            ext="ts",
            is_encrypted=is_encrypted,
            encryption_method=method,
        )

    def _check_encryption(self, playlist: m3u8.M3U8, variant_playlist) -> tuple[bool, str | None]:
        key = None
        if variant_playlist and hasattr(variant_playlist, "key") and variant_playlist.key:
            key = variant_playlist.key
        elif playlist.keys:
            for k in playlist.keys:
                if k:
                    key = k
                    break

        if key and key.method:
            return True, key.method
        return False, None

    def _determine_quality(self, bandwidth: int | None, resolution: tuple[int, int] | None) -> str:
        if resolution:
            height = resolution[1]
            if height >= 2160:
                return "4k"
            elif height >= 1440:
                return "1440p"
            elif height >= 1080:
                return "1080p"
            elif height >= 720:
                return "720p"
            elif height >= 480:
                return "480p"
            elif height >= 360:
                return "360p"
            return f"{height}p"

        if bandwidth:
            if bandwidth >= 25000000:
                return "4k"
            elif bandwidth >= 12000000:
                return "1440p"
            elif bandwidth >= 6000000:
                return "1080p"
            elif bandwidth >= 3000000:
                return "720p"
            elif bandwidth >= 1500000:
                return "480p"
            elif bandwidth >= 500000:
                return "360p"
            return f"{bandwidth // 1000}kbps"

        return "unknown"

    def parse_media(self, url: str) -> list[M3U8Segment]:
        playlist_content = self._fetch_playlist(url)
        playlist = m3u8.loads(playlist_content, uri=url)

        is_encrypted, method = self._check_encryption(playlist, None)
        if is_encrypted and method:
            raise EncryptedStreamError(
                f"Encrypted stream detected (METHOD={method}). vidgrab does not support DRM decryption."
            )

        segments: list[M3U8Segment] = []
        for i, seg in enumerate(playlist.segments):
            seg_url = resolve_relative_url(url, seg.uri) if not seg.uri.startswith("http") else seg.uri
            segments.append(
                M3U8Segment(
                    index=i,
                    url=seg_url,
                    duration=seg.duration or 0,
                )
            )

        return segments


class M3U8Downloader:
    def __init__(self, network_config: NetworkConfig, workers: int = 8, ffmpeg_path: str | None = None):
        self.network_config = network_config
        self.workers = workers
        self.ffmpeg_path = find_ffmpeg(ffmpeg_path)
        self.parser = M3U8Parser(network_config)
        self.rate_limiter = RateLimiter(network_config.rate_limit_bytes)

    def close(self) -> None:
        self.parser.close()

    def list_formats(self, url: str) -> list[VideoStream]:
        return self.parser.parse_master(url)

    def select_stream(self, streams: list[VideoStream], quality: str = "best") -> VideoStream:
        if not streams:
            raise DownloadError("No streams available")

        if quality == "best":
            return max(streams, key=lambda s: s.bandwidth)
        if quality == "worst":
            return min(streams, key=lambda s: s.bandwidth)

        for s in streams:
            if s.quality.lower() == quality.lower():
                return s

        try:
            target_height = int(quality.rstrip("pP"))
            for s in streams:
                if s.resolution and s.resolution[1] == target_height:
                    return s
        except ValueError:
            pass

        try:
            target_bw = int(quality)
            for s in streams:
                if abs(s.bandwidth - target_bw) < 100000:
                    return s
        except ValueError:
            pass

        logger.warning(f"Quality '{quality}' not found, using best available")
        return max(streams, key=lambda s: s.bandwidth)

    async def _download_segment(
        self,
        client: httpx.AsyncClient,
        segment: M3U8Segment,
        output_dir: Path,
        semaphore: asyncio.Semaphore,
        progress: Progress | None,
        seg_task: TaskID | None,
        retry_count: int = 3,
    ) -> Path:
        seg_path = output_dir / f"seg_{segment.index:05d}.ts"

        if seg_path.exists():
            segment.size = seg_path.stat().st_size
            if progress and seg_task is not None:
                progress.advance(seg_task)
            return seg_path

        async with semaphore:
            for attempt in range(retry_count):
                try:
                    response = await async_get(client, segment.url)
                    content = response.content
                    segment.size = len(content)

                    await self.rate_limiter.wait_if_needed(len(content))

                    seg_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(seg_path, "wb") as f:
                        f.write(content)

                    if progress and seg_task is not None:
                        progress.advance(seg_task)

                    return seg_path
                except Exception as e:
                    if attempt == retry_count - 1:
                        raise DownloadError(f"Failed to download segment {segment.index}: {e}") from e
                    await asyncio.sleep(1 * (attempt + 1))
            raise DownloadError(f"Failed to download segment {segment.index} after {retry_count} attempts")

    async def _download_segments(
        self,
        segments: list[M3U8Segment],
        output_dir: Path,
        done_segments: list[int],
        progress: Progress | None = None,
        overall_task: TaskID | None = None,
        seg_task: TaskID | None = None,
    ) -> AsyncGenerator[tuple[int, Path], None]:
        semaphore = asyncio.Semaphore(self.workers)

        pending_segments = [seg for seg in segments if seg.index not in done_segments]

        for seg in segments:
            if seg.index in done_segments:
                seg_path = output_dir / f"seg_{seg.index:05d}.ts"
                if seg_path.exists():
                    seg.size = seg_path.stat().st_size
                    if progress and seg_task is not None:
                        progress.advance(seg_task)

        async with create_async_client(self.network_config) as client:
            coros = [
                self._download_segment(client, seg, output_dir, semaphore, progress, seg_task)
                for seg in pending_segments
            ]

            for coro in asyncio.as_completed(coros):
                try:
                    path = await coro
                    seg_index = next(s.index for s in segments if f"seg_{s.index:05d}.ts" == path.name)
                    if progress and overall_task is not None:
                        progress.advance(overall_task, sum(s.size for s in segments if s.index in done_segments or s.size > 0))
                    yield seg_index, path
                except Exception as e:
                    logger.error(f"Segment download failed: {e}")
                    raise

    def _create_concat_file(self, segments: list[M3U8Segment], seg_dir: Path, concat_path: Path) -> None:
        with open(concat_path, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\n")
            for seg in segments:
                seg_path = seg_dir / f"seg_{seg.index:05d}.ts"
                f.write(f"file '{seg_path}'\n")

    def _merge_with_ffmpeg(self, concat_path: Path, output_path: Path, merge_format: str = "mp4") -> None:
        if not self.ffmpeg_path:
            raise FFmpegError("ffmpeg not found. Install ffmpeg for m3u8 merging, or use --keep-segments.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self.ffmpeg_path,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-c", "copy",
            "-f", merge_format,
            str(output_path),
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise FFmpegError(f"ffmpeg merge failed: {result.stderr.strip()}")
        except subprocess.TimeoutExpired as e:
            raise FFmpegError("ffmpeg merge timed out") from e
        except FileNotFoundError as e:
            raise FFmpegError(f"ffmpeg not found at {self.ffmpeg_path}") from e

    def download(
        self,
        task: DownloadTask,
        stream: VideoStream,
        quality: str = "best",
        merge: bool = True,
        merge_format: str = "mp4",
        keep_segments: bool = False,
        progress: Progress | None = None,
        overall_task: TaskID | None = None,
    ) -> Path:
        if stream.is_encrypted:
            raise EncryptedStreamError()

        task.status = TaskStatus.RUNNING
        task.selected_stream = stream
        task.save()

        streams = self.parser.parse_master(stream.url)
        selected_stream = self.select_stream(streams, quality) if len(streams) > 1 else stream

        if selected_stream.is_encrypted:
            task.status = TaskStatus.ENCRYPTED
            task.error = "Encrypted stream detected"
            task.save()
            raise EncryptedStreamError()

        segments = self.parser.parse_media(selected_stream.url)
        task.total_segments = len(segments)
        task.save()

        output_path = task.format_output_path(selected_stream)
        output_path = output_path.with_suffix(f".{merge_format}") if merge else output_path

        seg_dir = output_path.parent / f".segments_{task.id}"
        concat_path = seg_dir / "concat.txt"
        seg_dir.mkdir(parents=True, exist_ok=True)

        seg_task: TaskID | None = None
        if progress:
            if overall_task is not None:
                progress.update(overall_task, total=task.total_segments, completed=len(task.segments_done))
            seg_task = progress.add_task(f"[cyan]Segments", total=task.total_segments, completed=len(task.segments_done))

        async def run_download():
            async for seg_idx, _ in self._download_segments(
                segments,
                seg_dir,
                task.segments_done,
                progress,
                overall_task,
                seg_task,
            ):
                task.segments_done.append(seg_idx)
                if len(task.segments_done) % 10 == 0:
                    task.touch()

        try:
            asyncio.run(run_download())
        except EncryptedStreamError:
            raise
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.save()
            raise DownloadError(f"M3U8 download failed: {e}") from e

        if len(task.segments_done) != len(segments):
            missing = len(segments) - len(task.segments_done)
            raise DownloadError(f"Download incomplete: {missing} segments missing")

        if merge:
            if progress:
                progress.update(overall_task, description="[yellow]Merging...")
            self._create_concat_file(segments, seg_dir, concat_path)
            self._merge_with_ffmpeg(concat_path, output_path, merge_format)

            if not keep_segments and seg_dir.exists():
                shutil.rmtree(seg_dir)
        else:
            output_path = seg_dir

        total_size = sum(seg.size for seg in segments)
        task.downloaded_bytes = total_size
        task.total_bytes = total_size
        task.status = TaskStatus.COMPLETED
        task.save()

        return output_path
