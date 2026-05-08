import logging
import os
import re
import tempfile
import uuid
from typing import Optional

import ffmpeg
import httpx
import m3u8
from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TDRC, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from app.extractors.base import BaseExtractor


logger = logging.getLogger(__name__)


def detect_format(file_path: str) -> str:
    try:
        probe = ffmpeg.probe(file_path)
        return (probe.get("format") or {}).get("format_name", "unknown")
    except ffmpeg.Error:
        return "unknown"


def get_duration(file_path: str) -> float:
    try:
        probe = ffmpeg.probe(file_path)
        duration = (probe.get("format") or {}).get("duration")
        return float(duration) if duration else 0.0
    except ffmpeg.Error:
        return 0.0


def has_audio_stream(file_path: str) -> bool:
    try:
        probe = ffmpeg.probe(file_path)
    except ffmpeg.Error:
        return False

    for stream in probe.get("streams") or []:
        if (stream.get("codec_type") or "").lower() == "audio":
            return True
    return False


def has_video_stream(file_path: str) -> bool:
    try:
        probe = ffmpeg.probe(file_path)
    except ffmpeg.Error:
        return False

    for stream in probe.get("streams") or []:
        if (stream.get("codec_type") or "").lower() != "video":
            continue

        disposition = stream.get("disposition") or {}
        attached_pic_value = disposition.get("attached_pic")
        if str(attached_pic_value) == "1":
            continue

        codec_name = str(stream.get("codec_name") or "").strip().lower()
        if codec_name in {"mjpeg", "jpeg2000", "png", "bmp", "gif", "webp"}:
            continue

        width = stream.get("width") or 0
        height = stream.get("height") or 0
        if not width or not height:
            continue

        avg_frame_rate = _rate_to_float(stream.get("avg_frame_rate"))
        real_frame_rate = _rate_to_float(stream.get("r_frame_rate"))
        effective_fps = avg_frame_rate or real_frame_rate

        if effective_fps and effective_fps > 0:
            return True

        duration_value = stream.get("duration")
        nb_frames_value = stream.get("nb_frames")
        try:
            if nb_frames_value is not None and int(str(nb_frames_value)) > 1:
                return True
        except (TypeError, ValueError):
            pass
        try:
            if duration_value is not None and float(str(duration_value)) > 1.0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _rate_to_float(value) -> float:
    if value in {None, "", "0/0"}:
        return 0.0
    text = str(value)
    if "/" not in text:
        try:
            return float(text)
        except (TypeError, ValueError):
            return 0.0
    num_text, den_text = text.split("/", 1)
    try:
        num = float(num_text)
        den = float(den_text)
        if den == 0:
            return 0.0
        return num / den
    except (TypeError, ValueError):
        return 0.0


