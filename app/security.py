import hashlib
import hmac
import secrets
import time
import asyncio

SESSION_SECONDS = 7 * 24 * 60 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    _, rounds, salt, expected = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    await connection.execute('DELETE FROM sessions WHERE expires_at <= ?', (now,))
    await connection.execute('INSERT INTO sessions VALUES (?,?,?)', (token_hash(token), user_id, now + SESSION_SECONDS))
    return token


async def hash_password_async(password):
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password, encoded):
    return await asyncio.to_thread(verify_password, password, encoded)

