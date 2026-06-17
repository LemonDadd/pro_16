from __future__ import annotations

from pathlib import Path

import requests
from rich.progress import Progress, TaskID

from ..exceptions import DownloadError
from ..models import DownloadTask, TaskStatus, VideoStream
from ..network import NetworkConfig, RateLimiter, create_sync_session, get_content_length, supports_range_requests
from ..utils import atomic_move, logger


class DirectDownloader:
    def __init__(self, network_config: NetworkConfig):
        self.network_config = network_config
        self.session = create_sync_session(network_config)
        self.rate_limiter = RateLimiter(network_config.rate_limit_bytes)

    def download(
        self,
        task: DownloadTask,
        stream: VideoStream,
        progress: Progress | None = None,
        overall_task: TaskID | None = None,
    ) -> Path:
        task.status = TaskStatus.RUNNING
        task.selected_stream = stream
        task.save()

        output_path = task.format_output_path(stream)
        part_path = output_path.with_suffix(output_path.suffix + ".part")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        content_length = get_content_length(self.session, stream.url)
        if content_length:
            task.total_bytes = content_length
            stream.size = content_length
            task.save()

        supports_range = supports_range_requests(self.session, stream.url)
        downloaded = 0

        if task.downloaded_bytes > 0:
            downloaded = task.downloaded_bytes
            logger.debug(f"Resuming from task saved position: {downloaded} bytes")

        if part_path.exists() and supports_range and downloaded == 0:
            downloaded = part_path.stat().st_size
            task.downloaded_bytes = downloaded
            task.save()
            logger.debug(f"Resuming from part file: {downloaded} bytes")

        if downloaded > 0 and supports_range:
            logger.info(f"Resuming download from {downloaded} bytes")

        headers = {}
        if downloaded > 0 and supports_range:
            headers["Range"] = f"bytes={downloaded}-"

        try:
            response = self.session.get(stream.url, headers=headers, stream=True, timeout=self.network_config.timeout)
            response.raise_for_status()

            if downloaded > 0 and response.status_code != 206:
                logger.warning("Server does not support range requests, starting over")
                downloaded = 0
                task.downloaded_bytes = 0
                task.save()

            mode = "ab" if downloaded > 0 else "wb"
            chunk_size = 8192

            with open(part_path, mode) as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        task.downloaded_bytes = downloaded

                        self.rate_limiter.wait_if_needed_sync(len(chunk))

                        if progress and overall_task is not None:
                            progress.update(overall_task, completed=downloaded)

                        if downloaded % (1024 * 1024) == 0:
                            task.touch()

            if content_length and downloaded != content_length:
                raise DownloadError(
                    f"Download incomplete: got {downloaded} bytes, expected {content_length} bytes"
                )

            atomic_move(part_path, output_path)
            task.status = TaskStatus.COMPLETED
            task.save()

            return output_path

        except requests.exceptions.RequestException as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.save()
            raise DownloadError(f"Direct download failed: {e}") from e
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.save()
            raise
