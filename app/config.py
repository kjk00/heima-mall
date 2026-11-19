"""Load local settings without overriding variables exported by the user."""
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def load_environment(path=None):
    file = Path(path) if path else ROOT / '.env'
    if not file.is_file():
        return
    for line in file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('.env配置格式不正确，请使用KEY=value')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
