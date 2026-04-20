#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSL Context 工厂模块
提供全局复用的 SSL Context 单例
"""

import ssl
import logging

logger = logging.getLogger(__name__)


class SSLContextFactory:
    """SSL Context 工厂 - 单例模式，全局复用 SSL Context"""
    
    _instance: SSLContextFactory | None = None
    _no_verify_context: ssl.SSLContext | None = None
    _verified_context: ssl.SSLContext | None = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_no_verify_context(cls) -> ssl.SSLContext:
        """获取不验证证书的 SSL Context（用于 MikroTik 自签名证书场景）
        
        Returns:
            ssl.SSLContext: 不验证证书的 SSL 上下文
        """
        instance = cls()
        if instance._no_verify_context is None:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            instance._no_verify_context = context
            logger.debug("已创建不验证证书的 SSL Context 单例")
        return instance._no_verify_context
    
    @classmethod
    def get_verified_context(cls) -> ssl.SSLContext:
        """获取验证证书的 SSL Context
        
        Returns:
            ssl.SSLContext: 验证证书的 SSL 上下文
        """
        instance = cls()
        if instance._verified_context is None:
            context = ssl.create_default_context()
            instance._verified_context = context
            logger.debug("已创建验证证书的 SSL Context 单例")
        return instance._verified_context
    
    @classmethod
    def get_server_context(cls, cert_file: str, key_file: str) -> ssl.SSLContext:
        """获取服务端 SSL Context（用于 HTTPS/WSS 服务）
        
        Args:
            cert_file: 证书文件路径
            key_file: 私钥文件路径
            
        Returns:
            ssl.SSLContext: 服务端 SSL 上下文
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        logger.info(f"已创建服务端 SSL Context (cert={cert_file})")
        return context
    
    @classmethod
    def reset(cls):
        """重置所有缓存的 SSL Context（用于测试或配置变更）"""
        instance = cls()
        instance._no_verify_context = None
        instance._verified_context = None
        logger.debug("已重置 SSL Context 缓存")


# 便捷函数
def get_ssl_context() -> ssl.SSLContext:
    """获取不验证证书的 SSL Context（最常用场景的快捷方式）"""
    return SSLContextFactory.get_no_verify_context()


def get_server_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    """获取服务端 SSL Context"""
    return SSLContextFactory.get_server_context(cert_file, key_file)
