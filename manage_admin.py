"""Provision administrator permissions locally; never expose this through HTTP."""
import argparse
import os
from pathlib import Path
import random
import re
import secrets

from app.database import Database, transaction
from app.security import hash_password

ROOT = Path(__file__).resolve().parent


def credential_output_path(output):
    output = Path(output).expanduser().absolute()
    resolved = output.resolve()
    repository = ROOT.resolve()
    private = repository / 'data' / 'private'
    if resolved.is_relative_to(repository) and not resolved.is_relative_to(private):
        raise ValueError('仓库内的登录信息只能保存在data/private/，或用--output指定仓库外的文件')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if resolved.is_relative_to(private):
        private.chmod(0o700)
    return output


def assign_admin(connection, phone, store_id=None, super_admin=False):
    if not re.fullmatch(r'1[3-9]\d{9}', phone):
        raise ValueError('请输入有效的账号手机号')
    if not super_admin and not connection.execute('SELECT 1 FROM stores WHERE id=?', (store_id,)).fetchone():
        raise ValueError('绑定的店铺不存在')
    with transaction(connection):
        user = connection.execute('SELECT id FROM users WHERE phone=?', (phone,)).fetchone()
        if not user:
            raise ValueError('账号不存在，请先在商城注册')
        connection.execute("UPDATE users SET role='admin',is_super_admin=?,store_id=?,is_active=1 WHERE id=?",
                           (int(super_admin), None if super_admin else store_id, user['id']))
        connection.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
    return user['id']


def generate_store_admins(connection, output):
    stores = [dict(row) for row in connection.execute("""SELECT s.id,s.name FROM stores s
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.role='admin' AND u.is_super_admin=0
        AND u.is_active=1 AND u.store_id=s.id) ORDER BY s.id""")]
    if not stores:
        return 0
    random.SystemRandom().shuffle(stores)
    output = credential_output_path(output)
    # Do not replace an earlier credential file or make the credentials world readable.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as report, transaction(connection):
            report.write('# 店铺管理员登录信息\n\n登录商城或 `/admin`，每个账号只管理绑定店铺。\n\n')
            report.write('| 店铺编号 | 店铺 | 账号昵称 | 手机号 | 密码 |\n| --- | --- | --- | --- | --- |\n')
            for store in stores:
                while True:
                    phone = '199' + ''.join(secrets.choice('0123456789') for _ in range(8))
                    if not connection.execute('SELECT 1 FROM users WHERE phone=?', (phone,)).fetchone():
                        break
                password = secrets.token_urlsafe(15)
                name = '店铺管理员' + secrets.token_hex(2)
                connection.execute("""INSERT INTO users(phone,name,password_hash,role,store_id,is_super_admin)
                    VALUES (?,?,?,'admin',?,0)""", (phone, name, hash_password(password), store['id']))
                store_name = store['name'].replace('|', '\\|').replace('\n', ' ')
                report.write(f"| {store['id']} | {store_name} | {name} | {phone} | {password} |\n")
            report.write('\n终端管理员可查看全部店铺，使用原有管理员账号和密码登录。\n')
            report.flush()
            os.fsync(report.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return len(stores)


def main():
    from app.config import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description='从终端设置管理员权限和店铺归属')
    parser.add_argument('--database', type=Path, default=None, help='指定SQLite文件；省略时使用DATABASE_BACKEND和MYSQL_*配置')
    parser.add_argument('--phone', help='已注册账号的手机号')
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--super-admin', action='store_true', help='设置为终端管理员，可管理全部店铺')
    actions.add_argument('--store-id', type=int, help='设置为只管理指定店铺的管理员')
    actions.add_argument('--generate-store-admins', action='store_true', help='为尚无管理员的店铺随机生成账号')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/private/store-admin-accounts.md',
                        help='登录信息文件；仓库内仅允许data/private/，也可指定仓库外的新文件')
    arguments = parser.parse_args()
    if not arguments.generate_store_admins and not arguments.phone:
        parser.error('设置权限时必须指定 --phone')
    database = Database(arguments.database)
    database.initialize()
    try:
        with database.connection() as connection:
            if arguments.generate_store_admins:
                count = generate_store_admins(connection, arguments.output)
                print(f'已生成并绑定 {count} 个店铺管理员账号。' + (f' 登录信息：{arguments.output}' if count else ''))
            else:
                assign_admin(connection, arguments.phone, arguments.store_id, arguments.super_admin)
                print('已设置终端管理员权限，请重新登录。' if arguments.super_admin else f'已绑定店铺 {arguments.store_id}，请重新登录。')
    except (ValueError, OSError) as error:
        parser.exit(1, f'设置失败：{error}\n')


if __name__ == '__main__':
    main()
