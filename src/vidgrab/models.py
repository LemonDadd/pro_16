from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .config import DEFAULT_TASKS_DIR


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ENCRYPTED = "encrypted"


class StreamType(str, Enum):
    DIRECT = "direct"
    M3U8 = "m3u8"
    WEBPAGE = "webpage"
    UNKNOWN = "unknown"


@dataclass
class VideoStream:
    url: str
    stream_type: StreamType
    quality: str = "unknown"
    bandwidth: int = 0
    resolution: tuple[int, int] | None = None
    ext: str = "mp4"
    codecs: str | None = None
    is_encrypted: bool = False
    encryption_method: str | None = None
    size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["resolution"] = f"{self.resolution[0]}x{self.resolution[1]}" if self.resolution else None
        return d


@dataclass
class DownloadTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: str = ""
    output_dir: str = ""
    output_name: str | None = None
    filename_template: str = "{title}_{quality}.{ext}"
    selected_stream: VideoStream | None = None
    title: str = "video"
    status: TaskStatus = TaskStatus.PENDING
    total_bytes: int = 0
    downloaded_bytes: int = 0
    segments_done: list[int] = field(default_factory=list)
    total_segments: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def task_file(self) -> Path:
        return DEFAULT_TASKS_DIR / f"{self.id}.json"

    def save(self) -> None:
        DEFAULT_TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.time()
        data = {
            "id": self.id,
            "url": self.url,
            "output_dir": self.output_dir,
            "output_name": self.output_name,
            "filename_template": self.filename_template,
            "title": self.title,
            "status": self.status.value,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "segments_done": self.segments_done,
            "total_segments": self.total_segments,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }
        if self.selected_stream:
            data["selected_stream"] = self.selected_stream.to_dict()
        with open(self.task_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, task_id: str) -> "DownloadTask":
        task_file = DEFAULT_TASKS_DIR / f"{task_id}.json"
        if not task_file.exists():
            raise FileNotFoundError(f"Task file not found: {task_file}")
        with open(task_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        task = cls(
            id=data["id"],
            url=data["url"],
            output_dir=data["output_dir"],
            output_name=data.get("output_name"),
            filename_template=data.get("filename_template", "{title}_{quality}.{ext}"),
            title=data.get("title", "video"),
            status=TaskStatus(data["status"]),
            total_bytes=data.get("total_bytes", 0),
            downloaded_bytes=data.get("downloaded_bytes", 0),
            segments_done=data.get("segments_done", []),
            total_segments=data.get("total_segments", 0),
            error=data.get("error"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )
        if stream_data := data.get("selected_stream"):
            task.selected_stream = VideoStream(
                url=stream_data["url"],
                stream_type=StreamType(stream_data["stream_type"]),
                quality=stream_data.get("quality", "unknown"),
                bandwidth=stream_data.get("bandwidth", 0),
                resolution=tuple(map(int, stream_data["resolution"].split("x"))) if stream_data.get("resolution") else None,
                ext=stream_data.get("ext", "mp4"),
                codecs=stream_data.get("codecs"),
                is_encrypted=stream_data.get("is_encrypted", False),
                encryption_method=stream_data.get("encryption_method"),
                size=stream_data.get("size"),
            )
        return task

    @classmethod
    def list_all(cls) -> list["DownloadTask"]:
        if not DEFAULT_TASKS_DIR.exists():
            return []
        tasks = []
        for f in DEFAULT_TASKS_DIR.glob("*.json"):
            try:
                tasks.append(cls.load(f.stem))
            except Exception:
                continue
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        return tasks

    @classmethod
    def list_incomplete(cls) -> list["DownloadTask"]:
        return [t for t in cls.list_all() if t.status in (TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.RUNNING)]

    def format_output_path(self, stream: VideoStream | None = None) -> Path:
        stream = stream or self.selected_stream
        if self.output_name:
            return Path(self.output_dir) / self.output_name
        template = self.filename_template
        quality = stream.quality if stream else "unknown"
        ext = stream.ext if stream else "mp4"
        date = datetime.now().strftime("%Y%m%d")
        safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in self.title).strip()
        filename = template.format(
            title=safe_title,
            id=self.id,
            quality=quality,
            ext=ext,
            date=date,
        )
        return Path(self.output_dir) / filename

    def touch(self) -> None:
        self.updated_at = time.time()
        self.save()
