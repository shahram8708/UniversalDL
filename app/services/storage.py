import os
import shutil
import zipfile
from datetime import datetime, timedelta

from flask import current_app
from itsdangerous import URLSafeTimedSerializer


def get_job_dir(job_id: str) -> str:
    base_dir = current_app.config.get("TEMP_DOWNLOAD_DIR")
    path = os.path.join(base_dir, str(job_id))
    os.makedirs(path, exist_ok=True)
    return path


def get_output_path(job_id: str, filename: str) -> str:
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Invalid filename")
    return os.path.join(get_job_dir(job_id), filename)


def make_signed_url(job_id: str, filename: str, expires_in: int = 3600):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    token = serializer.dumps({"job_id": job_id, "filename": filename})
    url = f"/download/file/{token}"
    expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    return url, expiry


def verify_signed_url(token: str):
    serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    max_age = current_app.config.get("MAX_FILE_AGE_SECONDS")
    try:
        data = serializer.loads(token, max_age=max_age)
        return data.get("job_id"), data.get("filename")
    except Exception:
        return None


def delete_job_files(job_id: str) -> bool:
    base_dir = current_app.config.get("TEMP_DOWNLOAD_DIR")
    path = os.path.join(base_dir, str(job_id))
    if not os.path.exists(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def purge_expired_files(max_age_seconds: int = None) -> int:
    base_dir = current_app.config.get("TEMP_DOWNLOAD_DIR")
    max_age = max_age_seconds or current_app.config.get("MAX_FILE_AGE_SECONDS")
    now = datetime.utcnow().timestamp()
    deleted = 0
    if not os.path.exists(base_dir):
        return 0
    for entry in os.listdir(base_dir):
        path = os.path.join(base_dir, entry)
        if not os.path.isdir(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if now - mtime > max_age:
            if delete_job_files(entry):
                deleted += 1
    return deleted


def get_temp_dir_size() -> int:
    base_dir = current_app.config.get("TEMP_DOWNLOAD_DIR")
    total = 0
    for root, _, files in os.walk(base_dir):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def create_zip_archive(job_ids: list, batch_id: str) -> str:
    base_dir = current_app.config.get("TEMP_DOWNLOAD_DIR")
    zip_path = os.path.join(base_dir, f"batch_{batch_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for job_id in job_ids:
            job_dir = os.path.join(base_dir, str(job_id))
            if not os.path.exists(job_dir):
                continue
            for root, _, files in os.walk(job_dir):
                for name in files:
                    file_path = os.path.join(root, name)
                    arcname = os.path.relpath(file_path, base_dir)
                    try:
                        zipf.write(file_path, arcname)
                    except OSError:
                        continue
    return zip_path
