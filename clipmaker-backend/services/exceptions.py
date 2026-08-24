"""
Custom exceptions so the pipeline can distinguish "this will never work"
(bad URL, private video) from "YouTube is temporarily blocking us" (retry-able),
and so the frontend can show a clear, honest message instead of a raw
yt-dlp stack trace.
"""


class VideoDownloadError(Exception):
    """Raised when a video could not be downloaded after all strategies
    were exhausted. `user_message` is safe to show directly in the UI."""

    def __init__(
        self,
        user_message: str,
        technical_detail: str = "",
        *,
        retryable: bool = True,
    ):
        self.user_message = user_message
        self.technical_detail = technical_detail
        self.retryable = retryable
        super().__init__(user_message)
