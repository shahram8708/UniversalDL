import json
import logging
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from html import unescape as html_unescape

import httpx
import redis
from sqlalchemy import or_
from celery.exceptions import Ignore
from flask import current_app

from app.extensions import db
from app.models import AuditLog, BatchQueue, DownloadJob, Subscription, User
from app.services import dispatcher, ffmpeg, metadata, storage, url_parser
from app.services.notify import (
    send_batch_complete_email,
    send_download_complete_email,
    send_download_failed_email,
    send_subscription_new_content_email,
)
from app.services.proxy import proxy_pool
from app.extractors.base import BaseExtractor, ExtractorError
from app.celery_app import celery


logger = logging.getLogger(__name__)

IMAGE_SOURCE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
IMAGE_OUTPUT_FORMATS = {"jpg", "jpeg", "png"}
AUDIO_OUTPUT_FORMATS = {"mp3", "m4a", "flac", "wav"}
VIDEO_OUTPUT_FORMATS = {"mp4", "mkv", "webm"}

RETRYABLE_EXTRACTOR_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unreachable",
    "temporarily unavailable",
    "network error",
    "connection reset",
    "connection aborted",
    "could not connect",
    "service unavailable",
    "getaddrinfo failed",
    "name resolution",
    "temporary failure in name resolution",
    "nodename nor servname provided",
    "429",
    "403",
    "forbidden",
    "cloudflare",
    "captcha",
    "blocked",
)

STREAM_DOWNLOAD_MAX_ATTEMPTS = 4
STREAM_DOWNLOAD_RETRY_BACKOFF_SECONDS = 1.5


def _get_redis():
    redis_url = current_app.config.get("REDIS_URL")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _cache_media_info(job_id: str, media_info: dict):
    client = _get_redis()
    client.setex(f"media_info:{job_id}", 1800, json.dumps(media_info))


