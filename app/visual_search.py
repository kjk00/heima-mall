"""Model-free visual product search using handcrafted image features.

The index combines perceptual hashing, coarse color histograms, spatial color
layout, and edge orientation. It needs Pillow only; live stock and visibility
remain authoritative in SQL.
"""
import asyncio
from contextlib import contextmanager
import json
import logging
import math
import os
import re
import threading
import uuid
from pathlib import Path

from app.errors import MallError

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
IMAGE_NAME = re.compile(r'([a-f0-9]{64}\.(?:png|jpg|webp))')
ASSET_NAME = re.compile(r'[A-Za-z0-9_-]{1,100}')
MANIFEST_VERSION = 2
ALGORITHM = 'pHash-color-layout-edge-v1'
FEATURE_SHAPE = {'layout': 48, 'histogram': 24, 'edges': 8}


def product_image_bytes(image, storage, asset_root=None):
    """Read a catalog image by its database key, rejecting arbitrary paths."""
    if not isinstance(image, str):
        return None
    if image.startswith('/media/'):
        match = IMAGE_NAME.fullmatch(image.removeprefix('/media/'))
        return storage.get(match.group(1)) if match else None
    if ASSET_NAME.fullmatch(image) and image != 'favicon':
        path = (asset_root or ROOT / 'app' / 'static' / 'images') / f'{image}.svg'
        return path.read_bytes() if path.is_file() else None
    return None


def read_only_catalog_storage(root):
    """Create image storage that can read MinIO objects without creating buckets."""
    from app.storage import ObjectStorage

    endpoint = os.getenv('OSS_ENDPOINT', '').strip()
    access = os.getenv('OSS_ACCESS_KEY', '').strip()
    secret = os.getenv('OSS_SECRET_KEY', '').strip()
    if not (endpoint and access and secret):
        return ObjectStorage(root)

    from minio import Minio
    from minio.error import S3Error

    bucket = os.getenv('OSS_BUCKET', 'heima-mall')
    client = Minio(endpoint, access_key=access, secret_key=secret,
                   secure=os.getenv('OSS_SECURE', '0') == '1')

    class ReadOnlyMinio:
        def get(self, key):
            try:
                response = client.get_object(bucket, key)
            except S3Error as error:
                if error.code in ('NoSuchKey', 'NoSuchBucket'):
                    return None
                raise
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

    return ObjectStorage(root, primary=ReadOnlyMinio())


def _catalog_pil_image(content):
    from PIL import Image, ImageOps
    import io

    if not content:
        return None
    if b'<svg' in content[:8192].lower():
        try:
            import cairosvg
        except ImportError as error:
            raise RuntimeError('SVG商品图特征提取需要安装 CairoSVG 可选依赖') from error
        content = cairosvg.svg2png(bytestring=content, output_width=512)
    with Image.open(io.BytesIO(content)) as original:
        if original.width < 1 or original.height < 1:
            return None
        image = ImageOps.exif_transpose(original)
        if image.width * image.height > 32_000_000:
            image.thumbnail((1280, 1280))
        return image.convert('RGB')


