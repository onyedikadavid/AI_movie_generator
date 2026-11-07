import logging
import mimetypes
import os
import time
from app.core.config import settings

logger = logging.getLogger(__name__)


def _cloudinary_configured() -> bool:
    return bool(
        settings.CLOUDINARY_CLOUD_NAME
        and settings.CLOUDINARY_API_KEY
        and settings.CLOUDINARY_API_SECRET
    )


def _s3_configured() -> bool:
    return all([
        settings.S3_BUCKET,
        settings.S3_ACCESS_KEY_ID,
        settings.S3_SECRET_ACCESS_KEY,
        settings.S3_PUBLIC_BASE_URL,
    ])


def _upload_cloudinary(local_path: str, key: str) -> str:
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    # Cloudinary wants a public_id without the file extension - it appends
    # its own based on the detected resource type (image vs video).
    public_id = key.rsplit(".", 1)[0]
    big = os.path.getsize(local_path) > 20 * 1024 * 1024
    if big:
        # Chunked upload: survives flaky connections far better than one long POST
        # (a single 100 MB POST is what died with "ConnectionAbortedError 10053").
        result = cloudinary.uploader.upload_large(
            local_path, public_id=public_id, resource_type="video",
            overwrite=True, chunk_size=6 * 1024 * 1024, timeout=600,
        )
    else:
        result = cloudinary.uploader.upload(
            local_path, public_id=public_id, resource_type="auto",
            overwrite=True, timeout=600,
        )
    return result["secure_url"]


def _upload_s3(local_path: str, key: str) -> str:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION or "auto",
    )
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    client.upload_file(local_path, settings.S3_BUCKET, key, ExtraArgs={"ContentType": content_type})
    return f"{settings.S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"


def _is_url(v: str) -> bool:
    return v.startswith("http://") or v.startswith("https://")


def publish_file_strict(local_path: str, key: str, attempts: int = 5) -> str:
    """Like publish_file but retries with back-off and RAISES if a remote backend is
    configured and every attempt fails. Used for the final video, where silently keeping
    a local path gives a player stuck at 0:00."""
    if not (_cloudinary_configured() or _s3_configured()):
        return local_path
    last = None
    for i in range(attempts):
        try:
            if _cloudinary_configured():
                return _upload_cloudinary(local_path, key)
            return _upload_s3(local_path, key)
        except Exception as e:  # noqa: BLE001
            last = e
            wait = min(60, 5 * (2 ** i))
            logger.warning("Upload of %s failed (attempt %d/%d): %s - retrying in %ds",
                           key, i + 1, attempts, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"Could not upload {key} after {attempts} attempts: {last}")


def publish_file(local_path: str, key: str) -> str:
    """
    Uploads a generated file to whichever media backend is configured
    (Cloudinary takes priority if both are set) and returns its public URL -
    which is what gets stored in the database and used by the frontend. If
    neither is configured, or the upload fails for any reason, returns
    local_path unchanged so the pipeline still completes (the frontend then
    serves it from the API's own /storage mount, which only works if the API
    can see that same file on disk - fine for local-only setups, not for a
    worker/API split like PC + Render). Uploads never abort a generation run.
    """
    if _cloudinary_configured():
        try:
            return _upload_cloudinary(local_path, key)
        except ImportError:
            logger.warning("Cloudinary is configured but the 'cloudinary' package isn't installed - keeping local path.")
            return local_path
        except Exception as e:
            logger.warning(f"Cloudinary upload of {key} failed ({e}) - keeping local path instead.")
            return local_path

    if _s3_configured():
        try:
            return _upload_s3(local_path, key)
        except ImportError:
            logger.warning("S3 storage is configured but boto3 isn't installed - keeping local path.")
            return local_path
        except Exception as e:
            logger.warning(f"S3 upload of {key} failed ({e}) - keeping local path instead.")
            return local_path

    return local_path