def _load_cached_media_info(job_id: str):
    client = _get_redis()
    value = client.get(f"media_info:{job_id}")
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _is_retryable_extractor_error(exc: Exception) -> bool:
    if not isinstance(exc, ExtractorError):
        return False

    seen = set()
    current = exc
    while current and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if message and any(marker in message for marker in RETRYABLE_EXTRACTOR_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__

    return False


def _pick_quality(media_info, selected_quality):
    qualities = media_info.get("qualities") or []
    if not qualities:
        return None
    if selected_quality == "best":
        return qualities[0]
    selected_value = str(selected_quality or "").strip().lower()
    for quality in qualities:
        selector = str(quality.get("selector") or quality.get("format_id") or "").strip().lower()
        label = str(quality.get("label") or "").strip().lower()
        display_label = str(quality.get("display_label") or "").strip().lower()
        if not selected_value:
            continue
        if selected_value in {selector, label, display_label}:
            return quality

    for quality in qualities:
        label = str(quality.get("label") or "").strip().lower()
        if selected_value and label and selected_value.startswith(label):
            return quality

    return qualities[0]


def _is_audio_only_quality(quality: dict) -> bool:
    label = str((quality or {}).get("label") or "").strip().lower()
    return label in {"audio", "audio_only", "audio only"}


def _resolve_audio_url(media_info: dict, selected_quality: dict) -> str:
    if not selected_quality:
        return None

    direct_audio_url = selected_quality.get("audio_url")
    if direct_audio_url:
        return direct_audio_url

    qualities = media_info.get("qualities") or []
    audio_only_candidates = []
    shared_audio_urls = []

    for quality in qualities:
        if not quality:
            continue
        if quality.get("audio_url"):
            shared_audio_urls.append(quality.get("audio_url"))
        if _is_audio_only_quality(quality) and quality.get("url"):
            audio_only_candidates.append(quality)

    if audio_only_candidates:
        best = max(audio_only_candidates, key=lambda item: item.get("bitrate") or 0)
        return best.get("url")

    if shared_audio_urls:
        return shared_audio_urls[0]

    return None


def _url_has_extension(url: str, extensions: tuple) -> bool:
    value = str(url or "").split("?", 1)[0].strip().lower()
    if not value:
        return False
    return any(value.endswith(ext) for ext in extensions)


def _is_hls_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    base = value.split("?", 1)[0]
    if base.endswith(".m3u8"):
        return True
    return ".m3u8" in value


def _is_image_quality(quality: dict) -> bool:
    format_value = str((quality or {}).get("format") or "").strip().lower()
    if format_value in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return True
    label_value = str((quality or {}).get("label") or "").strip().lower()
    if label_value.startswith("image"):
        return True
    return _url_has_extension((quality or {}).get("url"), IMAGE_SOURCE_EXTENSIONS)


def _is_image_content(job: DownloadJob, media_info: dict, quality: dict) -> bool:
    explicit_type = str((media_info or {}).get("content_type") or "").strip().lower()
    if explicit_type in {"video", "audio", "document"}:
        return False

    if _is_image_quality(quality):
        return True
    if str(job.content_type or "").strip().lower() == "image":
        return True
    if explicit_type == "image":
        return True

    qualities = (media_info or {}).get("qualities") or []
    if qualities and all(_is_image_quality(item) for item in qualities if item):
        return True
    return False


def _normalize_selected_format(selected_format: str, is_audio_only: bool, is_image_content: bool) -> str:
    normalized = str(selected_format or "").strip().lower()

    if is_image_content:
        return normalized if normalized in IMAGE_OUTPUT_FORMATS else "jpg"

    if is_audio_only:
        return normalized if normalized in AUDIO_OUTPUT_FORMATS else "mp3"

    if normalized in VIDEO_OUTPUT_FORMATS or normalized in AUDIO_OUTPUT_FORMATS or normalized in IMAGE_OUTPUT_FORMATS:
        return normalized

    return "mp4"


def _resolve_soundcloud_audio_format(media_info: dict, quality: dict, selected_format: str) -> str:
    platform_id = str((media_info or {}).get("platform") or "").strip().lower()
    if platform_id != "soundcloud":
        return selected_format

    quality_format = str((quality or {}).get("format") or "").strip().lower()
    if quality_format in AUDIO_OUTPUT_FORMATS:
        return quality_format

    quality_url = str((quality or {}).get("url") or "").strip().lower()
    if quality_url.endswith(".m3u8") or ".m3u8" in quality_url:
        return "m4a"
    if quality_url.endswith(".m4a"):
        return "m4a"
    if quality_url.endswith(".mp3"):
        return "mp3"

    return selected_format


def _selected_format_matches_detected(selected_format: str, detected_format: str) -> bool:
    detected_value = str(detected_format or "").strip().lower()
    if not detected_value:
        return False

    aliases = {
        "jpg": {"jpg", "jpeg", "mjpeg", "image2"},
        "jpeg": {"jpg", "jpeg", "mjpeg", "image2"},
        "png": {"png", "image2"},
        "mp4": {"mp4", "mov", "m4a", "3gp", "mj2"},
        "mkv": {"matroska", "mkv"},
        "webm": {"webm", "matroska"},
        "mp3": {"mp3"},
        "flac": {"flac"},
        "m4a": {"m4a", "aac", "mp4"},
        "wav": {"wav"},
    }
    for token in aliases.get(selected_format, {selected_format}):
        if token and token in detected_value:
            return True
    return False


def _detect_downloaded_format(file_path: str) -> str:
    if not file_path or not os.path.exists(file_path):
        return "unknown"
    try:
        if os.path.getsize(file_path) <= 0:
            return "unknown"
    except OSError:
        return "unknown"
    return ffmpeg.detect_format(file_path)


def _normalize_remote_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return value

    for _ in range(2):
        decoded = html_unescape(value)
        if decoded == value:
            break
        value = decoded

    return value.replace("\\u0026", "&")


@celery.task(bind=True, max_retries=3, name="tasks.analyze_url")
def analyze_url_task(self, job_id: str) -> dict:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return {}
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return {}
    if job.status in {"pending_download", "complete"}:
        return _load_cached_media_info(job_id) or {}

    job.status = "analyzing"
    job.progress_pct = 5
    job.speed_bps = None
    job.eta_seconds = None
    job.error_message = None
    db.session.commit()

    try:
        platform_id, content_type, clean_url = url_parser.parse_url(job.url)
        job.platform = platform_id
        job.content_type = url_parser.normalize_content_type(content_type)
        job.progress_pct = 25
        db.session.commit()

        job.progress_pct = 60
        db.session.commit()
        media_info = dispatcher.dispatch_and_extract(platform_id, clean_url, proxy_pool)
        job.title = media_info.get("title")
        job.thumbnail_url = media_info.get("thumbnail")
        _cache_media_info(job_id, media_info)

        job.progress_pct = 100
        job.status = "pending_download"
        db.session.commit()
        return media_info
    except ExtractorError as exc:
        if _is_retryable_extractor_error(exc) and self.request.retries < self.max_retries:
            job.status = "queued"
            job.progress_pct = 0
            job.error_message = "Temporary network issue while analyzing URL. Retrying automatically."
            db.session.commit()
            countdown = 20 * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=countdown)

        job.status = "failed"
        job.progress_pct = 0
        job.error_message = str(exc)
        db.session.commit()
        if job.batch_id:
            batch_status_update_task.delay(str(job.batch_id))
        raise Ignore() from exc
    except httpx.HTTPError as exc:
        job.status = "failed"
        job.progress_pct = 0
        job.error_message = "Network error while analyzing URL. Please try again."
        db.session.commit()
        if job.batch_id:
            batch_status_update_task.delay(str(job.batch_id))
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
    except Exception as exc:
        job.status = "failed"
        job.progress_pct = 0
        job.error_message = "Failed to analyze URL. Please try again."
        db.session.commit()
        if job.batch_id:
            batch_status_update_task.delay(str(job.batch_id))
        raise


