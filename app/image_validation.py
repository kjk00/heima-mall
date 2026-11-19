"""Decode uploads before publication; dependency-free PNG support for legacy mode."""
import io
import struct
import warnings
import zlib

from app.errors import MallError

MAX_PIXELS = 16_000_000


def dimensions(width, height):
    if not 0 < width <= 10000 or not 0 < height <= 10000 or width * height > MAX_PIXELS:
        raise ValueError('图片尺寸不正确或过大')


def validate_png(content):
    """Validate complete non-interlaced PNG chunks, CRCs and bounded scanlines."""
    position, header, compressed, ended = 8, None, bytearray(), False
    while position < len(content):
        if position + 12 > len(content):
            raise ValueError('PNG文件被截断')
        size, = struct.unpack_from('>I', content, position)
        kind = content[position + 4:position + 8]
        end = position + 12 + size
        if end > len(content):
            raise ValueError('PNG数据块不完整')
        data = content[position + 8:end - 4]
        checksum, = struct.unpack_from('>I', content, end - 4)
        if zlib.crc32(kind + data) != checksum:
            raise ValueError('PNG校验失败')
        if header is None and kind != b'IHDR':
            raise ValueError('PNG缺少图片头')
        if kind == b'IHDR':
            if header is not None or size != 13:
                raise ValueError('PNG图片头不正确')
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            if size or end != len(content):
                raise ValueError('PNG结束块不正确')
            ended = True
        elif kind not in {b'PLTE'} and not kind[0] & 32:
            raise ValueError('PNG含不支持的关键数据块')
        position = end
    if not header or not ended or not compressed:
        raise ValueError('PNG图片数据不完整')
    width, height, depth, color, compression, filtering, interlace = header
    dimensions(width, height)
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if depth not in depths.get(color, set()) or color == 3 or compression or filtering or interlace:
        raise ValueError('此PNG格式需要安装Pillow后上传')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = (width * channels * depth + 7) // 8 + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    # Limit allocation even for malicious compressed payloads.
    pixels = decoder.decompress(compressed, expected + 1)
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('PNG像素数据不完整')
    if any(pixels[offset] > 4 for offset in range(0, expected, stride)):
        raise ValueError('PNG扫描行格式不正确')


def image_extension(content):
    png = content.startswith(b'\x89PNG\r\n\x1a\n')
    jpeg = content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9') and b'\xff\xda' in content
    webp = (len(content) >= 30 and content[:4] == b'RIFF' and content[8:12] == b'WEBP'
            and int.from_bytes(content[4:8], 'little') + 8 == len(content)
            and content[12:16] in {b'VP8 ', b'VP8L', b'VP8X'}
            and 0 < int.from_bytes(content[16:20], 'little') <= len(content) - 20)
    if not (png or jpeg or webp):
        raise MallError(422, '图片不完整，或不是有效的PNG、JPEG、WebP')
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        if not png:
            raise MallError(503, 'JPEG和WebP上传需要安装requirements.txt中的Pillow')
        try:
            validate_png(content)
        except (ValueError, zlib.error, struct.error) as error:
            raise MallError(422, 'PNG图片不完整或格式不正确') from error
        return 'png'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                dimensions(*image.size)
                extension = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}.get(image.format)
                if not extension:
                    raise ValueError('不支持的图片类型')
                if getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('仅支持静态图片')
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
        return extension
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise MallError(422, '图片无法解码、被截断或尺寸过大') from error
