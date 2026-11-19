#!/usr/bin/env bash
# Manage the Dockerized backing services for heima-mall. The Python app stays on
# the host and reaches them via 127.0.0.1 on the native ports, so .env is unchanged.
# The native installs were removed; data lives in Docker volumes + backups/.
# Usage: scripts/docker_services.sh {up|down|status|reindex|rollback}
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  up)
    docker compose up -d
    docker compose ps
    ;;
  down)
    # Keep named volumes (data persists). Add -v to discard them.
    docker compose down
    ;;
  status)
    docker compose ps --format '{{.Name}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'
    ;;
  reindex)
    # Rebuild the Elasticsearch index for the real catalog from MySQL.
    .venv/bin/python - <<'PY'
import asyncio, os
from app.config import load_environment
load_environment()
from app.async_database import AsyncDatabase
from app.search import index_alias
from app.search_sync import full_reindex
from elasticsearch import AsyncElasticsearch
async def main():
    db = AsyncDatabase()
    alias = index_alias(db.path)
    es = AsyncElasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=60)
    try:
        await full_reindex(db, es, alias)
        print('reindexed', alias, (await es.count(index=alias))['count'], 'docs')
    finally:
        await es.close()
        await db.close()
asyncio.run(main())
PY
    ;;
  rollback)
    # Native services were uninstalled; rolling back means reinstalling and
    # restoring from backups/docker-migration (Docker volumes are kept intact).
    cat >&2 <<'EOF'
原生服务已卸载，无法直接重启。如需回退到原生运行：
  1) brew install mysql redis elasticsearch-full rabbitmq minio
  2) brew services start mysql redis elasticsearch-full rabbitmq minio
  3) MongoDB 重新安装官方发行包到 ~/.local/mongodb 并启动 mongod
  4) 从 backups/docker-migration 恢复数据：
     - MySQL:  mysql -uroot -p < mysql_heima_mall.sql
     - MongoDB: mongorestore --nsInclude 'heima_params_*.*' mongo/
     - MinIO:  经 S3 API 上传 minio-objects/heima-mall/ 下的对象
Docker 数据卷与 backups/ 均保留，可安全重建。
EOF
    ;;
  *)
    echo "Usage: $0 {up|down|status|reindex|rollback}" >&2
    exit 1
    ;;
esac