@celery.task(bind=True, max_retries=3, name="tasks.download_media")
def download_media_task(self, job_id: str, subtitle_embed_mode: str = None) -> str:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return ""
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return ""
    if job.status == "complete":
        job_dir = storage.get_job_dir(job_id)
        for name in os.listdir(job_dir):
            return os.path.join(job_dir, name)
        return ""
    if job.status == "cancelled":
        return ""

    job.status = "downloading"
    job.progress_pct = 0
    job.speed_bps = None
    job.eta_seconds = None
    job.error_message = None
    db.session.commit()

    try:
        media_info = _load_cached_media_info(job_id)
        if not media_info:
            platform_id, content_type, clean_url = url_parser.parse_url(job.url)
            media_info = dispatcher.dispatch_and_extract(platform_id, clean_url, proxy_pool)
            _cache_media_info(job_id, media_info)

        quality = _pick_quality(media_info, job.selected_quality or "best")
        if not quality:
            raise ExtractorError("No quality options available", platform=media_info.get("platform"))

        job_dir = storage.get_job_dir(job_id)

        is_audio_only = _is_audio_only_quality(quality) or str(job.content_type or "").strip().lower() == "audio"
        is_image_content = _is_image_content(job, media_info, quality)
        selected_format = _normalize_selected_format(job.selected_format or "mp4", is_audio_only, is_image_content)
        if is_audio_only:
            selected_format = _resolve_soundcloud_audio_format(media_info, quality, selected_format)
        selected_output_is_image = selected_format in IMAGE_OUTPUT_FORMATS
        if job.selected_format != selected_format:
            job.selected_format = selected_format

        filename = metadata.generate_filename(media_info, quality.get("label"), selected_format)
        output_path = storage.get_output_path(job_id, filename)

        temp_path = output_path
        headers_required = media_info.get("headers_required") or {}

        if quality.get("size_bytes"):
            job.file_size_bytes = quality.get("size_bytes")
            db.session.commit()

        quality_url = _normalize_remote_url(quality.get("url"))
        fallback_audio_url = _normalize_remote_url(_resolve_audio_url(media_info, quality))
        quality_is_hls = bool(quality.get("is_hls") or _is_hls_url(quality_url))
        fallback_audio_is_hls = _is_hls_url(fallback_audio_url)

        if not quality_url:
            raise ExtractorError("Selected quality is missing media URL", platform=media_info.get("platform"))

        if is_audio_only:
            if quality_is_hls:
                temp_path = ffmpeg.stitch_hls(
                    quality_url,
                    output_path,
                    headers=headers_required,
                    progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                )
            else:
                temp_path = stream_download(
                    quality_url,
                    output_path,
                    headers=headers_required,
                    expected_size=quality.get("size_bytes"),
                    progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                )
        elif is_image_content:
            temp_path = stream_download(
                quality_url,
                output_path,
                headers=headers_required,
                expected_size=quality.get("size_bytes"),
                progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
            )
        elif fallback_audio_url:
            video_path = os.path.join(job_dir, "video_stream.mp4")
            if quality_is_hls:
                video_path = ffmpeg.stitch_hls(
                    quality_url,
                    video_path,
                    headers=headers_required,
                    progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                )
            else:
                stream_download(
                    quality_url,
                    video_path,
                    headers=headers_required,
                    expected_size=quality.get("size_bytes"),
                    progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                )

            video_has_stream = ffmpeg.has_video_stream(video_path)
            video_has_audio = ffmpeg.has_audio_stream(video_path)

            if video_has_stream and video_has_audio:
                os.replace(video_path, output_path)
                temp_path = output_path
            elif video_has_stream:
                audio_path = os.path.join(job_dir, "audio_stream.m4a")
                if fallback_audio_is_hls:
                    audio_path = ffmpeg.stitch_hls(
                        fallback_audio_url,
                        audio_path,
                        headers=headers_required,
                    )
                else:
                    stream_download(fallback_audio_url, audio_path, headers=headers_required)
                temp_path = ffmpeg.merge_audio_video(video_path, audio_path, output_path, headers=headers_required)
            else:
                raise RuntimeError("Downloaded stream does not contain a playable video track.")
        elif quality_is_hls:
            temp_path = ffmpeg.stitch_hls(
                quality_url,
                output_path,
                headers=headers_required,
                progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
            )
        else:
            temp_path = stream_download(
                quality_url,
                output_path,
                headers=headers_required,
                expected_size=quality.get("size_bytes"),
                progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
            )

        if not is_audio_only and not is_image_content and not ffmpeg.has_video_stream(temp_path):
            manifest_url = _normalize_remote_url(str(media_info.get("manifest_url") or "").strip())
            if manifest_url:
                try:
                    logger.warning(
                        "Downloaded media has no playable video stream for job %s. Retrying via manifest.",
                        job_id,
                    )
                    temp_path = ffmpeg.stitch_hls(
                        manifest_url,
                        output_path,
                        headers=headers_required,
                        progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                    )
                except Exception as video_fallback_exc:
                    logger.warning("Video recovery via manifest failed for job %s: %s", job_id, video_fallback_exc)

            if not ffmpeg.has_video_stream(temp_path):
                raise RuntimeError("Downloaded media does not contain a playable video stream.")

        if not is_audio_only and not is_image_content and not selected_output_is_image and not ffmpeg.has_audio_stream(temp_path):
            if fallback_audio_url and ffmpeg.has_video_stream(temp_path):
                recovered_audio_path = os.path.join(job_dir, "audio_recover.m4a")
                try:
                    if fallback_audio_is_hls:
                        recovered_audio_path = ffmpeg.stitch_hls(
                            fallback_audio_url,
                            recovered_audio_path,
                            headers=headers_required,
                        )
                    else:
                        stream_download(fallback_audio_url, recovered_audio_path, headers=headers_required)

                    merged_output_path = output_path
                    if os.path.abspath(temp_path) == os.path.abspath(output_path):
                        _, output_ext = os.path.splitext(output_path)
                        merged_output_path = os.path.join(job_dir, f"merged_with_audio{output_ext or '.mp4'}")
                    temp_path = ffmpeg.merge_audio_video(
                        temp_path,
                        recovered_audio_path,
                        merged_output_path,
                        headers=headers_required,
                    )
                except Exception as audio_merge_exc:
                    logger.warning("Audio recovery via fallback stream failed for job %s: %s", job_id, audio_merge_exc)

            manifest_url = _normalize_remote_url(str(media_info.get("manifest_url") or "").strip())
            if manifest_url and not ffmpeg.has_audio_stream(temp_path):
                try:
                    logger.warning(
                        "Downloaded video has no audio stream for job %s. Retrying via manifest.",
                        job_id,
                    )
                    manifest_is_audio = "st=audio" in manifest_url.lower()
                    if manifest_is_audio and ffmpeg.has_video_stream(temp_path):
                        manifest_audio_path = os.path.join(job_dir, "manifest_audio.m4a")
                        manifest_audio_path = ffmpeg.stitch_hls(
                            manifest_url,
                            manifest_audio_path,
                            headers=headers_required,
                        )
                        merged_output_path = output_path
                        if os.path.abspath(temp_path) == os.path.abspath(output_path):
                            _, output_ext = os.path.splitext(output_path)
                            merged_output_path = os.path.join(job_dir, f"manifest_merged_audio{output_ext or '.mp4'}")
                        temp_path = ffmpeg.merge_audio_video(
                            temp_path,
                            manifest_audio_path,
                            merged_output_path,
                            headers=headers_required,
                        )
                    else:
                        temp_path = ffmpeg.stitch_hls(
                            manifest_url,
                            output_path,
                            headers=headers_required,
                            progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                        )
                except Exception as audio_fallback_exc:
                    logger.warning("Audio recovery via manifest failed for job %s: %s", job_id, audio_fallback_exc)

            if manifest_url and not ffmpeg.has_audio_stream(temp_path):
                logger.warning(
                    "Downloaded video does not contain an audio stream for job %s after manifest fallback. Proceeding with video-only file.",
                    job_id,
                )
                # Proceed without audio rather than failing the entire job
                # downstream processing will continue with the video-only file
                pass

        detected = "image" if is_image_content else _detect_downloaded_format(temp_path)
        if not is_image_content and detected == "unknown":
            manifest_url = _normalize_remote_url(str(media_info.get("manifest_url") or "").strip())
            if manifest_url:
                try:
                    logger.warning(
                        "Primary media download produced unknown format for job %s. Retrying via manifest.",
                        job_id,
                    )
                    temp_path = ffmpeg.stitch_hls(
                        manifest_url,
                        output_path,
                        headers=headers_required,
                        progress_callback=lambda pct, spd: update_progress(job_id, pct, spd),
                    )
                    detected = _detect_downloaded_format(temp_path)
                except Exception as fallback_exc:
                    logger.warning("Manifest fallback failed for job %s: %s", job_id, fallback_exc)
            if detected == "unknown":
                raise RuntimeError("Downloaded media file is invalid or incomplete.")

        job.status = "converting"
        job.progress_pct = max(job.progress_pct or 0, 95)
        job.eta_seconds = None
        db.session.commit()

        if not _selected_format_matches_detected(selected_format, detected):
            converted_path = ffmpeg.convert(temp_path, selected_format)
            if converted_path != temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            temp_path = converted_path

        if job.subtitle_language and not is_audio_only and not is_image_content:
            subtitle = next(
                (s for s in media_info.get("subtitles", []) if s.get("lang") == job.subtitle_language),
                None,
            )
            if subtitle and subtitle.get("url"):
                subtitle_path = os.path.join(job_dir, f"subtitle.{subtitle.get('format', 'vtt')}")
                subtitle_downloaded = False
                try:
                    stream_download(subtitle.get("url"), subtitle_path, headers=headers_required)
                    subtitle_downloaded = True
                except httpx.HTTPStatusError as subtitle_exc:
                    status_code = subtitle_exc.response.status_code if subtitle_exc.response is not None else None
                    if status_code in {401, 403, 404, 410, 429}:
                        logger.warning(
                            "Skipping subtitle download for job %s due to HTTP %s from %s",
                            job_id,
                            status_code,
                            subtitle.get("url"),
                        )
                    else:
                        raise
                except httpx.HTTPError as subtitle_exc:
                    logger.warning("Skipping subtitle download for job %s due to subtitle fetch error: %s", job_id, subtitle_exc)

                if subtitle_downloaded and job.subtitle_embed:
                    embed_mode = "soft"
                    if subtitle_embed_mode in {"soft", "hard"}:
                        embed_mode = subtitle_embed_mode
                    temp_path = ffmpeg.embed_subtitles(temp_path, subtitle_path, output_path, embed_mode=embed_mode)

        final_is_image_output = is_image_content or selected_output_is_image

        if not final_is_image_output:
            cleaned_metadata = metadata.extract_platform_metadata(media_info)
            thumbnail_path = metadata.download_thumbnail(media_info.get("thumbnail"), job_dir)
            ffmpeg.inject_metadata(temp_path, cleaned_metadata, thumbnail_path)

        if media_info.get("chapters") and not final_is_image_output:
            base, ext = os.path.splitext(temp_path)
            chaptered_path = f"{base}_chaptered{ext}"
            temp_path = ffmpeg.embed_chapters(temp_path, media_info.get("chapters"), chaptered_path)

        update_progress(job_id, 100, 0)
        job.file_size_bytes = os.path.getsize(temp_path) if os.path.exists(temp_path) else None
        signed_url, expiry = storage.make_signed_url(job_id, os.path.basename(temp_path))
        job.download_url = signed_url
        job.download_url_expires_at = expiry
        job.status = "complete"
        job.progress_pct = 100
        job.speed_bps = None
        job.eta_seconds = None
        job.completed_at = datetime.utcnow()
        db.session.commit()

        if job.batch_id:
            batch_status_update_task.delay(str(job.batch_id))

        user = User.query.get(job.user_id) if job.user_id else None
        if user and user.email_notifications:
            send_download_complete_email(user.id, job_id)

        AuditLog.log(action="download_complete", user_id=job.user_id, resource_id=job.id)
        return temp_path
    except Exception as exc:
        job.status = "failed"
        job.error_message = _friendly_error(exc)
        db.session.commit()
        if job.batch_id:
            batch_status_update_task.delay(str(job.batch_id))
        if isinstance(exc, ExtractorError):
            send_download_failed_email(job.user_id, job_id, job.error_message)
            raise Ignore() from exc
        if isinstance(exc, RuntimeError):
            send_download_failed_email(job.user_id, job_id, job.error_message)
            raise Ignore() from exc
        if isinstance(exc, httpx.HTTPError):
            raise self.retry(exc=exc, countdown=30)
        raise


