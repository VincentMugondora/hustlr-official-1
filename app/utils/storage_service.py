import os
import uuid
import boto3
from datetime import datetime
from typing import Optional
from config import settings


class StorageService:
    def __init__(self) -> None:
        self.provider = getattr(settings, 'FILE_STORAGE_PROVIDER', 'local')
        self.bucket = getattr(settings, 'AWS_S3_BUCKET', None)
        self.public_base = getattr(settings, 'AWS_S3_PUBLIC_BASE_URL', None)
        self.s3 = None
        if self.provider == 's3':
            self.s3 = boto3.client('s3')
        self.local_root = os.path.abspath(os.path.join(os.getcwd(), 'uploads'))
        os.makedirs(self.local_root, exist_ok=True)

    def _make_key(self, prefix: str, ext: str) -> str:
        dt = datetime.utcnow().strftime('%Y/%m/%d')
        uid = uuid.uuid4().hex
        ext = (ext or '').lstrip('.')
        name = f"{uid}.{ext}" if ext else uid
        prefix = prefix.strip('/')</n        return f"{prefix}/{dt}/{name}"

    def upload_bytes(self, data: bytes, content_type: Optional[str] = None, prefix: str = 'media', ext: Optional[str] = None) -> str:
        if not data:
            raise ValueError('No data to upload')
        # Infer extension from content_type
        if not ext and content_type:
            if 'jpeg' in content_type:
                ext = 'jpg'
            elif 'png' in content_type:
                ext = 'png'
            elif 'pdf' in content_type:
                ext = 'pdf'
            elif 'webp' in content_type:
                ext = 'webp'
        key = self._make_key(prefix, ext or 'bin')

        if self.provider == 's3':
            if not self.bucket:
                raise RuntimeError('AWS_S3_BUCKET not configured')
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type or 'application/octet-stream', ACL='public-read')
            if self.public_base:
                return f"{self.public_base.rstrip('/')}/{key}"
            return f"https://{self.bucket}.s3.amazonaws.com/{key}"

        # Local fallback
        abs_path = os.path.join(self.local_root, key)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'wb') as f:
            f.write(data)
        # Return a file URL path; adjust if a static server is added
        return f"file://{abs_path}"
