import re
from pathlib import Path
from app.errors import MallError

MEDIA_NAME = re.compile(r'[a-f0-9]{64}\.(png|jpg|webp)')
CONTENT_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}


def media_file(root: Path, filename: str):
    if not MEDIA_NAME.fullmatch(filename):
        raise MallError(404, '图片不存在')
    path = (root / filename).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise MallError(404, '图片不存在')
    return path


def media_bytes(storage, filename: str):
    """Read an immutable image from object storage (MinIO or local fallback)."""
    match = MEDIA_NAME.fullmatch(filename)
    if not match:
        raise MallError(404, '图片不存在')
    content = storage.get(filename)
    if content is None:
        raise MallError(404, '图片不存在')
    return content, CONTENT_TYPES[match.group(1)]
