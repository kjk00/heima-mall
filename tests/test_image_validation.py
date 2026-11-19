"""白盒测试：图片上传校验的内部逻辑与边界（dimensions/validate_png/image_extension）。

用手工 PNG 构造器精确控制 IHDR/IDAT/IEND、CRC、扫描行与数据块类型，覆盖
依赖无关的 PNG 校验路径；再用 Pillow 生成真实 PNG/JPEG/WebP 覆盖解码路径。
"""
import io
import struct
import sys
import unittest
import zlib
from unittest.mock import patch

from app import image_validation as iv
from app.errors import MallError

try:
    from PIL import Image  # noqa: F401
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def build_png(width=4, height=4, depth=8, color=2, compression=0, filtering=0, interlace=0,
              filter_byte=0, raw=None, extra_chunks=b'', idat=True):
    ihdr = struct.pack('>IIBBBBB', width, height, depth, color, compression, filtering, interlace)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = (width * channels * depth + 7) // 8 + 1
    if raw is None:
        row = bytes([filter_byte]) + bytes(stride - 1)
        raw = row * height
    body = chunk(b'IHDR', ihdr) + extra_chunks
    if idat:
        body += chunk(b'IDAT', zlib.compress(raw))
    return b'\x89PNG\r\n\x1a\n' + body + chunk(b'IEND', b'')


def pillow_image(fmt, size=(10, 10), mode='RGB', **save_kwargs):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 30) if mode == 'RGB' else 0).save(buffer, fmt, **save_kwargs)
    return buffer.getvalue()


REAL_JPEG = pillow_image('JPEG') if PIL_AVAILABLE else b''


class DimensionsTests(unittest.TestCase):
    def test_accepts_boundaries(self):
        for width, height in ((1, 1), (10000, 1600), (4000, 4000), (1600, 10000)):
            iv.dimensions(width, height)

    def test_rejects_zero_negative_and_oversize_edges(self):
        for width, height in ((0, 10), (10, 0), (-1, 10), (10, -1), (10001, 1), (1, 10001),
                              (4000, 4001), (10000, 1601), (10000, 10000)):
            with self.assertRaises(ValueError, msg=f'{width}x{height}'):
                iv.dimensions(width, height)


class ValidatePngTests(unittest.TestCase):
    def test_valid_rgb_png_passes(self):
        iv.validate_png(build_png())
        iv.validate_png(build_png(width=1, height=1))
        iv.validate_png(build_png(color=6, depth=8))   # RGBA
        iv.validate_png(build_png(color=0, depth=8))   # grayscale

    def test_truncated_chunk_header_and_body(self):
        full = build_png()
        with self.assertRaises(ValueError):
            iv.validate_png(full[:12])                 # 不足一个数据块头
        with self.assertRaises(ValueError):
            iv.validate_png(full[:len(full) - 3])      # 结束块被截断

    def test_bad_crc_is_rejected(self):
        content = bytearray(build_png())
        idat = content.find(b'IDAT')
        content[idat + 4] ^= 0xFF                      # 破坏 IDAT 数据使 CRC 失配
        with self.assertRaises(ValueError):
            iv.validate_png(bytes(content))

    def test_missing_ihdr_first(self):
        body = chunk(b'IDAT', zlib.compress(b'\x00' * 13)) + chunk(b'IEND', b'')
        with self.assertRaises(ValueError):
            iv.validate_png(b'\x89PNG\r\n\x1a\n' + body)

    def test_duplicate_ihdr_rejected(self):
        ihdr = struct.pack('>IIBBBBB', 4, 4, 8, 2, 0, 0, 0)
        extra = chunk(b'IHDR', ihdr)
        with self.assertRaises(ValueError):
            iv.validate_png(build_png(extra_chunks=extra))

    def test_interlaced_palette_and_bad_depth_need_pillow(self):
        for kwargs in ({'interlace': 1}, {'color': 3}, {'color': 2, 'depth': 1},
                       {'compression': 1}, {'filtering': 1}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                iv.validate_png(build_png(**kwargs))

    def test_unknown_critical_chunk_rejected_but_ancillary_allowed(self):
        with self.assertRaises(ValueError):
            iv.validate_png(build_png(extra_chunks=chunk(b'XYZW', b'data')))   # 关键块（bit5=0）
        iv.validate_png(build_png(extra_chunks=chunk(b'tEXt', b'comment')))    # 辅助块（bit5=1）

    def test_bad_iend_and_missing_idat(self):
        ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 4, 8, 2, 0, 0, 0))
        with self.assertRaises(ValueError):
            iv.validate_png(b'\x89PNG\r\n\x1a\n' + ihdr + chunk(b'IDAT', zlib.compress(b'\x00' * 52)) + chunk(b'IEND', b'x'))
        with self.assertRaises(ValueError):
            iv.validate_png(build_png(idat=False))

    def test_scanline_filter_and_pixel_length_checked(self):
        with self.assertRaises(ValueError):
            iv.validate_png(build_png(filter_byte=5))                          # 过滤类型>4
        with self.assertRaises(ValueError):
            iv.validate_png(build_png(raw=b'\x00' * 10))                       # 像素长度不符


