"""Bound ASGI request bodies and validate raw JSON before framework coercion."""
import asyncio
import json
import re
from urllib.parse import parse_qs

from app.routes import ROUTES


class RequestGuard:
    def __init__(self, app):
        self.app = app
        self.models = [(route.method, re.compile('^' + re.sub(r'\{\w+\}', '[^/]+', route.path) + '$'),
                        route.model, 3 * 1024 * 1024 if route.operation == 'admin_upload_image' else 16384)
                       for route in ROUTES if route.model]

    @staticmethod
    async def reject(send, status, detail):
        body = json.dumps({'detail': detail}, ensure_ascii=False).encode()
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json; charset=utf-8'),
                                (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        try:
            parse_qs(scope.get('query_string', b'').decode('utf-8'), max_num_fields=20)
        except (ValueError, UnicodeError):
            return await self.reject(send, 422, '查询参数过多或格式不正确')
        matching = next(((model, maximum) for method, pattern, model, maximum in self.models
                         if method == scope['method'] and pattern.fullmatch(scope['path'])), None)
        if matching is None:
            return await self.app(scope, receive, send)
        model, maximum = matching
        headers = dict(scope.get('headers', []))
        if headers.get(b'content-type', b'').split(b';')[0].strip().lower() != b'application/json':
            return await self.reject(send, 415, '请求内容必须为application/json')
        try:
            length = int(headers.get(b'content-length', b'0'))
            if length < 0:
                raise ValueError()
        except ValueError:
            return await self.reject(send, 400, '请求正文长度不正确')
        if length > maximum:
            return await self.reject(send, 413, '请求正文过大')
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > maximum:
                        return await self.reject(send, 413, '请求正文过大')
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await self.reject(send, 408, '读取请求正文超时')
        if not body:
            return await self.reject(send, 400, '请求正文为空')
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            return await self.reject(send, 400, 'JSON格式不正确')
        if not isinstance(payload, dict):
            return await self.reject(send, 422, '请求正文必须为JSON对象')
        try:
            model(**payload)
        except (TypeError, ValueError, RecursionError):
            return await self.reject(send, 422, '请求字段类型、范围或内容不正确')
        pending = True
        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)
