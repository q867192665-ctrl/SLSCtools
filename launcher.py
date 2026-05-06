#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChinanTool 启动器
- 检测后端程序是否运行
- 未运行则启动后端
- 打开前端网页
- 每次只打开1次网页
"""

import os
import sys
import time
import socket
import subprocess
import webbrowser
import logging
import codecs

# 配置
HTTP_PORT = 32995
MAX_WAIT_SECONDS = 30
CHECK_INTERVAL = 0.5


def get_base_dir():
    """获取程序根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_log_dir():
    """获取日志目录（优先使用用户可写目录）"""
    local_app = os.environ.get('LOCALAPPDATA', '')
    if local_app:
        log_dir = os.path.join(local_app, 'ChinanTool')
    else:
        log_dir = get_base_dir()
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


# PyInstaller 打包后日志写入文件，开发模式输出到控制台
if getattr(sys, 'frozen', False):
    _log_file = os.path.join(get_log_dir(), 'launcher.log')
    # Python 3.8 兼容：使用 codecs.open() + StreamHandler 代替 encoding 参数
    _log_stream = codecs.open(_log_file, mode='a', encoding='utf-8')
    _handler = logging.StreamHandler(_log_stream)
    _handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[_handler])
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('ChinanTool Launcher')


def is_port_open(port, host='127.0.0.1'):
    """检测端口是否在监听"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def start_backend():
    """启动后端程序"""
    base_dir = get_base_dir()
    backend_exe = os.path.join(base_dir, 'chinantool_backend.exe')

    if os.path.exists(backend_exe):
        # PyInstaller 打包后的可执行文件
        logger.info(f"启动后端: {backend_exe}")
        subprocess.Popen(
            [backend_exe],
            cwd=base_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            close_fds=True
        )
    else:
        # 开发模式：用 Python 启动
        main_py = os.path.join(base_dir, 'main.py')
        python_exe = sys.executable
        logger.info(f"启动后端: {python_exe} {main_py}")
        subprocess.Popen(
            [python_exe, main_py],
            cwd=base_dir,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            close_fds=True
        )


def wait_for_backend():
    """等待后端启动完成"""
    logger.info(f"等待后端启动（端口 {HTTP_PORT}）...")
    elapsed = 0
    while elapsed < MAX_WAIT_SECONDS:
        if is_port_open(HTTP_PORT):
            logger.info("后端已启动")
            return True
        time.sleep(CHECK_INTERVAL)
        elapsed += CHECK_INTERVAL
    logger.error(f"后端启动超时（{MAX_WAIT_SECONDS}秒）")
    return False


def open_browser():
    """打开前端网页"""
    url = f'http://localhost:{HTTP_PORT}'
    logger.info(f"打开浏览器: {url}")
    webbrowser.open(url)


def main():
    logger.info("=" * 40)
    logger.info("ChinanTool 启动器")
    logger.info("=" * 40)

    backend_running = is_port_open(HTTP_PORT)

    if backend_running:
        logger.info("后端已在运行，直接打开网页")
    else:
        logger.info("后端未运行，正在启动...")
        start_backend()
        if not wait_for_backend():
            logger.error("后端启动失败，请检查日志")
            input("按回车键退出...")
            sys.exit(1)

    open_browser()
    logger.info("启动完成")


if __name__ == '__main__':
    main()
