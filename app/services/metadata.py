import os
import re
from datetime import datetime
from typing import Optional

import httpx
from PIL import Image


def download_thumbnail(thumbnail_url: str, save_dir: str) -> Optional[str]:
    if not thumbnail_url:
        return None
    try:
        response = httpx.get(thumbnail_url, timeout=20)
        response.raise_for_status()
        os.makedirs(save_dir, exist_ok=True)
        output_path = os.path.join(save_dir, "thumbnail.jpg")
        with open(output_path, "wb") as handle:
            handle.write(response.content)
        image = Image.open(output_path)
        image.thumbnail((500, 500))
        image.convert("RGB").save(output_path, "JPEG", quality=85)
        return output_path
    except Exception:
        return None


def extract_platform_metadata(media_info: dict) -> dict:
    title = media_info.get("title") or "media"
    description = media_info.get("description") or ""
    upload_date = media_info.get("upload_date")

    title = _sanitize_text(title)
    description = description[:1000]

    if upload_date:
        try:
            upload_date = datetime.strptime(upload_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            upload_date = None

    return {
        "title": title,
        "author": media_info.get("author"),
        "description": description,
        "upload_date": upload_date,
    }


def generate_filename(media_info: dict, quality_label: str, output_format: str) -> str:
    title = media_info.get("title") or "media"
    title = _sanitize_text(title)
    title = title.replace(" ", "_")
    title = re.sub(r"[^A-Za-z0-9_-]+", "", title)
    title = title[:80]
    label = quality_label or "best"
    label = re.sub(r"[^A-Za-z0-9_-]+", "", label)
    return f"{title}_{label}.{output_format}"


def _sanitize_text(value: str) -> str:
    if not value:
        return "media"
    return re.sub(r"[\\/:*?\"<>|]", "_", value).strip()