@unittest.skipUnless(PIL_AVAILABLE, '需要Pillow覆盖真实图片解码路径')
class ImageExtensionPillowTests(unittest.TestCase):
    def test_real_png_jpeg_webp_decode(self):
        self.assertEqual(iv.image_extension(pillow_image('PNG')), 'png')
        self.assertEqual(iv.image_extension(pillow_image('JPEG')), 'jpg')
        self.assertEqual(iv.image_extension(pillow_image('WEBP')), 'webp')

    def test_non_image_and_fake_headers_rejected(self):
        for content in (b'not an image at all', b'\x89PNG\r\n\x1a\n' + b'garbage',
                        b'\xff\xd8\xff' + b'garbage' + b'\xff\xd9', b'GIF89a' + b'\x00' * 40):
            with self.assertRaises(MallError) as caught:
                iv.image_extension(content)
            self.assertEqual(caught.exception.status_code, 422)

    def test_truncated_jpeg_rejected(self):
        jpeg = pillow_image('JPEG')
        with self.assertRaises(MallError) as caught:
            iv.image_extension(jpeg[:len(jpeg) // 2])
        self.assertEqual(caught.exception.status_code, 422)

    def test_oversized_dimensions_rejected(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (10000, 10000)).save(buffer, 'PNG')   # 1亿像素 > 1600万上限
        with self.assertRaises(MallError) as caught:
            iv.image_extension(buffer.getvalue())
        self.assertEqual(caught.exception.status_code, 422)

    def test_animated_webp_rejected(self):
        from PIL import Image
        frames = [Image.new('RGB', (8, 8), (i * 40, 0, 0)) for i in range(3)]
        buffer = io.BytesIO()
        frames[0].save(buffer, 'WEBP', save_all=True, append_images=frames[1:], duration=100, loop=0)
        with self.assertRaises(MallError) as caught:
            iv.image_extension(buffer.getvalue())
        self.assertEqual(caught.exception.status_code, 422)


class ImageExtensionLegacyTests(unittest.TestCase):
    """无 Pillow 时回退到依赖无关的 PNG 校验。"""
    def setUp(self):
        self.patcher = patch.dict(sys.modules, {'PIL': None})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_valid_png_accepted_without_pillow(self):
        self.assertEqual(iv.image_extension(build_png()), 'png')

    @unittest.skipUnless(PIL_AVAILABLE, '需要Pillow生成真实JPEG样本')
    def test_jpeg_requires_pillow(self):
        with self.assertRaises(MallError) as caught:
            iv.image_extension(REAL_JPEG)
        self.assertEqual(caught.exception.status_code, 503)

    def test_invalid_png_maps_to_422(self):
        with self.assertRaises(MallError) as caught:
            iv.image_extension(build_png(filter_byte=7))
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == '__main__':
    unittest.main()