"""Run the asynchronous service; explicitly select stdlib for legacy local use."""
import argparse
import errno
import importlib.util
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server


class LocalServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def main():
    from app.config import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description='启动黑马商城 Python 后端')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--backend', choices=['auto', 'stdlib', 'fastapi'], default='fastapi')
    parser.add_argument('--workers', type=int, default=1, help='FastAPI工作进程数；每个进程拥有独立连接池')
    arguments = parser.parse_args()
    required = ['fastapi', 'uvicorn', 'jinja2', 'sqlalchemy', 'aiomysql', 'redis', 'greenlet', 'PIL']
    import os
    if os.getenv('DATABASE_BACKEND', 'mysql') == 'sqlite':
        required.append('aiosqlite')
    available = all(importlib.util.find_spec(name) for name in required)
    use_fastapi = arguments.backend != 'stdlib'
    if use_fastapi and not available:
        parser.error('异步服务依赖尚未安装，请运行 pip install -r requirements.txt')
    if not 1 <= arguments.workers <= 16:
        parser.error('--workers必须为1–16')
    if use_fastapi:
        import uvicorn
        uvicorn.run('app.main:app', host=arguments.host, port=arguments.port, workers=arguments.workers,
                    limit_concurrency=512, backlog=2048, timeout_keep_alive=5)
    else:
        from contextlib import ExitStack
        from app.server import MallApplication
        from app.order_expiration import legacy_expiration_worker
        from app.search import sync_enabled
        print('正在启动商城服务…', flush=True)
        try:
            application = MallApplication()
            with make_server(arguments.host, arguments.port, application, server_class=LocalServer) as server, ExitStack() as workers:
                workers.enter_context(legacy_expiration_worker(application.state.database))
                if sync_enabled():
                    from app.search_sync import legacy_search_sync_worker
                    workers.enter_context(legacy_search_sync_worker(application.state.database))
                print(f'启动成功！黑马商城：http://{arguments.host}:{arguments.port}', flush=True)
                print(f'API 文档：http://{arguments.host}:{arguments.port}/docs', flush=True)
                print('请保持此终端窗口打开。按 Ctrl+C 停止服务。', flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            print('\n商城服务已停止。')
        except OSError as error:
            hint = '可用 --port 指定其他端口。' if error.errno == errno.EADDRINUSE else '请检查当前环境是否允许启动本地服务。'
            parser.exit(1, f'启动失败：{error}\n{hint}\n')
        except (RuntimeError, ValueError) as error:
            parser.exit(1, f'启动失败：{error}\n')


if __name__ == '__main__':
    main()