def update_progress(job_id: str, pct: float, speed_bps: float = 0):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return
    job.progress_pct = int(pct)
    job.speed_bps = int(speed_bps) if speed_bps else job.speed_bps
    if job.file_size_bytes and job.speed_bps:
        remaining = job.file_size_bytes * (1 - job.progress_pct / 100)
        job.eta_seconds = int(remaining / job.speed_bps) if job.speed_bps else None
    db.session.commit()


def stream_download(url: str, output_path: str, headers: dict = None, expected_size: int = None, progress_callback=None):
    url = _normalize_remote_url(url)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    client_headers = headers.copy() if headers else {}
    if "User-Agent" not in client_headers:
        client_headers["User-Agent"] = BaseExtractor.USER_AGENT_POOL[0]
    if "Accept" not in client_headers:
        client_headers["Accept"] = "*/*"
    if "Accept-Language" not in client_headers:
        client_headers["Accept-Language"] = "en-US,en;q=0.9"

    timeout = httpx.Timeout(30, read=300)
    last_update = time.time()
    last_reported_downloaded = 0
    total = expected_size
    last_error = None

    for attempt in range(1, STREAM_DOWNLOAD_MAX_ATTEMPTS + 1):
        resume_from = _safe_file_size(output_path)
        request_headers = client_headers.copy()
        file_mode = "ab" if resume_from > 0 else "wb"
        if resume_from > 0:
            request_headers["Range"] = f"bytes={resume_from}-"

        downloaded = resume_from
        chunks = 0

        try:
            with httpx.Client(timeout=timeout, headers=request_headers, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    if resume_from > 0 and response.status_code == 200:
                        _safe_remove_file(output_path)
                        resume_from = 0
                        downloaded = 0
                        file_mode = "wb"
                    elif response.status_code == 416 and resume_from > 0:
                        if progress_callback:
                            progress_callback(100, 0)
                        return output_path

                    response.raise_for_status()

                    total = _derive_total_size(response, resume_from, expected_size)
                    with open(output_path, file_mode) as handle:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if not chunk:
                                continue

                            handle.write(chunk)
                            downloaded += len(chunk)
                            chunks += 1

                            now = time.time()
                            if progress_callback and (chunks % 10 == 0 or now - last_update >= 1):
                                elapsed = now - last_update
                                speed = (downloaded - last_reported_downloaded) / elapsed if elapsed > 0 else 0
                                pct = (downloaded / total) * 100 if total else 0
                                if total and pct > 100:
                                    pct = 100
                                progress_callback(pct, speed)
                                last_update = now
                                last_reported_downloaded = downloaded

            final_size = _safe_file_size(output_path)
            if total and final_size < total:
                raise httpx.ReadError(
                    f"incomplete response body received {final_size} bytes expected {total}",
                    request=None,
                )

            if progress_callback:
                progress_callback(100, 0)
            return output_path

        except httpx.HTTPStatusError:
            raise
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = exc
            logger.warning(
                "Transient stream download error for %s on attempt %s of %s: %s",
                url,
                attempt,
                STREAM_DOWNLOAD_MAX_ATTEMPTS,
                exc,
            )
            if attempt >= STREAM_DOWNLOAD_MAX_ATTEMPTS:
                break
            backoff_seconds = STREAM_DOWNLOAD_RETRY_BACKOFF_SECONDS * attempt
            time.sleep(backoff_seconds)

    if last_error:
        raise last_error

    raise RuntimeError("Download failed before receiving response body")


def _safe_file_size(path: str) -> int:
    try:
        if os.path.exists(path):
            return os.path.getsize(path)
    except OSError:
        return 0
    return 0


def _safe_remove_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        return


def _derive_total_size(response: httpx.Response, resume_from: int, expected_size: int = None):
    content_range = str(response.headers.get("Content-Range") or "").strip()
    if content_range:
        match = re.search(r"/\s*(\d+)\s*$", content_range)
        if match:
            return int(match.group(1))

    content_length = str(response.headers.get("Content-Length") or "").strip()
    if content_length.isdigit():
        length = int(content_length)
        if response.status_code == 206:
            return resume_from + length
        return length

    return expected_size


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, httpx.RemoteProtocolError):
        return "Source closed connection before transfer completed. Please retry."
    if isinstance(exc, httpx.TimeoutException):
        return "Download timed out. Please try again."
    if isinstance(exc, httpx.ConnectError):
        return "Could not connect to the media source."
    if isinstance(exc, ValueError) and "DRM" in str(exc):
        return "This content is DRM-protected and cannot be downloaded."
    if isinstance(exc, RuntimeError):
        details = str(exc).lower()
        if (
            "invalid data found when processing input" in details
            or "moov atom not found" in details
            or "input media file is invalid or incomplete" in details
            or "downloaded media file is invalid or incomplete" in details
        ):
            return "Source returned an invalid media file. Please try another quality or retry later."
        if "playable video stream" in details:
            return "Source returned audio only media for this quality. Please choose another quality and try again."
        if "does not contain an audio stream" in details:
            return "Source did not provide audio in this selected stream. Please retry with another quality."
        return "Media processing failed. Format may be unsupported."
    if isinstance(exc, OSError):
        return "Server storage error. Please try again."
    return str(exc) if str(exc) else "Download failed. Please try again."


