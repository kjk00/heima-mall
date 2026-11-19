#!/bin/zsh
# macOS: double-click this file in Finder to start the local shop.
set -u

MALL_PROJECT_DIR="${0:A:h}"
cd -- "$MALL_PROJECT_DIR" || exit 1

MALL_PYTHON=""
for MALL_CANDIDATE in "$MALL_PROJECT_DIR/.venv/bin/python" python3.15 python3.14 python3.13 python3.12 python3; do
    if command -v "$MALL_CANDIDATE" >/dev/null 2>&1 && "$MALL_CANDIDATE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
        MALL_PYTHON="$MALL_CANDIDATE"
        break
    fi
done

if [[ -z "$MALL_PYTHON" ]]; then
    print '未找到 Python 3.12 或更高版本，请先安装 Python 后重新启动。'
    read -r '?按回车退出…'
    exit 1
fi

"$MALL_PYTHON" run.py --backend fastapi
MALL_EXIT_STATUS=$?
if (( MALL_EXIT_STATUS != 0 )); then
    print '商城未能启动，请查看上方的错误信息。'
    read -r '?按回车退出…'
fi
exit "$MALL_EXIT_STATUS"
