#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MikroTik RouterOS API 客户端
支持 REST API (端口 443) 和 Legacy API (端口 8728/8729)
"""

import socket
import ssl
import hashlib
import base64
import json
import logging
import urllib.request
import urllib.error
import ftplib
import os
import re
import datetime
import time
import traceback
from typing import Optional, Tuple, List, Dict, Any
from ssl_context import get_ssl_context

logger = logging.getLogger(__name__)


class MikroTikAPI:
    """MikroTik RouterOS API 客户端"""
    
    def __init__(self, host: str, username: str, password: str, port: int = 8728, use_ssl: bool = False):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self.socket: Optional[socket.socket] = None
        self.logged_in = False
        self.api_version = None  # 'rest', 'legacy', 'legacy_ssl'
        self.last_response_time = time.time()
        self._cached_system_date = None
        self._cached_date_time = 0
        self._routeros_version = None
        self._routeros_major_version = None
        self._cached_identity = None
        self._cached_system_info = None

    def _rest_request(self, endpoint: str, timeout: int = 5) -> Optional[Any]:
        """发送 REST API 请求（辅助方法，复用 SSL Context 和认证逻辑）
        
        Args:
            endpoint: REST API 端点路径（如 '/rest/system/resource'）
            timeout: 请求超时时间（秒）
            
        Returns:
            解析后的 JSON 数据，失败返回 None
        """
        try:
            context = get_ssl_context()
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            req = urllib.request.Request(f"https://{self.host}{endpoint}")
            req.add_header('Authorization', f'Basic {encoded_credentials}')
            
            with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
                data = json.loads(response.read().decode())
                return data
        except Exception as e:
            logger.debug(f"REST API 请求失败 ({endpoint}): {e}")
            return None

    def update_last_response_time(self):
        """更新设备最后响应时间"""
        self.last_response_time = time.time()

    def get_last_response_time(self) -> float:
        """获取设备最后响应时间"""
        return self.last_response_time
    
    def login(self) -> Tuple[bool, str]:
        """
        尝试登录设备
        返回：(成功与否，消息)
        """
        errors = []

        # 尝试 Legacy API (端口 8728)
        success, error = self._try_legacy_login(use_ssl=False)
        if success:
            self.api_version = 'legacy'
            self.logged_in = True
            self._fetch_routeros_version()
            return True, "Legacy API 登录成功"
        else:
            errors.append(f"Legacy API: {error}")

        # 尝试 Legacy SSL API (端口 8729)
        success, error = self._try_legacy_login(use_ssl=True)
        if success:
            self.api_version = 'legacy_ssl'
            self.logged_in = True
            self._fetch_routeros_version()
            return True, "Legacy SSL API 登录成功"
        else:
            errors.append(f"Legacy SSL API: {error}")

        if not errors:
            errors.append("所有 API 版本登录失败（未知原因）")
        
        return False, "; ".join(errors)
    
    def _try_rest_login(self) -> bool:
        """尝试 REST API 登录"""
        try:
            context = get_ssl_context()
            
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            req = urllib.request.Request(f"https://{self.host}/rest/system/resource")
            req.add_header('Authorization', f'Basic {encoded_credentials}')
            
            with urllib.request.urlopen(req, timeout=5, context=context) as response:
                print(f"REST API 响应状态: {response.status}")
                if response.status == 200:
                    data = response.read()
                    print(f"REST API 响应内容长度: {len(data)}")
                    if len(data) > 0:
                        return True
            return False
        except urllib.error.HTTPError as e:
            print(f"REST API HTTP错误: {e.code} - {e.reason}")
            return False
        except Exception as e:
            print(f"REST API 登录失败：{e}")
            return False
    
    def _try_legacy_login(self, use_ssl: bool = False) -> Tuple[bool, str]:
        """尝试 Legacy API 登录，返回 (成功与否, 错误信息)"""
        port = 8729 if use_ssl else 8728
        context = None
        last_error = None
        try:
            # 创建 socket 连接
            if use_ssl:
                context = get_ssl_context()
                self.socket = context.wrap_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            else:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            self.socket.settimeout(5)
            self.socket.connect((self.host, port))
            self.use_ssl = use_ssl
            
            # 启用 TCP Keepalive 以快速检测死连接
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # 设置 keepalive 参数（如果平台支持）
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)  # 10秒后开始探测
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)   # 每2秒探测一次
                if hasattr(socket, 'TCP_KEEPCNT'):
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)      # 最多探测3次
                print(f"[登录] TCP Keepalive 已启用")
            except Exception as ke_err:
                print(f"[登录] 启用 Keepalive 失败: {ke_err}")
            
            # 尝试明文登录（RouterOS 6.43+）
            result, error = self._plaintext_login()
            if result:
                return True, ""
            if error:
                last_error = error
            
            # 如果明文登录失败，尝试挑战 - 响应登录
            self.socket.close()
            if use_ssl and context:
                self.socket = context.wrap_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            else:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            self.socket.connect((self.host, port))
            
            result, error = self._challenge_login()
            if result:
                return True, ""
            if error:
                last_error = error
            
            return False, last_error or "登录失败"
        except Exception as e:
            error_msg = str(e)
            if "登录失败" in error_msg or "用户名或密码错误" in error_msg:
                return False, "用户名或密码错误"
            print(f"Legacy API 登录失败 (SSL={use_ssl}): {e}")
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
            self.socket = None
            return False, str(e)
    
    def _plaintext_login(self) -> Tuple[bool, str]:
        """明文登录（RouterOS 6.43+），返回 (成功与否, 错误信息)"""
        try:
            # 发送登录命令
            self.write_sentence(['/login', '=name=' + self.username, '=password=' + self.password])
            response = self.read_sentence()
            
            print(f"明文登录响应: {response}")
            
            if response and response[0] == '!done':
                return True, ""
            elif response and any('failure' in str(r).lower() or 'bad' in str(r).lower() or 'trap' in str(r).lower() for r in response):
                return False, "用户名或密码错误"
            return False, "登录失败"
        except Exception as e:
            print(f"明文登录异常: {e}")
            if "用户名或密码错误" in str(e):
                return False, "用户名或密码错误"
            return False, str(e)
    
    def _challenge_login(self) -> Tuple[bool, str]:
        """挑战 - 响应登录，返回 (成功与否, 错误信息)"""
        try:
            # 发送登录请求获取挑战码
            self.write_sentence(['/login'])
            response = self.read_sentence()
            
            # 解析挑战码
            challenge = None
            for line in response:
                if line.startswith('=ret='):
                    challenge = line[5:]
                    break
            
            if not challenge:
                return False, "无法获取挑战码"
            
            # 计算响应
            # 根据官方文档：MD5(0x00 + password + challenge)
            md5_hash = hashlib.md5()
            md5_hash.update(b'\x00')
            md5_hash.update(self.password.encode())
            md5_hash.update(bytes.fromhex(challenge))
            
            response_hash = md5_hash.hexdigest()
            
            # 发送响应
            self.write_sentence(['/login', '=name=' + self.username, '=response=' + response_hash])
            login_response = self.read_sentence()
            
            if login_response and login_response[0] == '!done':
                return True, ""
            elif login_response and any('failure' in str(r).lower() or 'bad' in str(r).lower() or 'trap' in str(r).lower() for r in login_response):
                return False, "用户名或密码错误"
            return False, "登录失败"
        except Exception as e:
            if "用户名或密码错误" in str(e):
                return False, "用户名或密码错误"
            print(f"挑战 - 响应登录失败：{e}")
            return False, str(e)
    
    def write_sentence(self, sentence: List[str]):
        """发送 API 命令"""
        if not self.socket:
            raise Exception("Socket not connected")
        
        for word in sentence:
            # 编码 word
            encoded_word = word.encode('utf-8')
            length = len(encoded_word)
            
            # 发送长度前缀
            if length < 0x80:
                self.socket.send(bytes([length]))
            elif length < 0x4000:
                self.socket.send(bytes([0x80 | (length >> 8), length & 0xFF]))
            elif length < 0x200000:
                self.socket.send(bytes([0xC0 | (length >> 16), (length >> 8) & 0xFF, length & 0xFF]))
            elif length < 0x10000000:
                self.socket.send(bytes([0xE0 | (length >> 24), (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF]))
            else:
                self.socket.send(bytes([0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF]))
            
            # 发送 word
            self.socket.send(encoded_word)
        
        # 发送空 word 表示句子结束
        self.socket.send(b'\x00')
    
    def read_sentence(self, timeout: float = 30) -> List[str]:
        """读取 API 响应

        Args:
            timeout: 读取超时时间（秒）
        """
        if not self.socket:
            raise Exception("Socket not connected")

        old_timeout = self.socket.gettimeout()
        self.socket.settimeout(timeout)

        sentence = []
        try:
            while True:
                # 读取长度
                length_byte = self.socket.recv(1)
                if not length_byte:
                    break
                
                length = length_byte[0]
                
                # 根据第一个字节判断实际长度
                if length < 0x80:
                    pass  # 长度就是 length
                elif length < 0xC0:
                    # 2 字节长度
                    next_byte = self.socket.recv(1)
                    length = ((length & 0x3F) << 8) | next_byte[0]
                elif length < 0xE0:
                    # 3 字节长度
                    next_bytes = self.socket.recv(2)
                    length = ((length & 0x1F) << 16) | (next_bytes[0] << 8) | next_bytes[1]
                elif length < 0xF0:
                    # 4 字节长度
                    next_bytes = self.socket.recv(3)
                    length = ((length & 0x0F) << 24) | (next_bytes[0] << 16) | (next_bytes[1] << 8) | next_bytes[2]
                else:
                    # 5 字节长度
                    next_bytes = self.socket.recv(4)
                    length = (next_bytes[0] << 24) | (next_bytes[1] << 16) | (next_bytes[2] << 8) | next_bytes[3]
                
                # 读取数据
                if length == 0:
                    break  # 空 word 表示句子结束
                
                data = self.socket.recv(length)
                decoded = False
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                    try:
                        sentence.append(data.decode(encoding))
                        decoded = True
                        break
                    except:
                        continue
                if not decoded:
                    sentence.append(data.decode('utf-8', errors='replace'))
                self.last_response_time = time.time()
        except ConnectionResetError:
            print("ConnectionResetError: 远程主机强迫关闭了连接")
            self.logged_in = False
            self.socket.settimeout(old_timeout)
            raise
        except Exception as e:
            print(f"读取响应失败：{e}")
            self.logged_in = False
            self.socket.settimeout(old_timeout)
            raise

        self.socket.settimeout(old_timeout)
        return sentence
    
    def get_system_info(self, force_refresh: bool = False) -> Dict[str, Any]:
        """获取系统信息"""
        if self._cached_system_info and not force_refresh:
            return self._cached_system_info

        if not self.logged_in:
            return {}

        try:
            if self.api_version == 'rest':
                context = get_ssl_context()

                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()

                req = urllib.request.Request(f"https://{self.host}/rest/system/resource")
                req.add_header('Authorization', f'Basic {encoded_credentials}')

                with urllib.request.urlopen(req, timeout=5, context=context) as response:
                    data = json.loads(response.read().decode())
                    print(f"[get_system_info] REST response: {data}")
                    if isinstance(data, list) and len(data) > 0:
                        self._cached_system_info = data[0]
                    elif isinstance(data, dict):
                        self._cached_system_info = data
                    else:
                        self._cached_system_info = {}
                    return self._cached_system_info
            else:
                if not self.socket:
                    return {}

                self.write_sentence(['/system/resource/print'])

                info = {}
                while True:
                    response = self.read_sentence()
                    if not response:
                        break
                    for line in response:
                        if line.startswith('='):
                            key_value = line[1:].split('=', 1)
                            if len(key_value) == 2:
                                info[key_value[0]] = key_value[1]
                    if '!done' in response:
                        break

                print(f"[get_system_info] Legacy response: {info}")
                self._cached_system_info = info
                return self._cached_system_info
        except Exception as e:
            print(f"获取系统信息失败：{e}")
            return {}

    def get_routeros_version(self) -> Tuple[Optional[str], Optional[int]]:
        """获取 RouterOS 版本信息

        Returns:
            (版本字符串, 主版本号) 如 ('7.20.2', 7) 或 ('6.49.10', 6)
        """
        if self._routeros_version and self._routeros_major_version:
            return self._routeros_version, self._routeros_major_version
        return None, None

    def _fetch_routeros_version(self):
        """登录后自动获取 RouterOS 版本（内部方法）"""
        try:
            info = self.get_system_info()
            version_str = info.get('version', '')
            if version_str:
                self._routeros_version = version_str
                match = re.match(r'(\d+)\.', version_str)
                if match:
                    self._routeros_major_version = int(match.group(1))
                print(f"[版本] RouterOS 版本: {version_str}, 主版本: {self._routeros_major_version}")
        except Exception as e:
            print(f"[版本] 获取版本失败: {e}")

    def is_ros7_or_later(self) -> bool:
        """判断是否为 RouterOS 7.x 或更高版本"""
        return self._routeros_major_version is not None and self._routeros_major_version >= 7

    def is_ros6_or_earlier(self) -> bool:
        """判断是否为 RouterOS 6.x 或更早版本"""
        return self._routeros_major_version is not None and self._routeros_major_version < 7

    def get_identity(self, force_refresh: bool = False) -> Optional[str]:
        """获取设备名称（通过/system/identity/print命令）"""
        if self._cached_identity and not force_refresh:
            return self._cached_identity
        
        if not self.logged_in:
            success, message = self.login()
            if not success:
                return None
        
        try:
            if self.api_version == 'rest':
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                
                req = urllib.request.Request(f"https://{self.host}/rest/system/identity")
                req.add_header('Authorization', f'Basic {encoded_credentials}')
                
                with urllib.request.urlopen(req, timeout=5, context=context) as response:
                    data = json.loads(response.read().decode())
                    if isinstance(data, list) and len(data) > 0:
                        self._cached_identity = data[0].get('name', self.host)
                        return self._cached_identity
                    elif isinstance(data, dict):
                        self._cached_identity = data.get('name', self.host)
                        return self._cached_identity
                    return self.host
            else:
                if not self.socket:
                    success, message = self.login()
                    if not success:
                        return None
                
                for attempt in range(5):
                    try:
                        self.write_sentence(['/system/identity/print'])
                        response = self.read_sentence()
                        
                        for line in response:
                            if line.startswith('=name='):
                                self._cached_identity = line[6:]
                                return self._cached_identity
                        
                        if '!done' in response:
                            break
                        
                        time.sleep(0.5)
                    except Exception as e:
                        time.sleep(0.5)
                
                return self.host
        except Exception as e:
            return self.host

    def _get_system_date(self) -> str:
        """获取设备系统日期

        Returns:
            系统日期字符串，格式如 'mar/20/2026'，失败返回 None
        """
        try:
            self.write_sentence(['/system/clock/print'])
            lines = []
            while True:
                sen = self.read_sentence()
                if not sen or '!done' in sen:
                    break
                lines.extend(sen)

            for line in lines:
                if line.startswith('=date='):
                    return line[6:]  # 返回如 'mar/20/2026'
            return None
        except Exception as e:
            print(f"获取系统日期失败: {e}")
            return None

    def _normalize_log_time(self, time_str: str, system_date: str = None) -> str:
        """标准化日志时间格式为 YYYY-MM-DD HH:MM:SS

        Args:
            time_str: 原始时间字符串，如 'mar/07 09:44:29' 或 '12:30:38'
            system_date: 系统日期字符串，如 'mar/20/2026'

        Returns:
            标准化后的时间字符串，如 '2026-03-07 09:44:29'
        """
        if not time_str:
            return time_str

        if time_str.startswith('20') and '-' in time_str:
            return time_str

        time_str = re.sub(r':+', ':', time_str)
        time_str = re.sub(r'\s+', ' ', time_str.strip())

        if '/' in time_str:
            try:
                parts = time_str.split()
                if len(parts) == 1:
                    date_part = parts[0]
                    time_str = f"{date_part} 00:00:00"
                elif len(parts) == 2:
                    date_part, time_part = parts
                    time_components = time_part.split(':')
                    if len(time_components) == 2:
                        time_part = time_part + ':00'
                        time_str = date_part + ' ' + time_part
                    elif len(time_components) == 3:
                        h, m, s = time_components
                        h = h.zfill(2)
                        m = m.zfill(2) if m else '00'
                        s = s.zfill(2) if s else '00'
                        time_str = f"{date_part} {h}:{m}:{s}"
                    else:
                        return time_str

                dt = datetime.datetime.strptime(time_str, '%b/%d %H:%M:%S')
                if system_date:
                    try:
                        sys_dt = datetime.datetime.strptime(system_date, '%b/%d/%Y')
                        dt = dt.replace(year=sys_dt.year)
                    except:
                        pass
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                print(f"[警告] 时间解析失败: '{time_str}', 错误: {e}")

        if ':' in time_str:
            time_parts = time_str.split(':')
            if len(time_parts) == 2:
                try:
                    if system_date:
                        try:
                            sys_dt = datetime.datetime.strptime(system_date, '%b/%d/%Y')
                            return f"{sys_dt.year}-{sys_dt.month:02d}-{sys_dt.day:02d} {time_parts[0].zfill(2)}:{time_parts[1].zfill(2)}:00"
                        except:
                            pass
                    now = datetime.datetime.now()
                    return f"{now.year}-{now.month:02d}-{now.day:02d} {time_parts[0].zfill(2)}:{time_parts[1].zfill(2)}:00"
                except:
                    pass
            elif len(time_parts) == 3:
                try:
                    h = time_parts[0].zfill(2)
                    m = time_parts[1].zfill(2) if time_parts[1] else '00'
                    s = time_parts[2].zfill(2) if time_parts[2] else '00'
                    fixed_time = f"{h}:{m}:{s}"
                    if system_date:
                        try:
                            sys_dt = datetime.datetime.strptime(system_date, '%b/%d/%Y')
                            return f"{sys_dt.year}-{sys_dt.month:02d}-{sys_dt.day:02d} {fixed_time}"
                        except:
                            pass
                    now = datetime.datetime.now()
                    return f"{now.year}-{now.month:02d}-{now.day:02d} {fixed_time}"
                except:
                    pass

        return time_str

    def _compare_log_ids(self, id1: str, id2: str) -> int:
        """比较两个日志 ID 的大小

        Args:
            id1: 第一个日志 ID（如 '*FF'）
            id2: 第二个日志 ID（如 '*100'）

        Returns:
            正数表示 id1 > id2，负数表示 id1 < id2，0 表示相等
        """
        try:
            int_id1 = int(id1.lstrip('*'), 16)
            int_id2 = int(id2.lstrip('*'), 16)
            
            # 考虑 16 位计数器回绕的情况
            # 当差值超过 32768（2^15）时，认为发生了回绕
            diff = int_id1 - int_id2
            if abs(diff) > 32768:
                # 发生回绕，反转比较结果
                if diff > 0:
                    return -1
                else:
                    return 1
            return diff
        except (ValueError, TypeError):
            return 0

    def get_logs(self, last_id: str = None, limit: int = 0, since_time: str = None) -> List[Dict[str, str]]:
        """获取设备日志

        Args:
            last_id: 上次获取的最后一条日志的 .id，返回该 ID 之后的新日志
            limit: 限制返回的日志条数，0 表示不限制
            since_time: 增量查询时间起点（设备原始格式，如 'apr/15 02:05:17'）

        Returns:
            日志列表，每条日志包含 id, time, topics, message, raw_time 字段
        """
        try:
            if not self.logged_in:
                print("get_logs: 未登录，尝试重新登录...")
                success, message = self.login()
                if not success:
                    print(f"get_logs: 重新登录失败: {message}")
                    return []

            is_ros7 = self.is_ros7_or_later()

            current_time = time.time()
            if self._cached_system_date and (current_time - self._cached_date_time) < 300:
                system_date = self._cached_system_date
            else:
                system_date = self._get_system_date()
                if system_date:
                    self._cached_system_date = system_date
                    self._cached_date_time = current_time

            command = ['/log/print', '=.proplist=.id,time,topics,message']

            print(f"get_logs: 获取日志 (limit={limit})")

            start_time = time.time()
            max_total_time = 60
            max_consecutive_timeouts = 3
            consecutive_timeouts = 0
            self.write_sentence(command)

            all_lines = []
            sentence_count = 0
            connection_reset = False
            while True:
                if hasattr(self, '_stop_event') and self._stop_event and self._stop_event.is_set():
                    print("get_logs: 收到停止信号，终止读取")
                    break
                if time.time() - start_time > max_total_time:
                    print(f"get_logs: 总耗时超过 {max_total_time} 秒，强制退出")
                    break
                try:
                    sentence = self.read_sentence(timeout=10)
                    consecutive_timeouts = 0
                    sentence_count += 1
                    if not sentence:
                        break
                    all_lines.extend(sentence)
                    if '!done' in sentence:
                        break
                    if sentence_count > 50000:
                        print(f"get_logs: 超过 50000 次读取，强制退出")
                        break
                except socket.timeout:
                    consecutive_timeouts += 1
                    print(f"get_logs: 读取超时 ({consecutive_timeouts}/{max_consecutive_timeouts})，继续等待...")
                    if consecutive_timeouts >= max_consecutive_timeouts:
                        print(f"get_logs: 连续 {max_consecutive_timeouts} 次超时，终止读取")
                        connection_reset = True
                        self.logged_in = False
                        break
                    continue
                except ConnectionResetError:
                    print("get_logs: 连接被重置，标记需要重新连接")
                    connection_reset = True
                    self.logged_in = False
                    break
                except Exception as e:
                    print(f"get_logs: 读取异常: {e}")
                    connection_reset = True
                    self.logged_in = False
                    break

            elapsed = time.time() - start_time
            print(f"get_logs: API 耗时: {elapsed:.2f}秒, {sentence_count} 次读取")

            if not all_lines:
                return []

            logs = []
            current_entry = {}
            max_keep = limit if limit > 0 else 0

            for line in all_lines:
                if line.startswith('!re'):
                    if current_entry and 'id' in current_entry and 'time' in current_entry:
                        logs.append(current_entry)
                        if max_keep > 0 and len(logs) > max_keep:
                            logs.pop(0)
                    current_entry = {}
                    continue

                if line.startswith('!done') or line.startswith('!trap'):
                    if current_entry and 'id' in current_entry and 'time' in current_entry:
                        logs.append(current_entry)
                        if max_keep > 0 and len(logs) > max_keep:
                            logs.pop(0)
                    break

                if line.startswith('='):
                    parts = line[1:].split('=', 1)
                    if len(parts) == 2:
                        key, value = parts
                        if key == '.id':
                            key = 'id'
                        if key == 'time':
                            current_entry['raw_time'] = value.lower()
                            if not is_ros7:
                                value = self._normalize_log_time(value, system_date)
                        current_entry[key] = value

            print(f"get_logs: 解析出 {len(logs)} 条日志")

            if last_id:
                last_idx = -1
                for i in range(len(logs) - 1, -1, -1):
                    if logs[i].get('id') == last_id:
                        last_idx = i
                        break
                if last_idx >= 0:
                    logs = logs[last_idx + 1:]
                    print(f"get_logs: 增量过滤后剩余 {len(logs)} 条新日志")
                else:
                    print(f"get_logs: 未找到 last_id={last_id}，返回全部日志")

            if connection_reset:
                self.logged_in = False
            else:
                self.logged_in = True
            if limit > 0 and len(logs) > limit:
                print(f"get_logs: 限制返回最后 {limit} 条日志")
                return logs[-limit:]
            return logs

        except ConnectionResetError:
            print("get_logs: 连接被重置")
            self.logged_in = False
            return []
        except socket.timeout as e:
            print(f"get_logs: 连接超时: {e}")
            self.logged_in = False
            return []
        except Exception as e:
            print(f"获取日志失败：{e}")
            traceback.print_exc()
            self.logged_in = False
            return []

    def get_new_logs(self, last_id: str = None, limit: int = 100) -> List[Dict[str, str]]:
        """获取新日志（通过 last_id 去重）

        Args:
            last_id: 上次获取的最后一条日志的 .id，返回该 ID 之后的新日志
            limit: 最大返回条数

        Returns:
            新日志列表
        """
        try:
            if not self.logged_in:
                print("get_new_logs: 未登录，尝试重新登录...")
                success, message = self.login()
                if not success:
                    print(f"get_new_logs: 重新登录失败: {message}")
                    return []

            is_ros7 = self.is_ros7_or_later()
            system_date = self._get_system_date()

            command = ['/log/print', '=.proplist=.id,time,topics,message']
            print(f"get_new_logs: 获取日志 (last_id={last_id}, limit={limit})")

            self.write_sentence(command)

            all_logs = []
            current_entry = {}
            start_time = time.time()
            max_time = 30

            while True:
                if time.time() - start_time > max_time:
                    print(f"get_new_logs: 超时，已获取 {len(all_logs)} 条")
                    break
                try:
                    sentence = self.read_sentence(timeout=5)
                    if not sentence:
                        break
                    for line in sentence:
                        if line.startswith('!re'):
                            if current_entry and 'time' in current_entry:
                                all_logs.append(current_entry)
                            current_entry = {}
                            continue
                        if line.startswith('!done') or line.startswith('!trap'):
                            break
                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                if key == '.id':
                                    key = 'id'
                                if key == 'time':
                                    current_entry['raw_time'] = value.lower()
                                    if not is_ros7:
                                        value = self._normalize_log_time(value, system_date)
                                current_entry[key] = value
                    if '!done' in sentence or '!trap' in sentence:
                        break
                except socket.timeout:
                    break
                except Exception as e:
                    print(f"get_new_logs: 读取异常: {e}")
                    break

            if not last_id:
                logs = all_logs[-limit:] if len(all_logs) > limit else all_logs
            else:
                found_idx = -1
                for i in range(len(all_logs) - 1, -1, -1):
                    if all_logs[i].get('id') == last_id:
                        found_idx = i
                        break
                if found_idx >= 0:
                    logs = all_logs[found_idx + 1:]
                else:
                    logs = all_logs[-limit:] if len(all_logs) > limit else all_logs

            print(f"get_new_logs: 总共 {len(all_logs)} 条，返回 {len(logs)} 条新日志")
            return logs

        except Exception as e:
            print(f"get_new_logs 失败: {e}")
            return []

    def follow_logs(self, callback, stop_event=None, timeout=5):
        """实时获取新日志（follow模式）

        Args:
            callback: 回调函数，接收日志字典
            stop_event: 停止事件，用于外部中断
            timeout: 每次读取超时时间（秒）

        Returns:
            获取的日志数量
        """
        try:
            if not self.logged_in:
                print("follow_logs: 未登录，尝试重新登录...")
                success, message = self.login()
                if not success:
                    print(f"follow_logs: 重新登录失败: {message}")
                    return 0

            is_ros7 = self.is_ros7_or_later()
            system_date = self._get_system_date()

            command = ['/log/print', '=follow=yes', '=.proplist=.id,time,topics,message']
            print(f"follow_logs: 开始follow模式")
            self.write_sentence(command)

            count = 0
            current_entry = {}

            while True:
                if stop_event and stop_event.is_set():
                    print("follow_logs: 收到停止信号")
                    break

                try:
                    sentence = self.read_sentence(timeout=timeout)
                    if not sentence:
                        continue

                    for line in sentence:
                        if line.startswith('!re'):
                            if current_entry and 'time' in current_entry:
                                callback(current_entry)
                                count += 1
                            current_entry = {}
                            continue

                        if line.startswith('!done') or line.startswith('!trap'):
                            if current_entry and 'time' in current_entry:
                                callback(current_entry)
                                count += 1
                            current_entry = {}
                            continue

                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                if key == '.id':
                                    key = 'id'
                                if key == 'time':
                                    current_entry['raw_time'] = value
                                    if not is_ros7:
                                        value = self._normalize_log_time(value, system_date)
                                current_entry[key] = value

                except socket.timeout:
                    if stop_event and stop_event.is_set():
                        break
                    continue
                except Exception as e:
                    if stop_event and stop_event.is_set():
                        break
                    print(f"follow_logs: 读取异常: {e}")
                    break

            return count

        except Exception as e:
            print(f"follow_logs 失败: {e}")
            return 0
    
    def _parse_api_log_line(self, line: str) -> Optional[Dict[str, str]]:
        """解析API返回的键值对格式日志行
        
        格式: =time=xxx=topics=xxx=message=xxx
        
        Args:
            line: 日志行
            
        Returns:
            解析后的日志字典，失败返回 None
        """
        try:
            line = line.strip()
            if not line:
                return None
            
            # 解析键值对
            log_entry = {}
            parts = line.split('=')
            i = 1  # 跳过第一个空字符串
            while i < len(parts):
                if i + 1 < len(parts):
                    key = parts[i]
                    # 找到下一个键的位置
                    value_parts = []
                    j = i + 1
                    while j < len(parts):
                        # 检查是否是已知键
                        if parts[j] in ['time', 'topics', 'message']:
                            break
                        value_parts.append(parts[j])
                        j += 1
                    value = '='.join(value_parts) if value_parts else ''
                    log_entry[key] = value
                    i = j
                else:
                    i += 1
            
            if 'time' in log_entry and 'message' in log_entry:
                return log_entry
            
            return None
            
        except Exception as e:
            print(f"解析API日志行失败：{e}")
            return None
    
    def download_log_file(self, local_dir: str = '.', device_mac: str = None) -> Tuple[bool, str, str]:
        """通过FTP下载日志文件（一次性获取历史日志）
        
        Args:
            local_dir: 本地保存目录
            device_mac: 设备MAC地址，用于区分多设备日志
            
        Returns:
            (成功与否, 消息, 本地文件路径)
        """
        ftp_host = self.host
        ftp_port = 6000
        ftp_user = self.username
        ftp_password = self.password
        remote_log_paths = ['/flash/log.0.txt', 'log.0.txt']
        
        os.makedirs(local_dir, exist_ok=True)
        
        if device_mac:
            device_id = device_mac.replace(':', '_').replace('-', '_')
            local_file_name = f"device_{device_id}.txt"
        else:
            local_file_name = f"device_unknown_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        local_file_path = os.path.join(local_dir, local_file_name)
        
        try:
            print(f"FTP 连接: {ftp_host}:{ftp_port}")
            ftp = ftplib.FTP()
            ftp.encoding = 'latin-1'
            ftp.connect(ftp_host, ftp_port, timeout=10)
            print(f"FTP 登录: {ftp_user}")
            ftp.login(ftp_user, ftp_password if ftp_password else '')
            print(f"✅ FTP连接成功：{ftp_host}:{ftp_port}")
            
            download_success = False
            last_error = None
            
            for remote_log_path in remote_log_paths:
                try:
                    print(f"FTP 尝试下载: {remote_log_path} -> {local_file_path}")
                    with open(local_file_path, 'wb') as f:
                        ftp.retrbinary(f"RETR {remote_log_path}", f.write)
                    print(f"✅ 日志文件下载完成：{local_file_path}")
                    download_success = True
                    break
                except ftplib.error_perm as e:
                    print(f"⚠️ 路径 {remote_log_path} 不存在或无权限: {e}")
                    last_error = e
                    continue
                except Exception as e:
                    print(f"⚠️ 路径 {remote_log_path} 下载失败: {e}")
                    last_error = e
                    continue
            
            ftp.quit()
            
            if download_success:
                return True, "FTP 下载成功", local_file_path
            else:
                return False, f"FTP 下载失败: 所有路径都无法访问 ({'; '.join(remote_log_paths)})", ''
            
        except ftplib.error_perm as e:
            print(f"❌ FTP权限错误: {e}（检查用户ftp权限/日志文件路径）")
            return False, f"FTP 权限错误: {e}", ''
        except Exception as e:
            print(f"❌ 日志文件下载失败: {str(e)}")
            traceback.print_exc()
            return False, f"FTP 下载失败: {e}", ''
    
    def get_log_updates(self, last_position: int = 0, local_file: str = None) -> Tuple[int, List[Dict[str, str]]]:
        """获取日志增量更新
        
        Args:
            last_position: 上次读取的位置
            local_file: 本地日志文件路径，如果为None则使用默认路径
            
        Returns:
            (新位置, 日志列表)
        """
        if local_file is None:
            local_file = 'log.0.txt'
        
        if not os.path.exists(local_file):
            return last_position, []
        
        try:
            with open(local_file, 'r', encoding='utf-8') as f:
                f.seek(last_position)
                content = f.read()
                
            new_position = f.tell()
            
            if not content.strip():
                return new_position, []
            
            logs = []
            lines = content.strip().split('\n')
            
            for line in lines:
                if not line.strip():
                    continue
                
                log_entry = self._parse_log_line(line)
                if log_entry:
                    logs.append(log_entry)
            
            return new_position, logs
            
        except Exception as e:
            print(f"获取日志更新失败：{e}")
            traceback.print_exc()
            return last_position, []
    
    def _parse_log_line(self, line: str) -> Optional[Dict[str, str]]:
        """解析日志行
        
        支持格式：
        - API格式: 2026-02-06 20:23:55 system,info message
        - FTP格式: Feb/06/2026 20:23:55 system,info message
        
        Args:
            line: 日志行
            
        Returns:
            解析后的日志字典，失败返回 None
        """
        try:
            line = line.strip()
            if not line:
                return None
            
            # 尝试匹配FTP格式: [Feb/06/2026 20:23:55] system,info message
            # 或FTP格式: Feb/06/2026 20:23:55 system,info message
            # 或API格式: 2026-02-06 20:23:55 system,info message
            pattern = r'^\[?([\w]{3}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]?\s+(\S+)\s+(.*)$'
            match = re.match(pattern, line)
            
            if match:
                time_str = match.group(1)
                # 转换FTP格式时间为API格式
                if '/' in time_str:
                    try:
                        # FTP格式: Mar/07/2026 22:08:51
                        dt = datetime.strptime(time_str, '%b/%d/%Y %H:%M:%S')
                        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        pass
                
                return {
                    'time': time_str,
                    'topics': match.group(2),
                    'message': match.group(3)
                }
            
            return None
            
        except Exception as e:
            print(f"解析日志行失败：{e}")
            return None
    
    def get_interfaces(self) -> List[Dict[str, Any]]:
        """获取接口列表
        
        Returns:
            接口列表，每个接口包含 name, type, tx_rate, rx_rate, mtu 等字段
        """
        print(f"get_interfaces: logged_in={self.logged_in}, socket={self.socket is not None}")
        if not self.logged_in:
            print("get_interfaces: 未登录")
            return []
        
        return self._get_interfaces_legacy()
    
    def _get_interfaces_rest(self) -> List[Dict[str, Any]]:
        """使用 REST API 获取接口列表"""
        try:
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                'Authorization': f'Basic {encoded_credentials}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(
                f'https://{self.host}/rest/interface',
                headers=headers,
                verify=False,
                timeout=10
            )
            
            if response.status_code == 200:
                interfaces_data = response.json()
                result = []
                for iface in interfaces_data:
                    mac_address = iface.get('mac-address', '')
                    if mac_address:
                        mac_address = mac_address.upper()
                    
                    result.append({
                        'name': iface.get('name', ''),
                        'type': iface.get('type', ''),
                        'mac_address': mac_address,
                        'mtu': str(iface.get('mtu', '')),
                        'running': iface.get('running', False),
                        'disabled': iface.get('disabled', False),
                        'comment': iface.get('comment', '')
                    })
                print(f"_get_interfaces_rest: 获取到 {len(result)} 个接口")
                return result
            else:
                print(f"_get_interfaces_rest: HTTP {response.status_code}")
                return []
        except Exception as e:
            print(f"_get_interfaces_rest 失败: {e}")
            return []
    
    def _get_interfaces_legacy(self) -> List[Dict[str, Any]]:
        """使用 Legacy API 获取接口列表"""
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                command = ['/interface/print', 
                           '.proplist=name,type,mac-address,mtu,running,disabled,comment,tx-byte,rx-byte,last-link-down-time,last-link-up-time,link-downs,slave']
                print(f"_get_interfaces_legacy: 发送命令 {command}, 重试次数={retry_count}")
                self.write_sentence(command)

                interfaces = []
                current_entry = {}
                sentence_count = 0

                while True:
                    sentence = self.read_sentence(timeout=10)
                    sentence_count += 1
                    print(f"_get_interfaces_legacy: 收到sentence #{sentence_count}: {sentence}")
                    if not sentence:
                        print(f"_get_interfaces_legacy: sentence为空，退出循环")
                        break

                    if '!done' in sentence:
                        if current_entry and 'name' in current_entry:
                            interfaces.append(current_entry)
                        print(f"_get_interfaces_legacy: 收到!done，退出循环")
                        break

                    for line in sentence:
                        if line.startswith('!re'):
                            if current_entry and 'name' in current_entry:
                                interfaces.append(current_entry)
                            current_entry = {}
                            continue

                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                current_entry[key] = value

                print(f"_get_interfaces_legacy: 共收到 {sentence_count} 个sentence，解析出 {len(interfaces)} 个接口")

                result = []
                for iface in interfaces:
                    mac_address = iface.get('mac-address', '')
                    if mac_address:
                        mac_address = mac_address.upper()
                    
                    tx_byte = iface.get('tx-byte', '0')
                    rx_byte = iface.get('rx-byte', '0')
                    
                    result.append({
                        'name': iface.get('name', ''),
                        'type': iface.get('type', ''),
                        'mac_address': mac_address,
                        'mtu': iface.get('mtu', ''),
                        'running': iface.get('running', 'false') == 'true',
                        'disabled': iface.get('disabled', 'false') == 'true',
                        'comment': iface.get('comment', ''),
                        'tx_byte': int(tx_byte) if tx_byte.isdigit() else 0,
                        'rx_byte': int(rx_byte) if rx_byte.isdigit() else 0,
                        'last_link_down_time': iface.get('last-link-down-time', ''),
                        'last_link_up_time': iface.get('last-link-up-time', ''),
                        'link_downs': iface.get('link-downs', '0'),
                        'slave': iface.get('slave', 'false') == 'true'
                    })

                print(f"get_interfaces: 获取到 {len(result)} 个接口")
                return result
                
            except (ConnectionResetError, ConnectionAbortedError, OSError, socket.timeout, socket.error) as e:
                print(f"获取接口列表失败（网络错误）: {e}, 重试次数={retry_count}/{max_retries}")
                retry_count += 1
                if retry_count <= max_retries:
                    print("尝试重新连接...")
                    self.close()
                    time.sleep(1)
                    success, msg = self.login()
                    if not success:
                        print(f"重新登录失败: {msg}")
                        return []
                else:
                    print("已达最大重试次数，放弃获取接口列表")
                    return []
            except Exception as e:
                print(f"获取接口列表失败：{e}")
                traceback.print_exc()
                return []
    
    def _format_bytes(self, bytes_val: int) -> str:
        """格式化字节数为可读格式"""
        if bytes_val >= 1024 * 1024 * 1024:
            return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
        elif bytes_val >= 1024 * 1024:
            return f"{bytes_val / (1024 * 1024):.2f} MB"
        elif bytes_val >= 1024:
            return f"{bytes_val / 1024:.2f} KB"
        else:
            return f"{bytes_val} B"
    
    def _format_system_time(self, date: str, time: str) -> str:
        """格式化系统时间为 YYYY-MM-DD HH:MM:SS"""
        try:
            if not date:
                return time

            months = {'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
                      'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'}

            if '/' in date:
                parts = date.split('/')
                if len(parts) == 3:
                    month_name = parts[0].lower()
                    day = parts[1].zfill(2)
                    year = parts[2]
                    if month_name in months:
                        month = months[month_name]
                        return f"{year}-{month}-{day} {time}"
                    return f"{date} {time}"

            parts = date.lower().split('-')

            if len(parts) == 3:
                first = parts[0]
                if first in months:
                    month = months.get(first, '01')
                    day = parts[1].zfill(2)
                    year = parts[2]
                    if len(year) == 2:
                        year = '20' + year
                    return f"{year}-{month}-{day} {time}"
                elif first.isdigit() and len(first) == 4:
                    return f"{date} {time}"
        except:
            pass
        return f"{date} {time}"
    
    def get_cpu_usage(self) -> Dict[str, Any]:
        """获取CPU使用率"""
        if not self.logged_in:
            return {'cpu_usage': '0%'}
        
        try:
            if self.api_version == 'rest':
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                
                req = urllib.request.Request(f"https://{self.host}/rest/system/resource")
                req.add_header('Authorization', f'Basic {encoded_credentials}')
                
                with urllib.request.urlopen(req, timeout=5, context=context) as response:
                    data = json.loads(response.read().decode())
                    if isinstance(data, list) and len(data) > 0:
                        resource = data[0]
                    elif isinstance(data, dict):
                        resource = data
                    else:
                        return {'cpu_usage': '0%'}
                    
                    cpu_load = resource.get('cpu-load', '0')
                    return {'cpu_usage': f"{cpu_load}%" if cpu_load else '0%'}
            else:
                if not self.socket:
                    return {'cpu_usage': '0%'}
                
                self.write_sentence(['/system/resource/print'])
                all_lines = []
                while True:
                    sentence = self.read_sentence()
                    if not sentence:
                        break
                    all_lines.extend(sentence)
                    if '!done' in sentence:
                        break
                
                cpu_load = '0'
                for line in all_lines:
                    if line.startswith('=cpu-load='):
                        cpu_load = line[10:]
                        break
                
                return {'cpu_usage': f"{cpu_load}%" if cpu_load else '0%'}
        except Exception as e:
            print(f"获取CPU使用率失败：{e}")
            return {'cpu_usage': '0%'}
    
    def get_system_time(self) -> Dict[str, Any]:
        """获取系统时间，返回 date、time 和格式化的 system_time"""
        if not self.logged_in:
            return {'system_time': '', 'date': '', 'time': ''}

        for attempt in range(3):
            result = self._get_system_time_once()
            if result.get('system_time'):
                return result
            time.sleep(0.1)

        return {'system_time': '', 'date': '', 'time': ''}

    def _get_system_time_once(self) -> Dict[str, Any]:
        """内部方法：获取系统时间（单次尝试）"""
        system_time = ''
        date = ''
        time_val = ''

        if self.api_version == 'rest' and hasattr(self, '_rest_available') and self._rest_available:
            try:
                context = get_ssl_context()

                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()

                req = urllib.request.Request(f"https://{self.host}/rest/system/clock/print")
                req.add_header('Authorization', f'Basic {encoded_credentials}')

                with urllib.request.urlopen(req, timeout=5, context=context) as response:
                    data = json.loads(response.read().decode())
                    if isinstance(data, list) and len(data) > 0:
                        clock = data[0]
                    elif isinstance(data, dict):
                        clock = data
                    else:
                        raise Exception("Unexpected REST response type")

                    time_val = clock.get('time', '')
                    date = clock.get('date', '')
                    system_time = self._format_system_time(date, time_val)
                    return {'system_time': system_time, 'date': date, 'time': time_val}
            except Exception as e:
                print(f"[get_system_time] REST failed: {e}, falling back to Legacy API")

        if self.socket:
            try:
                self.write_sentence(['/system/clock/print'])
                all_lines = []
                while True:
                    sentence = self.read_sentence()
                    if not sentence:
                        break
                    all_lines.extend(sentence)
                    if '!done' in sentence:
                        break

                for line in all_lines:
                    if line.startswith('=date='):
                        date = line[6:]
                    elif line.startswith('=time='):
                        time_val = line[6:]

                if date and time_val:
                    system_time = self._format_system_time(date, time_val)
            except Exception as e:
                print(f"[get_system_time] Legacy API failed: {e}")

        return {'system_time': system_time, 'date': date, 'time': time_val}

    def talk(self, command: List[str]) -> List[Dict[str, str]]:
        """发送 API 命令并返回响应
        
        Args:
            command: API 命令列表
            
        Returns:
            响应字典列表
        """
        if not self.socket or not self.logged_in:
            raise Exception("Not connected or not logged in")
        
        # 发送命令
        self.write_sentence(command)
        
        # 读取响应
        response = []
        while True:
            sentence = self.read_sentence()
            if not sentence:
                break
            
            # 解析响应行
            response_dict = {}
            for word in sentence:
                if word.startswith('='):
                    key, value = word[1:].split('=', 1) if '=' in word[1:] else (word[1:], '')
                    response_dict[key] = value
                elif word.startswith('!'):
                    response_dict[word] = True
            
            response.append(response_dict)
            
            # 检查是否是最终响应
            if '!done' in response_dict:
                break
        
        return response

    def close(self):
        """关闭连接"""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        self.logged_in = False

    def is_alive(self) -> bool:
        """检测API连接是否存活，失败时会设置 logged_in=False"""
        if not self.logged_in:
            print(f"[is_alive] logged_in 已为 False，连接已断开")
            return False

        # 第一步: 快速TCP端口探测 - 检测设备是否在线
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(2)
            test_sock.connect((self.host, self.port))
            test_sock.close()
        except Exception as tcp_err:
            print(f"[is_alive] TCP端口探测失败({self.host}:{self.port}): {tcp_err}，设备已离线")
            self.logged_in = False
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            return False

        if self.api_version == 'rest':
            try:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()

                req = urllib.request.Request(f"https://{self.host}/rest/system/resource")
                req.add_header('Authorization', f'Basic {encoded_credentials}')

                with urllib.request.urlopen(req, timeout=8, context=context) as response:
                    if response.status == 200:
                        self.last_response_time = time.time()
                        return True
                    self.logged_in = False
                    return False
            except Exception as e:
                print(f"[is_alive] REST API 检测失败: {e}")
                self.logged_in = False
                return False
        else:
            if not self.socket:
                print(f"[is_alive] socket 已为 None，连接已断开")
                self.logged_in = False
                return False
            
            try:
                peer_name = self.socket.getpeername()
                print(f"[is_alive] Socket 连接状态检查: {peer_name}")
            except (socket.error, OSError) as sock_err:
                print(f"[is_alive] Socket 已断开: {sock_err}")
                self.logged_in = False
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
                return False
            
            try:
                print(f"[is_alive] 开始API检测...")
                self.socket.settimeout(3)
                self.write_sentence(['/system/resource/print'])
                response = self.read_sentence(timeout=3)
                self.socket.settimeout(30)
                self.last_response_time = time.time()
                print(f"[is_alive] API检测成功，连接正常")
                return True
                
            except socket.timeout:
                print(f"[is_alive] API操作超时(3秒)，设备可能已离线")
                try:
                    self.socket.settimeout(30)
                except:
                    pass
                self.logged_in = False
                return False
                
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as conn_err:
                print(f"[is_alive] 连接被重置/中断: {conn_err}")
                self.logged_in = False
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
                return False
                
            except (socket.error, OSError) as sock_err:
                print(f"[is_alive] Socket 错误: {sock_err}")
                self.logged_in = False
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
                return False
                
            except Exception as e:
                print(f"[is_alive] Legacy API 检测失败: {e}")
                import traceback
                traceback.print_exc()
                try:
                    self.socket.settimeout(30)
                except:
                    pass
                self.logged_in = False
                return False