@celery.task(name="tasks.cleanup_files")
def cleanup_file_task():
    cleaned = storage.purge_expired_files()
    expired_jobs = DownloadJob.query.filter(
        DownloadJob.download_url_expires_at.isnot(None),
        DownloadJob.download_url_expires_at < datetime.utcnow(),
    ).all()
    for job in expired_jobs:
        job.download_url = None
        job.download_url_expires_at = None
    db.session.commit()
    logger.info("Cleaned %s expired download directories", cleaned)
    return cleaned


@celery.task(name="tasks.delete_job_files")
def delete_job_files_task(job_id: str):
    return storage.delete_job_files(job_id)


@celery.task(name="tasks.batch_status_update")
def batch_status_update_task(batch_id: str):
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        return {}
    batch = BatchQueue.query.get(batch_uuid)
    if not batch:
        return {}

    completed = DownloadJob.query.filter_by(batch_id=batch.id, status="complete").count()
    failed = DownloadJob.query.filter_by(batch_id=batch.id, status="failed").count()
    batch.completed_jobs = completed
    batch.failed_jobs = failed
    total_done = completed + failed

    if total_done >= batch.total_jobs and batch.total_jobs > 0:
        batch.status = "complete" if failed == 0 else "partial"
        batch.completed_at = datetime.utcnow()
        if completed > 0:
            job_ids = [str(job.id) for job in DownloadJob.query.filter_by(batch_id=batch.id, status="complete").all()]
            storage.create_zip_archive(job_ids, str(batch.id))
            batch.zip_url = f"/download/batch/zip/{batch.id}"
            batch.zip_expires_at = datetime.utcnow() + timedelta(seconds=3600)
        send_batch_complete_email(batch.user_id, batch_id)
    elif total_done > 0:
        batch.status = "in_progress"

    db.session.commit()
    return {
        "batch_id": str(batch.id),
        "status": batch.status,
        "completed_jobs": batch.completed_jobs,
        "failed_jobs": batch.failed_jobs,
    }


