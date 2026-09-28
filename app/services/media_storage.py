import logging
import mimetypes
from app.core.config import settings

logger = logging.getLogger(__name__)


def _configured() -> bool:
    return all([
        settings.S3_BUCKET,
        settings.S3_ACCESS_KEY_ID,
        settings.S3_SECRET_ACCESS_KEY,
        settings.S3_PUBLIC_BASE_URL,
    ])


def publish_file(local_path: str, key: str) -> str:
    """
    Uploads a generated file to S3-compatible storage and returns its public
    URL - which is what gets stored in the database and used by the frontend.
    If storage isn't configured, or the upload fails, returns local_path
    unchanged so the pipeline still completes (the frontend then serves it
    from the API's own /storage mount, which only works if the API can see
    that same file on disk). Uploads never abort a generation run.
    """
    if not _configured():
        return local_path

    try:
        import boto3
    except ImportError:
        logger.warning("S3 storage is configured but boto3 isn't installed - keeping local path.")
        return local_path

    try:
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
    except Exception as e:
        logger.warning(f"Upload of {key} failed ({e}) - keeping local path instead.")
        return local_path
