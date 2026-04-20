#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 连接上下文管理器
提取 "创建连接→登录→操作→关闭" 的通用模式
"""

import logging
from typing import Optional, Callable, Any, TypeVar
from contextlib import contextmanager
from mikrotik_api import MikroTikAPI

logger = logging.getLogger(__name__)

T = TypeVar('T')


class APIConnectionError(Exception):
    """API 连接错误"""
    pass


@contextmanager
def api_connection(ip: str, username: str, password: str, 
                   port: int = 8728, use_ssl: bool = False):
    """API 连接上下文管理器
    
    用法:
        with api_connection(ip, username, password) as api:
            result = api.get_interfaces()
    
    Args:
        ip: 设备 IP 地址
        username: 用户名
        password: 密码
        port: API 端口，默认 8728
        use_ssl: 是否使用 SSL
    
    Yields:
        MikroTikAPI: 已登录的 API 连接对象
    
    Raises:
        APIConnectionError: 连接或登录失败时抛出
    """
    mt_api = None
    try:
        mt_api = MikroTikAPI(ip, username, password, port=port, use_ssl=use_ssl)
        success, message = mt_api.login()
        
        if not success:
            raise APIConnectionError(f"连接设备 {ip} 失败: {message}")
        
        logger.debug(f"API 连接已建立: {ip}")
        yield mt_api
        
    finally:
        if mt_api:
            try:
                mt_api.close()
                logger.debug(f"API 连接已关闭: {ip}")
            except Exception as e:
                logger.warning(f"关闭 API 连接异常: {ip} - {e}")


def with_api_connection(ip: str, username: str, password: str,
                        port: int = 8728, use_ssl: bool = False):
    """API 连接装饰器工厂
    
    用法:
        @with_api_connection(ip, username, password)
        def get_data(api):
            return api.get_interfaces()
    
    Args:
        ip: 设备 IP 地址
        username: 用户名
        password: 密码
        port: API 端口
        use_ssl: 是否使用 SSL
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable[[MikroTikAPI], T]) -> Callable[[], T]:
        def wrapper(*args, **kwargs) -> T:
            with api_connection(ip, username, password, port, use_ssl) as api:
                return func(api, *args, **kwargs)
        return wrapper
    return decorator


def execute_with_api(ip: str, username: str, password: str,
                     operation: Callable[[MikroTikAPI], T],
                     port: int = 8728, use_ssl: bool = False,
                     error_default: T = None) -> T:
    """使用 API 连接执行操作（函数式风格）
    
    用法:
        result = execute_with_api(ip, username, password, 
                                  lambda api: api.get_interfaces(),
                                  error_default=[])
    
    Args:
        ip: 设备 IP 地址
        username: 用户名
        password: 密码
        operation: 要执行的操作函数，参数为 MikroTikAPI 对象
        port: API 端口
        use_ssl: 是否使用 SSL
        error_default: 出错时返回的默认值
    
    Returns:
        操作结果，出错时返回 error_default
    """
    try:
        with api_connection(ip, username, password, port, use_ssl) as api:
            return operation(api)
    except APIConnectionError as e:
        logger.error(f"API 操作失败: {ip} - {e}")
        return error_default
    except Exception as e:
        logger.error(f"API 操作异常: {ip} - {e}")
        return error_default
