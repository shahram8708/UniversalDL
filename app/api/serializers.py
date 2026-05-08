from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from urllib.parse import urlparse

from flask import jsonify
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator, model_validator


class QualityOption(str, Enum):
    BEST = "best"
    EIGHT_K = "8K"
    FOUR_K = "4K"
    TWO_K = "2K"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    P360 = "360p"
    P240 = "240p"
    AUDIO_ONLY = "audio_only"


class FormatOption(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"
    MP3 = "mp3"
    FLAC = "flac"
    M4A = "m4a"
    WAV = "wav"


class JobStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    DOWNLOADING = "downloading"
    CONVERTING = "converting"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(..., min_length=10, max_length=2048, description="URL to analyze")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str):
        cleaned = (value or "").strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        parsed = urlparse(cleaned)
        if not parsed.netloc or "." not in parsed.netloc:
            raise ValueError("URL must contain a valid domain")
        return cleaned


class DownloadRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(..., min_length=10, max_length=2048)
    quality: str = Field(default="best", max_length=20)
    format: FormatOption = Field(default=FormatOption.MP4)
    subtitle_language: Optional[str] = Field(default=None, max_length=10)
    subtitle_embed: bool = Field(default=False)
    embed_metadata: bool = Field(default=True)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str):
        cleaned = (value or "").strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")
        parsed = urlparse(cleaned)
        if not parsed.netloc or "." not in parsed.netloc:
            raise ValueError("URL must contain a valid domain")
        return cleaned

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str):
        valid_qualities = [
            "best",
            "8K",
            "4K",
            "2K",
            "1080p",
            "720p",
            "480p",
            "360p",
            "240p",
            "144p",
            "audio_only",
        ]
        if value not in valid_qualities:
            raise ValueError(f"Invalid quality: {value}")
        return value

    @field_validator("subtitle_language")
    @classmethod
    def validate_lang_code(cls, value: Optional[str]):
        if value is None:
            return None
        if len(value) not in (2, 5):
            raise ValueError("Language code must be 2 or 5 chars")
        return value.lower()


class BatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    urls: List[str] = Field(..., min_length=1, max_length=50, description="List of URLs to download")
    quality: str = Field(default="best")
    format: FormatOption = Field(default=FormatOption.MP4)
    notify_email: bool = Field(default=False)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, value: List[str]):
        seen = set()
        cleaned = []
        for url in value:
            item = (url or "").strip()
            if len(item) < 10 or not (item.startswith("http://") or item.startswith("https://")):
                raise ValueError("Each URL must start with http and be at least 10 chars")
            if item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned


class SubscriptionCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel_url: str = Field(..., min_length=10, max_length=2048)
    frequency: str = Field(default="daily")
    quality: str = Field(default="best")
    format: FormatOption = Field(default=FormatOption.MP4)
    notification_email: bool = Field(default=True)

    @field_validator("frequency")
    @classmethod
    def validate_frequency(cls, value: str):
        if value not in ("hourly", "daily", "weekly"):
            raise ValueError("frequency must be hourly, daily, or weekly")
        return value


class QualityInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    url: Optional[str] = None
    size_bytes: Optional[int] = None
    codec: Optional[str] = None
    bitrate: Optional[int] = None
    hdr: bool = False
    format: Optional[str] = None


class SubtitleInfo(BaseModel):
    lang: str
    label: str
    format: Optional[str] = None


class ChapterInfo(BaseModel):
    title: str
    start_ms: int


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    status: str
    platform: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    qualities: List[QualityInfo] = Field(default_factory=list)
    subtitles: List[SubtitleInfo] = Field(default_factory=list)
    chapters: List[ChapterInfo] = Field(default_factory=list)
    is_hls: bool = False


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    job_id: str
    status: JobStatus
    platform: Optional[str] = None
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    selected_quality: Optional[str] = None
    selected_format: Optional[str] = None
    progress_pct: int = 0
    speed_bps: Optional[int] = None
    eta_seconds: Optional[int] = None
    file_size_bytes: Optional[int] = None
    error_message: Optional[str] = None
    download_url: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    qualities: List[QualityInfo] = Field(default_factory=list)


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    overall_pct: int
    zip_url: Optional[str] = None
    jobs: List[JobStatusResponse] = Field(default_factory=list)


class HistoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    platform: Optional[str] = None
    title: Optional[str] = None
    content_type: Optional[str] = None
    selected_quality: Optional[str] = None
    selected_format: Optional[str] = None
    status: str
    file_size_bytes: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class HistoryResponse(BaseModel):
    items: List[HistoryItemResponse]
    total: int
    page: int
    per_page: int
    pages: int


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sub_id: str
    channel_url: str
    platform: Optional[str] = None
    channel_name: Optional[str] = None
    quality: str
    format: str
    frequency: str
    is_active: bool
    last_checked_at: Optional[datetime] = None
    next_check_at: Optional[datetime] = None
    total_downloaded: int
    created_at: Optional[datetime] = None


class PlatformInfo(BaseModel):
    platform_id: str
    display_name: str
    status: str
    success_rate_7d: float
    requires_headless: bool
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None


class FormatInfo(BaseModel):
    id: str
    label: str
    type: str
    codecs: List[str]
    description: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    status_code: int
    details: Optional[Any] = None


class ActionResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    status: Optional[str] = None
    job_id: Optional[str] = None
    batch_id: Optional[str] = None
    total_jobs: Optional[int] = None
    expires_at: Optional[str] = None


def job_to_response_dict(job) -> dict:
    status_value = job.status
    if status_value == "pending_download":
        status_value = "analyzing"
    return {
        "job_id": str(job.id),
        "status": status_value,
        "platform": job.platform,
        "title": job.title,
        "thumbnail_url": job.thumbnail_url,
        "selected_quality": job.selected_quality,
        "selected_format": job.selected_format,
        "progress_pct": job.progress_pct or 0,
        "speed_bps": job.speed_bps,
        "eta_seconds": job.eta_seconds,
        "file_size_bytes": job.file_size_bytes,
        "error_message": job.error_message,
        "download_url": job.download_url,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def make_error_response(error_code: str, message: str, status_code: int = 400, details=None):
    payload = ErrorResponse(
        error=error_code,
        message=message,
        status_code=status_code,
        details=details,
    ).model_dump()
    return jsonify(payload), status_code
