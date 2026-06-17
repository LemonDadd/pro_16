from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path.home() / ".vidgrab"
DEFAULT_TASKS_DIR = DEFAULT_CONFIG_DIR / "tasks"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.yaml"

DEFAULT_FILENAME_TEMPLATE = "{title}_{quality}.{ext}"
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "vidgrab"


@dataclass
class ProfileConfig:
    cookies: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    proxy: str | None = None
    output_dir: str | None = None
    filename_template: str | None = None
    workers: int | None = None
    merge: bool | None = None
    ffmpeg_path: str | None = None
    rate_limit: str | None = None


@dataclass
class AppConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    workers: int = 8
    merge: bool = True
    ffmpeg_path: str | None = None
    proxy: str | None = None
    default_quality: str = "best"
    rate_limit: str | None = None
    profiles: dict[str, ProfileConfig] = field(default_factory=dict)
    _cli_overrides: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, config_path: Path | None = None) -> "AppConfig":
        config_path = config_path or DEFAULT_CONFIG_FILE
        config = cls()

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

            if "output_dir" in data:
                config.output_dir = Path(os.path.expanduser(data["output_dir"]))
            if "filename_template" in data:
                config.filename_template = data["filename_template"]
            if "workers" in data:
                config.workers = int(data["workers"])
            if "merge" in data:
                config.merge = bool(data["merge"])
            if "ffmpeg_path" in data:
                config.ffmpeg_path = os.path.expanduser(data["ffmpeg_path"])
            if "proxy" in data:
                config.proxy = data["proxy"]
            if "default_quality" in data:
                config.default_quality = data["default_quality"]
            if "rate_limit" in data:
                config.rate_limit = data["rate_limit"]

            if "profiles" in data and isinstance(data["profiles"], dict):
                for name, profile_data in data["profiles"].items():
                    headers = profile_data.get("headers")
                    if headers is None:
                        headers = profile_data.get("header", {})
                    config.profiles[name] = ProfileConfig(
                        cookies=os.path.expanduser(profile_data["cookies"]) if profile_data.get("cookies") else None,
                        headers=headers,
                        proxy=profile_data.get("proxy"),
                        output_dir=os.path.expanduser(profile_data["output_dir"]) if profile_data.get("output_dir") else None,
                        filename_template=profile_data.get("filename_template"),
                        workers=int(profile_data["workers"]) if profile_data.get("workers") else None,
                        merge=bool(profile_data["merge"]) if profile_data.get("merge") is not None else None,
                        ffmpeg_path=os.path.expanduser(profile_data["ffmpeg_path"]) if profile_data.get("ffmpeg_path") else None,
                        rate_limit=profile_data.get("rate_limit"),
                    )

        return config

    def apply_profile(self, profile_name: str) -> None:
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' not found in config")
        profile = self.profiles[profile_name]

        if profile.output_dir:
            self.output_dir = Path(profile.output_dir)
        if profile.filename_template:
            self.filename_template = profile.filename_template
        if profile.workers is not None:
            self.workers = profile.workers
        if profile.merge is not None:
            self.merge = profile.merge
        if profile.ffmpeg_path:
            self.ffmpeg_path = profile.ffmpeg_path
        if profile.proxy:
            self.proxy = profile.proxy
        if profile.rate_limit:
            self.rate_limit = profile.rate_limit

        self._cli_overrides["cookies"] = profile.cookies
        self._cli_overrides["headers"] = profile.headers

    def apply_cli_overrides(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            if value is not None:
                self._cli_overrides[key] = value
                if hasattr(self, key) and key not in ("profiles", "_cli_overrides"):
                    setattr(self, key, value)

    def get_cli(self, key: str, default: Any = None) -> Any:
        return self._cli_overrides.get(key, default)
