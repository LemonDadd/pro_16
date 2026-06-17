from __future__ import annotations

from dataclasses import dataclass

import httpx
import m3u8

from ..exceptions import DownloadError, EncryptedStreamError
from ..models import StreamType, VideoStream
from ..network import NetworkConfig, resolve_relative_url


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