def _safe_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return list(value)


def _unique_list(items):
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def get_latest_content_ids(platform: str, channel_url: str) -> list:
    """
    Returns list of most recent content IDs for a channel or feed.
    Returns at most 10 IDs.
    """
    platform_id = (platform or "generic").lower()
    timeout = httpx.Timeout(10.0, read=10.0)
    try:
        if platform_id == "youtube":
            from app.extractors.youtube import YoutubeExtractor

            extractor = YoutubeExtractor(proxy_pool=proxy_pool)
            info = extractor.extract_info(
                channel_url,
                option_overrides={
                    "extract_flat": "in_playlist",
                    "skip_download": True,
                    "playlistend": 10,
                    "socket_timeout": 8,
                    "noplaylist": False,
                },
            )
            entries = info.get("entries") or []
            ids = [entry.get("id") for entry in entries if entry.get("id")]
            return ids[:10]

        if platform_id == "soundcloud":
            from app.extractors.soundcloud import SoundCloudExtractor

            extractor = SoundCloudExtractor(proxy_pool=proxy_pool)
            return extractor.get_latest_content_ids(channel_url, limit=10)

        if platform_id == "twitch":
            match = re.search(r"twitch\.tv/([^/]+)", channel_url, re.IGNORECASE)
            login = match.group(1) if match else ""
            if not login:
                raise ExtractorError("Twitch channel not found", platform=platform_id, url=channel_url)
            query = (
                "query { user(login: \""
                + login
                + "\") { videos(first: 10, sort: TIME) { edges { node { id } } } } }"
            )
            payload = {"query": query}
            headers = {"Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko"}
            with httpx.Client(timeout=timeout) as client:
                response = client.post("https://gql.twitch.tv/gql", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json().get("data", {}).get("user") or {}
            edges = (data.get("videos") or {}).get("edges") or []
            return [edge.get("node", {}).get("id") for edge in edges if edge.get("node", {}).get("id")][:10]

        if platform_id == "reddit":
            json_url = channel_url.rstrip("/") + ".json?limit=10"
            headers = {"User-Agent": "UniversalDL/1.0"}
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = client.get(json_url)
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, list):
                children = payload[0].get("data", {}).get("children", [])
            else:
                children = payload.get("data", {}).get("children", [])
            ids = []
            for item in children:
                data = item.get("data", {})
                name = data.get("name") or data.get("id")
                if name:
                    ids.append(name)
            return ids[:10]

        if platform_id == "bilibili":
            match = re.search(r"space\.bilibili\.com/(\d+)", channel_url)
            uid = match.group(1) if match else ""
            if not uid:
                raise ExtractorError("Bilibili user id not found", platform=platform_id, url=channel_url)
            with httpx.Client(timeout=timeout) as client:
                response = client.get(
                    "https://api.bilibili.com/x/space/arc/search",
                    params={"mid": uid, "ps": 10},
                )
                response.raise_for_status()
                data = response.json().get("data", {}).get("list", {}).get("vlist", [])
            ids = [item.get("bvid") for item in data if item.get("bvid")]
            return ids[:10]

        if platform_id == "spotify":
            match = re.search(r"open\.spotify\.com/show/([A-Za-z0-9]+)", channel_url)
            show_id = match.group(1) if match else ""
            show_url = f"https://open.spotify.com/show/{show_id}" if show_id else channel_url
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                html = client.get(show_url).text
            ids = re.findall(r"spotify:episode:([A-Za-z0-9]+)", html)
            return _unique_list(ids)[:10]

        if platform_id == "generic":
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(channel_url)
                response.raise_for_status()
                root = ET.fromstring(response.text)
            ids = []
            for item in root.findall(".//item"):
                link = item.findtext("link")
                if link:
                    ids.append(link)
            for entry in root.findall(".//entry"):
                entry_id = entry.findtext("id") or entry.findtext("link")
                if entry_id:
                    ids.append(entry_id)
            return _unique_list(ids)[:10]

        return []
    except Exception as exc:
        raise ExtractorError(
            "Could not fetch content list for " + platform_id + ": " + str(exc),
            platform=platform_id,
            url=channel_url,
        ) from exc


