"""Check configured local MySQL using the production aiomysql/AsyncSession path."""
import argparse
import asyncio
import errno
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def connection_failure_hint(error):
    """Inspect wrapped errors without printing SQL, URLs, or credentials."""
    pending, seen, errors = [error], set(), []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        errors.append(current)
        pending.extend(cause for cause in
                       (current.__cause__, current.__context__, getattr(current, 'orig', None))
                       if isinstance(cause, BaseException))
    if any(isinstance(error, OSError) and error.errno in {errno.EPERM, errno.EACCES}
           for error in errors):
        return ('当前执行环境禁止访问MySQL连接（Operation not permitted / Permission denied），'
                '尚无法验证账号密码。请在允许访问本地MySQL的本机终端运行此脚本。')
    codes = {error.args[0] for error in errors if error.args and isinstance(error.args[0], int)}
    if 1045 in codes:
        return 'MySQL认证失败（1045）。请检查MYSQL_USER、MYSQL_PASSWORD及该账号允许登录的主机。'
    if 1049 in codes:
        return '配置的数据库不存在（1049）。请核对MYSQL_DATABASE；需要创建商城表结构时运行此脚本的--initialize选项。'
    if 1044 in codes or 1142 in codes:
        return 'MySQL账号没有所需数据库或表的访问权限。请检查该账号的数据库授权。'
    if any(isinstance(error, ConnectionRefusedError) for error in errors):
        return 'MySQL连接被拒绝。请启动MySQL服务，并核对MYSQL_HOST、MYSQL_PORT或MYSQL_UNIX_SOCKET。'
    return '请检查MySQL服务、MYSQL_*配置、数据库权限和本地连接权限。'


async def check(initialize):
    from app.async_database import AsyncDatabase
    database=None
    try:
        database=AsyncDatabase()
        if database.backend!='mysql':
            print('未连接MySQL：请在.env中设置DATABASE_BACKEND=mysql。',file=sys.stderr)
            return 1
        if initialize:
            await database.initialize()
        async with database.connection() as connection:
            row=(await connection.execute('SELECT VERSION() AS version,DATABASE() AS db,CURRENT_USER() AS account')).fetchone()
        print(f"MySQL连接成功：{row['db']}，版本{row['version']}，账号{row['account']}")
        print('连接方式：SQLAlchemy AsyncSession + aiomysql')
        return 0
    except Exception as error:
        print(f'连接未成功（{type(error).__name__}）。{connection_failure_hint(error)}',file=sys.stderr)
        return 1
    finally:
        if database is not None:
            await database.close()


def main():
    parser=argparse.ArgumentParser(description='检查本地MySQL异步连接；可初始化商城表结构')
    parser.add_argument('--initialize',action='store_true',help='创建配置的数据库、表和演示种子；已有数据保留')
    arguments=parser.parse_args()
    from app.config import load_environment
    load_environment()
    missing=[name for name in ('sqlalchemy','aiomysql','greenlet') if importlib.util.find_spec(name) is None]
    if missing:
        parser.exit(2,'未连接MySQL：缺少'+', '.join(missing)+'。请执行python -m pip install -r requirements.txt。\n')
    return asyncio.run(check(arguments.initialize))


if __name__=='__main__':
    raise SystemExit(main())
