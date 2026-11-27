"""Deterministic tests for model-free handcrafted image search."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app import visual_search


class FakeStorage:
    def __init__(self, images):
        self.images = images

    def get(self, key):
        return self.images.get(key)


class CatalogConnection:
    def __init__(self, rows, revision):
        self.rows = rows
        self.revision = revision

    async def execute(self, sql, _parameters=()):
        if 'SELECT revision' in sql:
            return SimpleNamespace(fetchone=lambda: {'revision': self.revision})
        return SimpleNamespace(fetchall=lambda: self.rows)


def image_bytes(color, mark=False):
    image = Image.new('RGB', (40, 40), color)
    if mark:
        for y in range(8, 20):
            for x in range(10, 24):
                image.putpixel((x, y), (255, 255, 255))
    output = io.BytesIO()
    image.save(output, format='JPEG')
    return output.getvalue()


def media_key(number):
    return f'/media/{number:064x}.jpg'


class HandcraftedFeatureTests(unittest.TestCase):
    def test_features_are_fixed_size_and_identical_images_have_zero_distance(self):
        red = visual_search._catalog_pil_image(image_bytes((230, 20, 10)))
        features = visual_search.image_features(red)

        self.assertEqual(len(features['hash']), 16)
        self.assertEqual(len(features['layout']), 48)
        self.assertEqual(len(features['histogram']), 24)
        self.assertEqual(len(features['edges']), 8)
        self.assertEqual(visual_search.feature_distance(features, features), 0.0)

    def test_color_and_shape_changes_produce_nonzero_distance(self):
        red = visual_search.image_features(visual_search._catalog_pil_image(image_bytes((255, 0, 0))))
        green = visual_search.image_features(visual_search._catalog_pil_image(image_bytes((0, 255, 0))))
        marked = visual_search.image_features(
            visual_search._catalog_pil_image(image_bytes((255, 0, 0), mark=True)))

        self.assertGreater(visual_search.feature_distance(red, green), 0.2)
        self.assertGreater(visual_search.feature_distance(red, marked), 0.01)
        self.assertTrue(visual_search._valid_features(red))
        self.assertFalse(visual_search._valid_features({**red, 'edges': [0.5]}))


class VisualSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.storage = FakeStorage({
            media_key(1).removeprefix('/media/'): image_bytes((255, 0, 0)),
            media_key(2).removeprefix('/media/'): image_bytes((0, 255, 0)),
            media_key(3).removeprefix('/media/'): image_bytes((0, 0, 255)),
            media_key(4).removeprefix('/media/'): image_bytes((230, 20, 10)),
        })
        self.service = visual_search.VisualSearch(self.root / 'index', candidates=20)

    async def test_color_layout_ranking_persistence_and_incremental_catalog_sync(self):
        products = [
            {'id': 1, 'image': media_key(1)},
            {'id': 2, 'image': media_key(2)},
            {'id': 3, 'image': media_key(3)},
        ]
        self.assertEqual(self.service.rebuild_index(products, 7, self.storage), 3)
        self.assertTrue(self.service.enabled)
        query = image_bytes((240, 12, 4))
        connection = CatalogConnection(products, 7)
        ids = await self.service.search_image(query, connection, self.storage)
        self.assertEqual(ids[0], 1)

        other_worker = visual_search.VisualSearch(self.root / 'index', candidates=20)
        self.addCleanup(other_worker.close)
        self.assertEqual(await other_worker.search_image(query, connection, self.storage), ids)

        connection.rows = [
            {'id': 1, 'image': media_key(1)},
            {'id': 3, 'image': media_key(3)},
            {'id': 4, 'image': media_key(4)},
        ]
        connection.revision = 8
        ids = await self.service.search_image(query, connection, self.storage)
        self.assertEqual(ids[0], 4)
        self.assertEqual(ids[1], 1)
        self.assertNotIn('2', self.service._manifest['items'])
        self.assertEqual(self.service._manifest['revision'], 8)
        self.assertTrue((self.root / 'index' / 'manifest.json').is_file())
        self.assertEqual(await other_worker.search_image(query, connection, self.storage), ids)

    def test_catalog_image_lookup_rejects_untrusted_paths(self):
        self.assertIsNone(visual_search.product_image_bytes('../secret', self.storage))
        self.assertIsNone(visual_search.product_image_bytes('/media/../../secret.jpg', self.storage))
        self.assertIsNone(visual_search.product_image_bytes('favicon', self.storage))

    def test_corrupt_manifest_is_rejected(self):
        self.service.index_path.mkdir()
        (self.service.index_path / 'manifest.json').write_text(
            '{"version":2,"algorithm":"wrong","revision":1,"items":{}}', encoding='utf-8')
        self.assertIsNone(self.service._read_manifest())
        self.assertFalse(self.service.enabled)


if __name__ == '__main__':
    unittest.main()
