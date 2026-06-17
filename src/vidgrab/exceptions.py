from __future__ import annotations


class VidgrabError(Exception):
    exit_code: int = 1

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class EncryptedStreamError(VidgrabError):
    exit_code = 2

    def __init__(self, message: str = "Encrypted stream detected (DRM). vidgrab does not support DRM decryption.") -> None:
        super().__init__(message, exit_code=2)


class DownloadError(VidgrabError):
    pass


class NetworkError(VidgrabError):
    pass


class ConfigError(VidgrabError):
    pass


class FFmpegError(VidgrabError):
    pass


class PlaywrightMissingError(VidgrabError):
    def __init__(self) -> None:
        super().__init__(
            "Playwright is required for web sniffing. Install with: pip install vidgrab[sniff] && playwright install chromium",
            exit_code=3,
        )
