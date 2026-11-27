"""Build or replace the model-free local image-feature index.

For raster images, Pillow is already installed. Install the optional visual
extra only if the catalog contains SVG product artwork.
    python scripts/rebuild_visual_index.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_environment
from app.database import Database
from app.visual_search import VisualSearch, read_only_catalog_storage


def main():
    load_environment()
    database = Database()
    if getattr(database, 'backend', 'sqlite') == 'mysql':
        database.initialize(create_schema=False)
    elif not Path(database.path).expanduser().is_file():
        raise RuntimeError('商城 SQLite 数据库不存在，请先启动应用完成初始化')
    with database.connection() as connection:
        revision_row = connection.execute(
            'SELECT revision FROM catalog_cache_state WHERE id=1').fetchone()
        revision = int(revision_row['revision']) if revision_row else 0
        products = connection.execute(
            'SELECT p.id,p.image FROM products p JOIN stores s ON s.id=p.store_id '
            'WHERE p.is_active=1 AND p.stock>0 AND s.is_active=1 ORDER BY p.id').fetchall()
    storage = read_only_catalog_storage(Path(database.path).parent / 'uploads')
    visual_search = VisualSearch.from_environment()
    try:
        indexed = visual_search.rebuild_index(products, revision, storage)
    finally:
        visual_search.close()
    print(f'图片特征索引已构建：{indexed} 件商品，目录版本 {revision}')


if __name__ == '__main__':
    main()
