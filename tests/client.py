"""Exercise real WSGI requests in-process, without binding a network port."""
import io
import json
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from wsgiref.util import setup_testing_defaults


class Client:
    def __init__(self, application):
        self.application = application
        self.cookies = {}

    def request(self, method, path, payload=None, headers=None, raw=None, remote_addr='127.0.0.1'):
        url = urlsplit(path)
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else b''
        environ = {}
        setup_testing_defaults(environ)
        environ.update({'REQUEST_METHOD': method, 'PATH_INFO': url.path, 'QUERY_STRING': url.query,
            'HTTP_HOST': 'testserver', 'REMOTE_ADDR': remote_addr, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(body)), 'HTTP_X_REQUESTED_WITH': 'HeimaMall',
            'HTTP_COOKIE': '; '.join(f'{key}={value}' for key, value in self.cookies.items()), 'wsgi.input': io.BytesIO(body)})
        for key, value in (headers or {}).items():
            environ['CONTENT_TYPE' if key.lower() == 'content-type' else 'HTTP_' + key.upper().replace('-', '_')] = value
        result = {}

        def start_response(status, response_headers):
            result['status'] = int(status.split()[0])
            result['headers'] = response_headers
            for key, value in response_headers:
                if key.lower() == 'set-cookie':
                    cookie = SimpleCookie(value)
                    for name, morsel in cookie.items():
                        if morsel['max-age'] == '0':
                            self.cookies.pop(name, None)
                        else:
                            self.cookies[name] = morsel.value

        body = b''.join(self.application(environ, start_response))
        result['body'] = body
        try:
            result['json'] = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            result['json'] = None
        return result

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, payload=None, **kwargs):
        return self.request('POST', path, payload, **kwargs)

    def login(self, phone, password, admin=False):
        captcha = self.get('/api/auth/captcha')['json']
        with self.application.state.database.connection() as connection:
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        return self.post('/api/admin/auth/login' if admin else '/api/auth/login',
                         {'phone': phone, 'password': password, 'captcha_id': captcha['captcha_id'], 'captcha_code': code})
