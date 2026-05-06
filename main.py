#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChinanTool - MikroTik 网络设备管理工具
主入口文件
"""

import os
import sys
import platform
import logging
import yaml
import codecs


def get_base_dir():
    """获取程序根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    """加载配置文件"""
    config_path = os.path.join(get_base_dir(), 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def setup_logging(config: dict):
    """配置日志"""
    log_level = config.get('logging', {}).get('level', 'INFO')
    log_format = config.get('logging', {}).get('format', '%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # PyInstaller 打包后，将日志写入用户可写目录（不显示控制台窗口）
    if getattr(sys, 'frozen', False):
        local_app = os.environ.get('LOCALAPPDATA', '')
        if local_app:
            log_dir = os.path.join(local_app, 'ChinanTool')
        else:
            log_dir = get_base_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'chinantool.log')
        # Python 3.8 兼容：使用 codecs.open() + StreamHandler 代替 encoding 参数
        log_stream = codecs.open(log_file, mode='a', encoding='utf-8')
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(logging.Formatter(log_format))
        logging.basicConfig(
            level=getattr(logging, log_level),
            handlers=[handler]
        )
    else:
        logging.basicConfig(level=getattr(logging, log_level), format=log_format)
    
    # 抑制第三方库的过度日志
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    logging.getLogger('websockets').setLevel(logging.WARNING)


def check_admin():
    """检查管理员权限（Windows）"""
    if platform.system() == "Windows":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                logging.warning("建议以管理员权限运行以获得最佳效果（MNDP广播需要）")
        except Exception:
            pass


def main():
    """主入口"""
    # PyInstaller --noconsole 模式下 sys.stderr/stdout 为 None，
    # uvicorn 日志初始化会调用 isatty() 导致崩溃，需要用黑洞对象替代
    if getattr(sys, 'frozen', False):
        class NullWriter:
            def write(self, *args): pass
            def flush(self): pass
            def isatty(self): return False
        if sys.stdout is None:
            sys.stdout = NullWriter()
        if sys.stderr is None:
            sys.stderr = NullWriter()
    
    config = load_config()
    setup_logging(config)
    check_admin()
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("ChinanTool - MikroTik 网络设备管理工具")
    logger.info("=" * 50)
    
    host = config.get('server', {}).get('host', '0.0.0.0')
    http_port = config.get('server', {}).get('http_port', 32995)
    ws_port = config.get('server', {}).get('ws_port', 32996)
    tls_config = config.get('tls', {})
    
    logger.info(f"HTTP API 端口: {http_port}")
    logger.info(f"WebSocket 端口: {ws_port}")
    logger.info(f"TLS 启用: {tls_config.get('enabled', False)}")
    
    # 启动 API 服务器（通过 uvicorn）
    import uvicorn
    from api_server import app
    
    ssl_kwargs = {}
    if tls_config.get('enabled') and tls_config.get('cert_file') and tls_config.get('key_file'):
        from ssl_context import get_server_ssl_context
        ssl_kwargs['ssl'] = get_server_ssl_context(tls_config['cert_file'], tls_config['key_file'])
        logger.info(f"TLS 证书: {tls_config['cert_file']}")
    
    protocol = 'https' if ssl_kwargs else 'http'
    ws_protocol = 'wss' if ssl_kwargs else 'ws'
    logger.info(f"前端地址: {protocol}://localhost:{http_port}")
    logger.info(f"API 文档: {protocol}://localhost:{http_port}/docs")
    logger.info(f"WebSocket: {ws_protocol}://localhost:{ws_port}")
    logger.info("-" * 50)
    
    uvicorn.run(app, host=host, port=http_port, **ssl_kwargs)


if __name__ == '__main__':
    main()