def build_content_url(platform: str, content_id: str) -> str:
    platform_id = (platform or "").lower()
    if platform_id == "youtube":
        return "https://www.youtube.com/watch?v=" + content_id
    if platform_id == "soundcloud":
        value = str(content_id or "").strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return "https://api.soundcloud.com/tracks/" + content_id
    if platform_id == "twitch":
        return "https://www.twitch.tv/videos/" + content_id
    if platform_id == "reddit":
        return "https://www.reddit.com/" + content_id
    if platform_id == "bilibili":
        return "https://www.bilibili.com/video/" + content_id
    if platform_id == "spotify":
        return "https://open.spotify.com/episode/" + content_id
    if platform_id == "generic":
        return content_id
    return content_id


@celery.task(name="tasks.subscription_poll", bind=True, max_retries=2)
def subscription_poll_task(self, subscription_id: str = None, force: bool = False):
    """
    Polls subscribed channels for new content and queues downloads.
    Can be called for a single subscription_id or for all due subscriptions.
    force=True bypasses the next_check_at time check.
    """
    subs_to_process = []

    if subscription_id:
        try:
            sub_uuid = uuid.UUID(subscription_id)
        except ValueError:
            sub_uuid = None
        if sub_uuid:
            sub = Subscription.query.get(sub_uuid)
            if sub:
                subs_to_process = [sub]
    else:
        subs_to_process = (
            Subscription.query.filter(
                Subscription.is_active.is_(True),
                or_(Subscription.next_check_at.is_(None), Subscription.next_check_at <= datetime.utcnow()),
            ).all()
        )

    for sub in subs_to_process:
        try:
            if not sub or not sub.is_active:
                continue
            if not force and sub.next_check_at and sub.next_check_at > datetime.utcnow():
                continue

            latest_ids = get_latest_content_ids(sub.platform, sub.channel_url)
            latest_ids = _unique_list(latest_ids)
            known_ids = _safe_list(sub.known_content_ids)
            new_ids = [item for item in latest_ids if item not in known_ids]

            if not new_ids:
                sub.last_checked_at = datetime.utcnow()
                sub.next_check_at = sub.calculate_next_check()
                db.session.commit()
                continue

            new_download_count = 0
            jobs = []
            for content_id in new_ids[:5]:
                content_url = build_content_url(sub.platform, content_id)
                job = DownloadJob(
                    user_id=sub.user_id,
                    url=content_url,
                    platform=sub.platform,
                    selected_quality=sub.quality,
                    selected_format=sub.format,
                    status="queued",
                )
                db.session.add(job)
                jobs.append(job)
                new_download_count += 1

            db.session.flush()
            for job in jobs:
                analyze_url_task.delay(str(job.id))

            known_ids.extend(new_ids)
            sub.known_content_ids = known_ids[-500:]
            sub.last_checked_at = datetime.utcnow()
            sub.next_check_at = sub.calculate_next_check()
            if new_download_count > 0:
                sub.last_download_at = datetime.utcnow()
                sub.total_downloaded = (sub.total_downloaded or 0) + new_download_count
            db.session.commit()

            if new_download_count > 0 and sub.notification_email:
                send_subscription_new_content_email(str(sub.user_id), str(sub.id), new_download_count)
        except ExtractorError as exc:
            logger.error("Subscription poll failed for %s: %s", sub.id, exc, exc_info=True)
            try:
                sub.last_checked_at = datetime.utcnow()
                sub.next_check_at = sub.calculate_next_check()
                db.session.commit()
            except Exception:
                db.session.rollback()
        except Exception as exc:
            logger.error("Subscription poll error for %s: %s", sub.id, exc, exc_info=True)
            db.session.rollback()