def image_features(image):
    """Return compact deterministic features without a learned model."""
    from PIL import Image

    small = image.convert('RGB').resize((16, 16), Image.Resampling.LANCZOS)
    pixels = list(small.get_flattened_data())

    # 64-bit difference hash is a cheap perceptual duplicate/near-duplicate cue.
    gray = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
    gray_pixels = list(gray.get_flattened_data())
    image_hash = 0
    for y in range(8):
        for x in range(8):
            image_hash = (image_hash << 1) | int(gray_pixels[y * 9 + x] > gray_pixels[y * 9 + x + 1])

    # Four-by-four RGB cells preserve rough object placement and dominant colors.
    layout = []
    for cell_y in range(4):
        for cell_x in range(4):
            cell = [pixels[y * 16 + x]
                    for y in range(cell_y * 4, cell_y * 4 + 4)
                    for x in range(cell_x * 4, cell_x * 4 + 4)]
            layout.extend(sum(pixel[channel] for pixel in cell) / (len(cell) * 255)
                          for channel in range(3))

    # Eight bins per RGB channel provide a small global color histogram.
    histogram = [0] * 24
    for pixel in pixels:
        for channel, value in enumerate(pixel):
            histogram[channel * 8 + min(7, value // 32)] += 1
    histogram = [value / (len(pixels) * 3) for value in histogram]

    # Quantized unsigned gradient orientations retain coarse shape/texture cues.
    luminance = [0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]
                 for pixel in pixels]
    edges = [0.0] * 8
    for y in range(1, 15):
        for x in range(1, 15):
            gx = luminance[y * 16 + x + 1] - luminance[y * 16 + x - 1]
            gy = luminance[(y + 1) * 16 + x] - luminance[(y - 1) * 16 + x]
            magnitude = math.hypot(gx, gy)
            if magnitude:
                angle = math.atan2(gy, gx) % math.pi
                edges[min(7, int(angle * 8 / math.pi))] += magnitude
    edge_total = sum(edges)
    if edge_total:
        edges = [value / edge_total for value in edges]

    return {'hash': f'{image_hash:016x}', 'layout': layout,
            'histogram': histogram, 'edges': edges}


def feature_distance(left, right):
    """Weighted distance; lower scores indicate more visually similar images."""
    hash_distance = (int(left['hash'], 16) ^ int(right['hash'], 16)).bit_count() / 64
    layout_distance = sum(abs(a - b) for a, b in zip(left['layout'], right['layout'])) / 48
    histogram_distance = sum(abs(a - b) for a, b in zip(left['histogram'], right['histogram'])) / 2
    edge_distance = sum(abs(a - b) for a, b in zip(left['edges'], right['edges'])) / 2
    return (0.25 * hash_distance + 0.50 * layout_distance
            + 0.15 * histogram_distance + 0.10 * edge_distance)


def _valid_features(features):
    if not isinstance(features, dict) or not re.fullmatch(r'[a-f0-9]{16}', str(features.get('hash', ''))):
        return False
    for key, length in FEATURE_SHAPE.items():
        values = features.get(key)
        if not isinstance(values, list) or len(values) != length:
            return False
        if any(type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1
               for value in values):
            return False
    return True


class VisualSearch:
    """Persistent brute-force handcrafted-feature index with catalog sync."""

    def __init__(self, index_path='data/visual-search', candidates=200):
        self.index_path = Path(index_path).expanduser()
        self.candidates = candidates
        self._manifest = None
        self._manifest_stamp = None
        self._lock = threading.RLock()

    @classmethod
    def from_environment(cls):
        raw_candidates = int(os.getenv('VISUAL_SEARCH_CANDIDATES', '200'))
        if not 20 <= raw_candidates <= 1000:
            raise ValueError('VISUAL_SEARCH_CANDIDATES必须为20–1000')
        index_path = os.getenv('VISUAL_SEARCH_INDEX_PATH', str(ROOT / 'data' / 'visual-search')).strip()
        return cls(index_path or str(ROOT / 'data' / 'visual-search'), raw_candidates)

    @property
    def configured(self):
        return True

    @property
    def enabled(self):
        return self._read_manifest() is not None

    def _read_manifest(self):
        try:
            path = self.index_path / 'manifest.json'
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if self._manifest is not None and stamp == self._manifest_stamp:
                return self._manifest
            value = json.loads(path.read_text(encoding='utf-8'))
            if (type(value.get('version')) is not int or value['version'] != MANIFEST_VERSION
                    or value.get('algorithm') != ALGORITHM
                    or type(value.get('revision')) is not int or value['revision'] < 0
                    or not isinstance(value.get('items'), dict)):
                self._manifest = None
                self._manifest_stamp = stamp
                return None
            for product_id, item in value['items'].items():
                if (not product_id.isdigit() or not isinstance(item, dict)
                        or not isinstance(item.get('image'), str)
                        or (item.get('features') is not None and not _valid_features(item['features']))):
                    self._manifest = None
                    self._manifest_stamp = stamp
                    return None
            self._manifest = value
            self._manifest_stamp = stamp
            return value
        except (OSError, ValueError, AttributeError, TypeError):
            self._manifest = None
            self._manifest_stamp = None
            return None

    @contextmanager
    def _file_lock(self):
        """Serialize index updates across web worker processes."""
        import fcntl

        self.index_path.mkdir(parents=True, exist_ok=True)
        with (self.index_path / '.index.lock').open('a+b') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _refresh_index_locked(self):
        manifest = self._read_manifest()
        if not manifest:
            raise MallError(503, '图片搜索索引尚未构建，请先运行商品图片索引脚本')
        self._manifest = manifest

    async def search_image(self, image_bytes, connection, storage, category='', store_id=None):
        revision_row = (await connection.execute(
            'SELECT revision FROM catalog_cache_state WHERE id=1')).fetchone()
        revision = int(revision_row['revision']) if revision_row else 0
        rows = None
        with self._lock:
            current = self._read_manifest()
            needs_sync = current is None or int(current.get('revision', -1)) != revision
        if needs_sync:
            rows = (await connection.execute(
                'SELECT p.id,p.image FROM products p JOIN stores s ON s.id=p.store_id '
                'WHERE p.is_active=1 AND p.stock>0 AND s.is_active=1 ORDER BY p.id')).fetchall()
        return await self._backend_call(self._search, image_bytes, revision, rows, storage)

    @staticmethod
    async def _backend_call(function, *arguments):
        try:
            return await asyncio.to_thread(function, *arguments)
        except MallError:
            raise
        except Exception as error:
            LOGGER.exception('无模型图片搜索失败')
            raise MallError(503, '图片相似搜索暂时不可用，请检查商品图片或重建索引') from error

    def _search(self, image_bytes, revision, rows, storage):
        with self._lock:
            with self._file_lock():
                self._refresh_index_locked()
                if int(self._manifest.get('revision', -1)) < revision:
                    if rows is None:
                        raise MallError(503, '商品目录刚刚发生变化，请重试图片搜索')
                    self._sync_catalog(rows, revision, storage)
            query = _catalog_pil_image(image_bytes)
            if query is None:
                return []
            query_features = image_features(query)
            ranked = []
            for product_id, item in self._manifest['items'].items():
                features = item.get('features')
                if features is not None:
                    ranked.append((feature_distance(query_features, features), int(product_id)))
            ranked.sort(key=lambda match: (match[0], match[1]))
            return [product_id for _distance, product_id in ranked[:self.candidates]]

    def _sync_catalog(self, rows, revision, storage):
        catalog = {str(int(row['id'])): str(row['image']) for row in rows}
        old_items = self._manifest['items']
        items = {}
        for product_id, image_key in catalog.items():
            old = old_items.get(product_id)
            if old and old.get('image') == image_key:
                items[product_id] = old
                continue
            content = product_image_bytes(image_key, storage)
            features = None
            if content is not None:
                try:
                    image = _catalog_pil_image(content)
                    if image is not None:
                        features = image_features(image)
                except Exception:
                    LOGGER.warning('商品图片特征生成失败，商品图片键=%s', image_key, exc_info=True)
            items[product_id] = {'image': image_key, 'features': features}
        self._manifest = {'version': MANIFEST_VERSION, 'algorithm': ALGORITHM,
                          'revision': int(revision), 'items': items}
        self._persist()

    def rebuild_index(self, rows, revision, storage):
        """Build a fresh model-free feature index for the management command."""
        catalog = {str(int(row['id'])): str(row['image']) for row in rows}
        items = {}
        indexed = 0
        for product_id, image_key in catalog.items():
            content = product_image_bytes(image_key, storage)
            features = None
            if content is not None:
                try:
                    image = _catalog_pil_image(content)
                    if image is not None:
                        features = image_features(image)
                        indexed += 1
                except Exception:
                    LOGGER.warning('商品图片特征生成失败，商品图片键=%s', image_key, exc_info=True)
            items[product_id] = {'image': image_key, 'features': features}
        if not indexed:
            raise RuntimeError('没有可用商品图片，无法构建图片搜索索引')
        with self._lock, self._file_lock():
            self._manifest = {'version': MANIFEST_VERSION, 'algorithm': ALGORITHM,
                              'revision': int(revision), 'items': items}
            self._persist()
        return indexed

    def _persist(self):
        self.index_path.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path / f'manifest-{uuid.uuid4().hex}.tmp'
        target = self.index_path / 'manifest.json'
        with temporary.open('w', encoding='utf-8') as output:
            json.dump(self._manifest, output, ensure_ascii=False, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        stat = target.stat()
        self._manifest_stamp = (stat.st_mtime_ns, stat.st_size)

    def close(self):
        self._manifest = None
        self._manifest_stamp = None