def stitch_hls(
    manifest_url: str,
    output_path: str,
    headers: dict = None,
    progress_callback=None,
    allow_variant_recursion: bool = True,
) -> str:
    total_duration = None
    try:
        response = httpx.get(manifest_url, headers=headers, timeout=20)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "Manifest prefetch failed for %s via httpx, attempting direct ffmpeg input: %s",
            manifest_url,
            exc,
        )
    else:
        content = response.text
        if BaseExtractor.check_drm(content):
            raise ValueError("DRM-protected content cannot be downloaded")

        try:
            playlist = m3u8.loads(content, uri=manifest_url)
        except Exception as exc:
            logger.warning("Failed to parse HLS manifest %s: %s", manifest_url, exc)
            playlist = None

        if playlist and playlist.is_variant and allow_variant_recursion:
            audio_renditions = [
                item
                for item in (playlist.media or [])
                if str(getattr(item, "type", "")).upper() == "AUDIO" and getattr(item, "uri", None)
            ]
            video_variants = list(playlist.playlists or [])

            def is_video_variant(item) -> bool:
                info = item.stream_info or {}
                codecs = str(getattr(info, "codecs", "") or "").lower()
                resolution = getattr(info, "resolution", None)
                if resolution:
                    return True
                if codecs:
                    return any(token in codecs for token in ("avc", "hvc", "hev", "vp9", "vp8", "av01"))
                return False

            def video_score(item) -> tuple:
                info = item.stream_info or {}
                bandwidth = info.bandwidth or 0
                resolution = getattr(info, "resolution", None) or (0, 0)
                return (resolution[1] if isinstance(resolution, tuple) else 0, bandwidth)

            video_candidates = [item for item in video_variants if is_video_variant(item)] or video_variants

            if audio_renditions and video_candidates:
                best_video = max(video_candidates, key=video_score)
                default_audio = next(
                    (item for item in audio_renditions if str(getattr(item, "default", "")).lower() == "yes"),
                    None,
                )
                best_audio = default_audio or audio_renditions[0]

                video_url = best_video.absolute_uri or best_video.uri
                audio_url = best_audio.absolute_uri or best_audio.uri
                if video_url and audio_url:
                    return merge_audio_video(video_url, audio_url, output_path, headers=headers)

            if video_candidates:
                best_video = max(video_candidates, key=video_score)
                video_url = best_video.absolute_uri or best_video.uri
                if video_url:
                    return stitch_hls(
                        video_url,
                        output_path,
                        headers=headers,
                        progress_callback=progress_callback,
                        allow_variant_recursion=False,
                    )

        if playlist:
            total_duration = sum(segment.duration for segment in playlist.segments) or None
    header_string = _build_header_string(headers)
    input_kwargs = {
        "allowed_extensions": "ALL",
        "protocol_whitelist": "file,http,https,tcp,tls,crypto",
    }
    if header_string:
        input_kwargs["headers"] = header_string

    if progress_callback:
        process = (
            ffmpeg.input(manifest_url, **input_kwargs)
            .output(output_path, c="copy", movflags="faststart")
            .overwrite_output()
            .run_async(pipe_stderr=True)
        )
        _track_progress(process, total_duration, progress_callback)
        retcode = process.wait()
        if retcode != 0:
            raise RuntimeError("FFmpeg failed while stitching HLS")
    else:
        try:
            (
                ffmpeg.input(manifest_url, **input_kwargs)
                .output(output_path, c="copy", movflags="faststart")
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        except ffmpeg.Error as exc:
            raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc

    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("HLS output file is empty")
    return output_path


def merge_audio_video(video_url: str, audio_url: str, output_path: str, headers: dict = None) -> str:
    header_string = _build_header_string(headers)
    video_kwargs = {"headers": header_string} if header_string and _is_remote_source(video_url) else {}
    audio_kwargs = {"headers": header_string} if header_string and _is_remote_source(audio_url) else {}
    try:
        video_input = ffmpeg.input(video_url, **video_kwargs)
        audio_input = ffmpeg.input(audio_url, **audio_kwargs)
        (
            ffmpeg.output(
                video_input["v:0"],
                audio_input["a:0"],
                output_path,
                vcodec="copy",
                acodec="aac",
                strict="experimental",
                shortest=None,
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    return output_path


def _is_remote_source(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def convert(input_path: str, target_format: str, output_path: str = None, progress_callback=None) -> str:
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}.{target_format}"

    normalized_format = (target_format or "").lower()
    if normalized_format in {"jpg", "jpeg", "png"}:
        if has_video_stream(input_path):
            return extract_video_frame(input_path, normalized_format, output_path=output_path)
        return convert_image(input_path, normalized_format, output_path=output_path)
    target_format = normalized_format

    if detect_format(input_path) == "unknown":
        raise RuntimeError("Input media file is invalid or incomplete.")

    final_output_path = output_path
    use_temp_output = _same_file_path(input_path, output_path)
    if use_temp_output:
        output_path = _temp_output_path(final_output_path)

    kwargs = {}
    if target_format == "mp4":
        kwargs = {"vcodec": "libx264", "acodec": "aac", "movflags": "faststart", "crf": 23, "preset": "fast"}
    elif target_format == "mkv":
        kwargs = {"vcodec": "copy", "acodec": "copy"}
    elif target_format == "webm":
        kwargs = {"vcodec": "libvpx-vp9", "acodec": "libopus", "crf": 30}
    elif target_format == "mp3":
        kwargs = {"vn": None, "acodec": "libmp3lame", "audio_bitrate": "320k", "map": "0:a:0"}
    elif target_format == "flac":
        kwargs = {"vn": None, "acodec": "flac", "map": "0:a:0"}
    elif target_format == "wav":
        kwargs = {"vn": None, "acodec": "pcm_s16le", "map": "0:a:0"}
    elif target_format == "m4a":
        kwargs = {"vn": None, "acodec": "aac", "audio_bitrate": "256k", "strict": "experimental"}

    try:
        (
            ffmpeg.input(input_path)
            .output(output_path, **kwargs)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        if use_temp_output and os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    if use_temp_output:
        os.replace(output_path, final_output_path)
        return final_output_path
    return output_path


def convert_image(input_path: str, target_format: str, output_path: str = None) -> str:
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}.{target_format}"

    final_output_path = output_path
    use_temp_output = _same_file_path(input_path, output_path)
    if use_temp_output:
        output_path = _temp_output_path(final_output_path)

    save_format = "JPEG" if target_format in {"jpg", "jpeg"} else "PNG"
    try:
        with Image.open(input_path) as image:
            if save_format == "JPEG":
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output_path, format=save_format, quality=95, optimize=True)
            else:
                if image.mode == "P":
                    image = image.convert("RGBA")
                image.save(output_path, format=save_format, optimize=True)
    except Exception as exc:
        if use_temp_output and os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(f"Image conversion failed: {exc}") from exc

    if use_temp_output:
        os.replace(output_path, final_output_path)
        return final_output_path
    return output_path


def extract_video_frame(input_path: str, target_format: str, output_path: str = None) -> str:
    if output_path is None:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}.{target_format}"

    final_output_path = output_path
    use_temp_output = _same_file_path(input_path, output_path)
    if use_temp_output:
        output_path = _temp_output_path(final_output_path)

    frame_time = min(max(get_duration(input_path) * 0.1, 0.0), 5.0)
    output_kwargs = {"vframes": 1}
    if target_format in {"jpg", "jpeg"}:
        output_kwargs["qscale:v"] = 2

    try:
        (
            ffmpeg.input(input_path, ss=frame_time)
            .output(output_path, **output_kwargs)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        if use_temp_output and os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc

    if use_temp_output:
        os.replace(output_path, final_output_path)
        return final_output_path
    return output_path


def embed_subtitles(video_path: str, subtitle_path: str, output_path: str, embed_mode: str = "soft") -> str:
    final_output_path = output_path
    use_temp_output = _same_file_path(video_path, output_path)
    if use_temp_output:
        output_path = _temp_output_path(final_output_path)

    try:
        if embed_mode == "soft":
            ext = os.path.splitext(output_path)[1].lstrip(".").lower()
            subtitle_codec = "mov_text" if ext == "mp4" else "srt"
            input_video = ffmpeg.input(video_path)
            input_subtitle = ffmpeg.input(subtitle_path)
            (
                ffmpeg.output(input_video, input_subtitle, output_path, vcodec="copy", acodec="copy", scodec=subtitle_codec)
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
        else:
            (
                ffmpeg.input(video_path)
                .output(output_path, vf=f"subtitles={subtitle_path}", acodec="copy")
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True)
            )
    except ffmpeg.Error as exc:
        if use_temp_output and os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    if use_temp_output:
        os.replace(output_path, final_output_path)
        return final_output_path
    return output_path


def inject_metadata(file_path: str, media_info: dict, thumbnail_path: str = None) -> str:
    ext = os.path.splitext(file_path)[1].lstrip(".").lower()
    metadata = {
        "title": media_info.get("title"),
        "artist": media_info.get("author"),
        "comment": media_info.get("description"),
        "date": media_info.get("upload_date"),
        "description": media_info.get("description"),
    }

    if ext in {"mp3"}:
        try:
            try:
                audio = ID3(file_path)
            except ID3NoHeaderError:
                audio = ID3()

            if metadata.get("title"):
                audio.add(TIT2(encoding=3, text=metadata.get("title")))
            if metadata.get("artist"):
                audio.add(TPE1(encoding=3, text=metadata.get("artist")))
            if metadata.get("date"):
                audio.add(TDRC(encoding=3, text=metadata.get("date")))
            if metadata.get("comment"):
                audio.add(COMM(encoding=3, lang="eng", desc="desc", text=metadata.get("comment")))
            if thumbnail_path and os.path.exists(thumbnail_path):
                with open(thumbnail_path, "rb") as img:
                    audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img.read()))
            audio.save(file_path)
        except Exception as exc:
            logger.warning("Failed to inject ID3 metadata for %s: %s", file_path, exc)
        return file_path

    if ext in {"m4a", "aac", "mp4"}:
        audio = MP4(file_path)
        if metadata.get("title"):
            audio["\xa9nam"] = metadata.get("title")
        if metadata.get("artist"):
            audio["\xa9ART"] = metadata.get("artist")
        if metadata.get("comment"):
            audio["\xa9cmt"] = metadata.get("comment")
        if metadata.get("date"):
            audio["\xa9day"] = metadata.get("date")
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, "rb") as img:
                audio["covr"] = [MP4Cover(img.read(), imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        return file_path

    output_path = f"{file_path}.meta"
    metadata_args = []
    for key, value in metadata.items():
        if value:
            metadata_args.extend(["-metadata", f"{key}={value}"])
    try:
        (
            ffmpeg.input(file_path)
            .output(output_path, vcodec="copy", acodec="copy")
            .global_args(*metadata_args)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        os.replace(output_path, file_path)
    except ffmpeg.Error as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    return file_path


def embed_chapters(file_path: str, chapters: list, output_path: str) -> str:
    if not chapters:
        return file_path
    content = [";FFMETADATA1"]
    for chapter in chapters:
        start_ms = chapter.get("start_ms") or 0
        title = chapter.get("title") or "Chapter"
        content.append("[CHAPTER]")
        content.append("TIMEBASE=1/1000")
        content.append(f"START={start_ms}")
        content.append(f"END={start_ms + 1}")
        content.append(f"title={title}")
    metadata_text = "\n".join(content)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ini", mode="w", encoding="utf-8") as temp:
        temp.write(metadata_text)
        metadata_file = temp.name

    try:
        (
            ffmpeg.input(file_path)
            .input(metadata_file, f="ffmetadata")
            .output(output_path, vcodec="copy", acodec="copy", map_metadata="1")
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    finally:
        os.unlink(metadata_file)
    return output_path


def extract_thumbnail(file_path: str, output_path: str, timestamp: float = 1.0) -> str:
    try:
        (
            ffmpeg.input(file_path, ss=timestamp)
            .output(output_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        raise RuntimeError(exc.stderr.decode("utf-8", errors="ignore")) from exc
    return output_path


def _build_header_string(headers: dict) -> Optional[str]:
    if not headers:
        return None
    return "".join([f"{key}: {value}\r\n" for key, value in headers.items()])


def _same_file_path(path_a: str, path_b: str) -> bool:
    if not path_a or not path_b:
        return False
    return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))


def _temp_output_path(file_path: str) -> str:
    base, ext = os.path.splitext(file_path)
    return f"{base}.tmp_{uuid.uuid4().hex}{ext}"


def _track_progress(process, total_duration, progress_callback):
    if not total_duration:
        progress_callback(0, 0)
    time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    while True:
        line = process.stderr.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="ignore")
        match = time_re.search(decoded)
        if match and total_duration:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            current = hours * 3600 + minutes * 60 + seconds
            pct = min((current / total_duration) * 100, 100)
            progress_callback(pct, 0)
    progress_callback(100, 0)