@celery.task(name="tasks.extractor_health_check")
def extractor_health_check_task():
    """
    Runs a test extraction for each enabled PlatformExtractor record.
    Updates last_success_at, last_failure_at, failure_reason, success_rate_7d.
    Each extractor is tested independently - one failure does not stop others.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
    from datetime import datetime, timedelta
    import importlib

    from app.extensions import db
    from app.models.download_job import DownloadJob
    from app.models.extractor import PlatformExtractor
    from app.services import dispatcher

    extractors = PlatformExtractor.query.filter_by(is_enabled=True).all()
    logger.info("Starting health check for %s extractors", len(extractors))

    success_count = 0
    fail_count = 0

    for extractor in extractors:
        try:
            test_url = None
            if extractor.config_json and isinstance(extractor.config_json, dict):
                test_url = extractor.config_json.get("test_url") or extractor.config_json.get("TEST_URL")

            if not test_url:
                module_paths = []
                if extractor.extractor_module:
                    module_paths.append(extractor.extractor_module)
                    if extractor.extractor_module.startswith("app.services.extractors."):
                        module_paths.append(
                            extractor.extractor_module.replace("app.services.extractors.", "app.extractors.")
                        )
                else:
                    module_paths.append(f"app.extractors.{extractor.platform_id}")

                for module_path in module_paths:
                    try:
                        module = importlib.import_module(module_path)
                    except Exception:
                        continue
                    extractor_cls = None
                    for attr in dir(module):
                        obj = getattr(module, attr)
                        if isinstance(obj, type) and getattr(obj, "PLATFORM_ID", None) == extractor.platform_id:
                            extractor_cls = obj
                            break
                    if extractor_cls and getattr(extractor_cls, "TEST_URL", None):
                        test_url = extractor_cls.TEST_URL
                        break

            if not test_url:
                logger.debug("Skipping extractor %s: no test URL", extractor.platform_id)
                continue

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        dispatcher.dispatch_and_extract,
                        extractor.platform_id,
                        test_url,
                    )
                    future.result(timeout=45)

                extractor.last_success_at = datetime.utcnow()
                extractor.failure_reason = None
                success_count += 1
                logger.info("%s: health check PASSED", extractor.platform_id)
            except (FuturesTimeoutError, Exception) as exc:
                extractor.last_failure_at = datetime.utcnow()
                extractor.failure_reason = str(exc)[:500]
                fail_count += 1
                logger.warning("%s: health check FAILED: %s", extractor.platform_id, exc)

            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            total_7d = DownloadJob.query.filter(
                DownloadJob.platform == extractor.platform_id,
                DownloadJob.created_at >= seven_days_ago,
            ).count()
            failed_7d = DownloadJob.query.filter(
                DownloadJob.platform == extractor.platform_id,
                DownloadJob.created_at >= seven_days_ago,
                DownloadJob.status == "failed",
            ).count()

            if total_7d > 0:
                success_rate = ((total_7d - failed_7d) / total_7d) * 100
                extractor.success_rate_7d = round(success_rate, 2)

            db.session.commit()
        except Exception as exc:
            logger.error("Health check error for %s: %s", extractor.platform_id, exc, exc_info=True)
            db.session.rollback()

    logger.info("Health check complete: %s passed, %s failed", success_count, fail_count)
    return {
        "checked": len(extractors),
        "passed": success_count,
        "failed": fail_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


@celery.task(name="tasks.purge_old_url_logs")
def purge_old_url_logs_task():
    """
    Privacy cleanup: hash IP addresses in audit_logs older than 30 days.
    Null out URL fields in download_jobs older than 90 days for non-anonymous users.
    """
    import hashlib
    from datetime import datetime, timedelta

    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.download_job import DownloadJob

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)

    old_logs = (
        AuditLog.query.filter(
            AuditLog.created_at < thirty_days_ago,
            AuditLog.ip_address.isnot(None),
            ~AuditLog.ip_address.startswith("hashed:"),
        ).all()
    )
    for log_entry in old_logs:
        hashed = hashlib.sha256(log_entry.ip_address.encode("utf-8")).hexdigest()[:16]
        log_entry.ip_address = "hashed:" + hashed
    db.session.commit()
    logger.info("Hashed %s IP addresses in audit logs", len(old_logs))

    old_jobs = (
        DownloadJob.query.filter(
            DownloadJob.created_at < ninety_days_ago,
            DownloadJob.url.isnot(None),
        ).all()
    )
    for job in old_jobs:
        job.url = None
    db.session.commit()
    logger.info("Cleared URLs from %s old download records", len(old_jobs))

    return {"ip_hashed": len(old_logs), "urls_cleared": len(old_jobs)}


@celery.task(name="tasks.calculate_success_rates")
def calculate_success_rates_task():
    """Recalculates success_rate_7d for all extractors from real download data."""
    from datetime import datetime, timedelta

    from app.extensions import db
    from app.models.download_job import DownloadJob
    from app.models.extractor import PlatformExtractor

    extractors = PlatformExtractor.query.all()
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    updated = 0

    for extractor in extractors:
        total_7d = DownloadJob.query.filter(
            DownloadJob.platform == extractor.platform_id,
            DownloadJob.created_at >= seven_days_ago,
        ).count()
        failed_7d = DownloadJob.query.filter(
            DownloadJob.platform == extractor.platform_id,
            DownloadJob.created_at >= seven_days_ago,
            DownloadJob.status == "failed",
        ).count()
        if total_7d > 0:
            success_rate = ((total_7d - failed_7d) / total_7d) * 100
            extractor.success_rate_7d = round(success_rate, 2)
        db.session.commit()
        updated += 1

    return updated
