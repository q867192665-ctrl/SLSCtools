#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportUnreachable=false
"""
WebSocket 服务器，用于实时获取 MikroTik 设备日志和接口列表
"""

from __future__ import annotations

import asyncio
import os
import websockets
from websockets.protocol import State as WsState
import json
import threading
import time
import socket
import logging
import yaml
from typing import Any, TYPE_CHECKING
import sys
from mikrotik_api import MikroTikAPI
# librouteros 是可选依赖，仅在需要读取日志文件时使用
# 如果未安装，日志文件读取功能将不可用，但不影响 WebSocket 服务器
try:
    from librouteros import connect as librouteros_connect
    HAS_LIBROUTEROS = True
except ImportError:
    librouteros_connect = None  # type: ignore[reportAny]
    HAS_LIBROUTEROS = False

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection as WebSocketConn
else:
    WebSocketConn = object


def is_ws_closed(websocket: WebSocketConn) -> bool:
    """兼容 websockets 新旧版本的 WebSocket 关闭状态检测"""
    # websockets >= 13 使用 state 属性
    if hasattr(websocket, 'state'):
        return websocket.state == WsState.CLOSED  # type: ignore[reportAny, reportAttributeAccessIssue]
    # websockets < 13 使用 closed 属性
    if hasattr(websocket, 'closed'):
        return websocket.closed  # pyright: ignore[reportAttributeAccessIssue]
    return False

logger = logging.getLogger(__name__)

# ==================== 路径工具 ====================

def get_base_dir():
    """获取程序根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ==================== 配置加载 ====================

def load_config() -> dict[str, Any]:
    """加载配置文件"""
    config_path = os.path.join(get_base_dir(), 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            result: dict[str, Any] = yaml.safe_load(f) or {}  # type: ignore[reportAny]
            return result
    return {}

CONFIG: dict[str, Any] = load_config()

# ==================== 轮询间隔配置 ====================

POLLING_CONFIG: dict[str, Any] = CONFIG.get('polling', {})
INTERFACE_INTERVAL: int = int(POLLING_CONFIG.get('interface_interval', 2))
WIRELESS_INTERVAL: int = int(POLLING_CONFIG.get('wireless_interval', 2))
SECURITY_PROFILE_INTERVAL: int = int(POLLING_CONFIG.get('security_profile_interval', 3))
IP_ADDRESS_INTERVAL: int = int(POLLING_CONFIG.get('ip_address_interval', 3))
CLIENT_INTERVAL: int = int(POLLING_CONFIG.get('client_interval', 1))
DEVICE_CHECK_INTERVAL: int = int(POLLING_CONFIG.get('device_check_interval', 3))
MAX_CONSECUTIVE_ERRORS: int = int(POLLING_CONFIG.get('max_consecutive_errors', 5))


# 存储活跃的 WebSocket 连接
active_connections: dict[str, set[WebSocketConn]] = {}
connections_lock: threading.Lock = threading.Lock()

# 存储每个连接的过滤参数
connection_filters: dict[str, dict[str, str | None]] = {}
filters_lock: threading.Lock = threading.Lock()

# 存储每个设备的最后活动时间（用于检测离线）
device_last_activity: dict[str, float] = {}
activity_lock: threading.Lock = threading.Lock()

# 存储每个设备的 watch_device_status 任务
device_watch_tasks: dict[str, asyncio.Task[None]] = {}
tasks_lock: threading.Lock = threading.Lock()

# 存储每个设备的 API 连接
device_api_connections: dict[str, MikroTikAPI] = {}
api_conn_lock: threading.Lock = threading.Lock()

# 存储接口列表轮询任务
interface_polling_tasks: dict[str, asyncio.Task[None]] = {}
interface_polling_lock: threading.Lock = threading.Lock()

# 存储接口列表的独立API连接
interface_api_connections: dict[str, MikroTikAPI] = {}
interface_api_lock: threading.Lock = threading.Lock()

# 存储设备下载状态
device_download_status: dict[str, bool] = {}
download_status_lock: threading.Lock = threading.Lock()


def clear_device_download_status(identifier: str) -> None:
    """清除设备的下载状态标记"""
    with download_status_lock:
        if identifier in device_download_status:
            del device_download_status[identifier]
            print(f"[下载状态] 已清除 {identifier} 的下载标记")


log_cache_store: dict[str, dict] = {}
log_cache_store_lock: threading.Lock = threading.Lock()
log_api_connections: dict[str, MikroTikAPI] = {}
log_api_connections_lock: threading.Lock = threading.Lock()


def register_log_api(ip: str, api: MikroTikAPI) -> None:
    with log_api_connections_lock:
        if ip in log_api_connections:
            old_api = log_api_connections[ip]
            try:
                old_api.close()
            except:
                pass
        log_api_connections[ip] = api


def unregister_log_api(ip: str) -> None:
    with log_api_connections_lock:
        if ip in log_api_connections:
            del log_api_connections[ip]


def close_log_api_connection(ip: str) -> None:
    with log_api_connections_lock:
        if ip in log_api_connections:
            api = log_api_connections[ip]
            try:
                api.close()
                print(f"[日志] 已关闭 {ip} 的连接")
            except:
                pass
            del log_api_connections[ip]


def get_log_cache(ip: str) -> dict:
    with log_cache_store_lock:
        if ip not in log_cache_store:
            log_cache_store[ip] = {
                'logs': [],
                'last_time': None,
                'last_raw_time': None,
                'last_id': None,
                'seq': 0,
                'ftp_file': None,
                'ftp_done': False,
                'lock': threading.Lock(),
                'processed_logs': set(),
                'log_counter': 0,
            }
            print(f"[日志缓存] 创建新缓存: {ip}")
        return log_cache_store[ip]


def clear_log_cache(ip: str) -> None:
    close_log_api_connection(ip)
    with log_cache_store_lock:
        if ip in log_cache_store:
            cache = log_cache_store.pop(ip)
            ftp_file = cache.get('ftp_file')
            if ftp_file and os.path.exists(ftp_file):
                try:
                    os.remove(ftp_file)
                    print(f"[日志缓存] 已删除FTP文件: {ftp_file}")
                except Exception:
                    pass
            log_count = len(cache.get('logs', []))
            print(f"[日志缓存] 已清理 {ip} 的缓存 (共 {log_count} 条日志)")
        else:
            print(f"[日志缓存] 未找到 {ip} 的缓存 (当前缓存: {list(log_cache_store.keys())})")


class TrafficMonitorManager:
    """流量监控管理器，使用单个连接监控所有接口"""
    
    def __init__(self, device_ip: str, username: str, password: str) -> None:
        self.device_ip: str = device_ip
        self.username: str = username
        self.password: str = password
        self.traffic_data: dict[str, dict[str, int]] = {}
        self.traffic_data_lock: threading.Lock = threading.Lock()
        self.monitor_thread: threading.Thread | None = None
        self.monitor_api: MikroTikAPI | None = None
        self.running: bool = False
        self.websocket: WebSocketConn | None = None
        self.send_task: asyncio.Task[None] | None = None
        self.current_interfaces: list[str] = []
    
    def _start_monitor_sync(self):
        """同步方式启动流量监控（在线程中运行）"""
        try:
            self.monitor_api = MikroTikAPI(self.device_ip, self.username, self.password, port=8728, use_ssl=False)
            success, message = self.monitor_api.login()
            
            if not success:
                print(f"[流量监控] 连接失败: {message}")
                return
            
            while self.running and self.current_interfaces:
                try:
                    interface_list = ','.join(self.current_interfaces)
                    self.monitor_api.write_sentence(['/interface/monitor-traffic', f'=interface={interface_list}', '=duration=36000'])
                    print(f"[流量监控] 监控已启动: {interface_list} (duration=36000)")
                    
                    while self.running:
                        try:
                            if self.monitor_api.socket is not None:
                                self.monitor_api.socket.settimeout(5)
                            response = self.monitor_api.read_sentence(timeout=5)
                            
                            if '!done' in response or '!trap' in response:
                                print(f"[流量监控] 监控结束，重新启动")
                                break
                            
                            if '!re' in response:
                                iface_name = None
                                tx_bps = 0
                                rx_bps = 0
                                
                                for line in response:
                                    if line.startswith('=name='):
                                        iface_name = line.split('=')[2]
                                    elif line.startswith('=tx-bits-per-second='):
                                        try:
                                            tx_bps = int(line.split('=')[2])
                                        except:
                                            pass
                                    elif line.startswith('=rx-bits-per-second='):
                                        try:
                                            rx_bps = int(line.split('=')[2])
                                        except:
                                            pass
                                
                                if iface_name:
                                    with self.traffic_data_lock:
                                        self.traffic_data[iface_name] = {
                                            'tx_bps': tx_bps,
                                            'rx_bps': rx_bps
                                        }
                        except socket.timeout:
                            continue
                        except Exception as e:
                            if self.running:
                                print(f"[流量监控] 读取数据异常: {e}")
                            break
                    
                    if self.running:
                        time.sleep(1)
                        
                except Exception as e:
                    if self.running:
                        print(f"[流量监控] 监控异常: {e}")
                    break
        except Exception as e:
            print(f"[流量监控] 初始化异常: {e}")
        finally:
            print(f"[流量监控] 监控已停止")
            if self.monitor_api:
                try:
                    self.monitor_api.close()
                except:
                    pass
                self.monitor_api = None
    
    async def start_monitor(self):
        """启动流量监控"""
        if self.monitor_thread:
            return
        
        self.running = True
        thread = threading.Thread(
            target=self._start_monitor_sync,
            daemon=True
        )
        thread.start()
        self.monitor_thread = thread
    
    async def stop_monitor(self):
        """停止流量监控"""
        self.running = False
        
        if self.monitor_api:
            try:
                self.monitor_api.close()
            except:
                pass
        
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
            self.monitor_thread = None
        
        print(f"[流量监控] 监控已关闭")
    
    async def update_interfaces(self, interfaces: list[dict[str, Any]]) -> None:
        """更新监控的接口列表"""
        new_interfaces: list[str] = []
        
        for iface in interfaces:
            iface_name: str | None = iface.get('name')  # type: ignore[reportAny]
            iface_disabled: str | bool = iface.get('disabled', False)  # type: ignore[reportAny]
            if isinstance(iface_disabled, str):
                iface_disabled = iface_disabled.lower() == 'true'
            if iface_name and not iface_disabled:
                new_interfaces.append(iface_name)
        
        if set(new_interfaces) != set(self.current_interfaces):
            self.current_interfaces = new_interfaces
            print(f"[流量监控] 接口列表已更新: {new_interfaces}")
            
            with self.traffic_data_lock:
                for iface_name in list(self.traffic_data.keys()):
                    if iface_name not in new_interfaces:
                        del self.traffic_data[iface_name]
                        print(f"[流量监控] 已移除接口 {iface_name} 的流量数据")
    
    async def start_send_task(self, websocket: WebSocketConn) -> None:
        """启动定期发送流量数据的任务"""
        self.websocket = websocket
        self.running = True
        self.send_task = asyncio.create_task(self._send_traffic_data_loop())
        await self.start_monitor()
    
    async def _send_traffic_data_loop(self):
        """定期发送流量数据到前端"""
        while self.running:
            try:
                with self.traffic_data_lock:
                    traffic_copy = dict(self.traffic_data)
                
                if traffic_copy and self.websocket:
                    try:
                        await self.websocket.send(json.dumps({  # type: ignore[reportAny]
                            'type': 'interface_traffic',
                            'status': 'success',
                            'traffic': traffic_copy
                        }, ensure_ascii=False))
                    except websockets.exceptions.ConnectionClosed:
                        break
                
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[流量监控] 发送数据异常: {e}")
    
    async def stop_all(self):
        """停止所有监控"""
        self.running = False
        
        if self.send_task:
            _ = self.send_task.cancel()
            try:
                await self.send_task
            except asyncio.CancelledError:
                pass
        
        await self.stop_monitor()
        
        print(f"[流量监控] 设备 {self.device_ip} 所有监控已停止")


traffic_managers: dict[str, TrafficMonitorManager] = {}
traffic_managers_lock: threading.Lock = threading.Lock()


def update_device_activity(device_ip: str) -> None:
    """更新设备的最后活动时间"""
    with activity_lock:
        device_last_activity[device_ip] = time.time()


def get_device_activity(device_ip: str) -> float:
    """获取设备的最后活动时间"""
    with activity_lock:
        return device_last_activity.get(device_ip, 0)


def get_api_connection(device_ip: str, username: str, password: str) -> MikroTikAPI | None:
    """获取或创建 MikroTik API 连接"""
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()

        if success:
            print(f"连接已创建：{device_ip} - {message}")

            with api_conn_lock:
                if device_ip in device_api_connections:
                    old_api = device_api_connections[device_ip]
                    if old_api and old_api is not mt_api:
                        try:
                            old_api.close()
                            print(f"关闭设备 {device_ip} 的旧连接")
                        except:
                            pass
                device_api_connections[device_ip] = mt_api

            return mt_api
        else:
            print(f"创建连接失败：{device_ip} - {message}")
            return None
    except Exception as e:
        print(f"创建连接失败：{device_ip} - {e}")
        return None


def close_api_connection(device_ip: str) -> None:
    """关闭 API 连接"""
    print(f"连接已关闭：{device_ip}")


async def register_connection(websocket: WebSocketConn, device_ip: str) -> None:
    """注册 WebSocket 连接"""
    with connections_lock:
        if device_ip not in active_connections:
            active_connections[device_ip] = set()
        active_connections[device_ip].add(websocket)

    with filters_lock:
        if device_ip not in connection_filters:
            connection_filters[device_ip] = {'topics': None, 'level': None}

    print(f"[调试] WebSocket 连接已注册：{device_ip}, 当前连接数: {len(active_connections[device_ip])}")


async def unregister_connection(websocket: WebSocketConn, device_ip: str, _device_mac: str | None = None, _force_cleanup: bool = False) -> None:
    """注销 WebSocket 连接

    Args:
        websocket: WebSocket连接对象
        device_ip: 设备IP地址
        device_mac: 设备MAC地址
        force_cleanup: 是否强制清理资源
    """
    import traceback
    is_last_connection = False
    caller = ''.join(traceback.format_stack()[-3:-1])  # 调用者信息

    with connections_lock:
        if device_ip in active_connections:
            active_connections[device_ip].discard(websocket)
            if not active_connections[device_ip]:
                del active_connections[device_ip]
                is_last_connection = True
                logger.info(f"[注销] 设备{device_ip}所有连接已清理, 调用者:\n{caller}")
            else:
                logger.info(f"[注销] 设备{device_ip}剩余连接数: {len(active_connections[device_ip])}, 调用者:\n{caller}")

    # 只有最后一个连接断开时才清除 filters
    if is_last_connection:
        with filters_lock:
            if device_ip in connection_filters:
                del connection_filters[device_ip]

    logger.info(f"WebSocket 连接已注销：{device_ip}, is_last={is_last_connection}")

    # 只有最后一个连接断开时才清理日志缓存
    if is_last_connection:
        clear_log_cache(device_ip)
        logger.info(f"[日志缓存] 设备 {device_ip} 的日志缓存已清理")


device_offline_flags: dict[str, bool] = {}
offline_flags_lock: threading.Lock = threading.Lock()


def mark_device_offline(device_ip: str) -> None:
    """跨模块离线通知：HTTP处理器检测到设备离线时调用"""
    with offline_flags_lock:
        device_offline_flags[device_ip] = True
    print(f"[离线通知] 设备 {device_ip} 被标记为离线（由外部模块触发）")


def _tcp_probe(host: str, port: int, timeout: int = 2) -> bool:
    """轻量级 TCP 端口探测，仅检查设备端口是否可达"""
    try:
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.settimeout(timeout)
        test_sock.connect((host, port))
        test_sock.close()
        return True
    except Exception:
        return False


async def watch_device_status(mt_api: MikroTikAPI, device_ip: str, _device_mac: str | None, websocket: WebSocketConn) -> None:
    """监测设备在线状态，检测离线时通知前端触发断线重连
    
    使用轻量级 TCP 探测 + 连续失败容错机制：
    - 单次检测失败不判定离线，需连续 OFFLINE_THRESHOLD 次失败
    - 外部离线通知（device_offline_flags）立即触发
    - 不调用 is_alive() 避免干扰正在进行的 API 操作
    - finally 中不注销 WebSocket 连接，由主循环处理
    - 增加宽限期和失败阈值，避免频繁切换菜单时误判离线
    """
    CHECK_INTERVAL: int = DEVICE_CHECK_INTERVAL
    OFFLINE_THRESHOLD = 5  # 连续失败次数阈值（从3增加到5，提高容错性）
    consecutive_failures = 0
    GRACE_PERIOD = 60  # 连接建立后的宽限期（秒），在此期间不执行严格检查（从30增加到60）
    grace_period_remaining = GRACE_PERIOD

    logger.info(f"[watch_device_status] ===== 开始监控设备: {device_ip} =====")

    # 首次启动时等待较长时间，让socket连接完全稳定
    # 这在Win7等环境下特别重要，避免立即误判
    await asyncio.sleep(10)

    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            
            # 检查是否有子 WebSocket 连接活跃，如果有则延长宽限期
            with connections_lock:
                has_sub_connections = device_ip in active_connections and len(active_connections[device_ip]) > 1
            
            if has_sub_connections and grace_period_remaining < 30:
                grace_period_remaining = 30
                logger.info(f"[watch_device_status] 检测到子连接活跃，延长宽限期至30秒: {device_ip}")
            
            # 更新宽限期
            if grace_period_remaining > 0:
                grace_period_remaining -= CHECK_INTERVAL

            if is_ws_closed(websocket):
                logger.info(f"[watch_device_status] WebSocket 已关闭，退出监控: {device_ip}")
                break

            # 外部离线通知（最高优先级，立即触发）
            with offline_flags_lock:
                if device_ip in device_offline_flags:
                    del device_offline_flags[device_ip]
                    logger.info(f"[watch_device_status] 收到外部离线通知: {device_ip}")
                    mt_api.logged_in = False
                    await send_device_offline(websocket, device_ip, "外部模块检测到离线")
                    break

            # 检查 1: mt_api 对象有效性
            if not mt_api or not mt_api.logged_in:
                consecutive_failures += 1
                logger.warning(f"[watch_device_status] API对象无效或未登录 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {device_ip}")
                if consecutive_failures >= OFFLINE_THRESHOLD:
                    await send_device_offline(websocket, device_ip, "API连接无效")
                    break
                continue

            # 检查 2: API Socket 状态（仅检查 TCP 连接是否存在）
            # 在宽限期内，跳过此检查以避免误判
            if grace_period_remaining <= 0:
                if hasattr(mt_api, 'socket') and mt_api.socket:
                    try:
                        mt_api.socket.getpeername()
                    except (socket.error, OSError):
                        # Socket可能暂时不可用，但不一定表示设备离线
                        # 增加容错：先尝试重新建立连接，而不是直接判定离线
                        logger.warning(f"[watch_device_status] Socket状态异常，尝试重连 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {device_ip}")
                        try:
                            mt_api.socket.close()
                        except:
                            pass
                        mt_api.socket = None
                        mt_api.logged_in = False
                        
                        # 尝试重新登录
                        success, msg = mt_api.login()
                        if success:
                            logger.info(f"[watch_device_status] Socket重连成功: {device_ip}")
                            consecutive_failures = 0  # 重置失败计数
                            continue
                        else:
                            consecutive_failures += 1
                            logger.warning(f"[watch_device_status] Socket重连失败 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {msg}")
                            if consecutive_failures >= OFFLINE_THRESHOLD:
                                await send_device_offline(websocket, device_ip, f"Socket重连失败: {msg}")
                                break
                            continue
                else:
                    # REST API 没有 socket，跳过此检查
                    if mt_api.api_version != 'rest':
                        logger.warning(f"[watch_device_status] Socket为None，尝试重连 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {device_ip}")
                        # 尝试重新登录
                        success, msg = mt_api.login()
                        if success:
                            logger.info(f"[watch_device_status] 重连成功: {device_ip}")
                            consecutive_failures = 0
                            continue
                        else:
                            consecutive_failures += 1
                            if consecutive_failures >= OFFLINE_THRESHOLD:
                                await send_device_offline(websocket, device_ip, f"重连失败: {msg}")
                                break
                            continue
            else:
                logger.debug(f"[watch_device_status] 宽限期内，跳过Socket检查 (剩余{grace_period_remaining}秒): {device_ip}")

            # 检查 3: 轻量级 TCP 端口探测（不发送 API 命令，避免干扰）
            # 在宽限期内，跳过此检查以避免误判
            if grace_period_remaining <= 0:
                try:
                    # 根据API版本选择正确的探测端口
                    if mt_api.api_version == 'rest':
                        probe_port = 443
                    elif mt_api.api_version == 'legacy_ssl':
                        probe_port = 8729
                    else:
                        probe_port = mt_api.port if hasattr(mt_api, 'port') else 8728
                        
                    loop = asyncio.get_event_loop()
                    port_reachable = await asyncio.wait_for(
                        loop.run_in_executor(None, _tcp_probe, mt_api.host, probe_port),
                        timeout=3
                    )
                    if not port_reachable:
                        consecutive_failures += 1
                        logger.warning(f"[watch_device_status] TCP端口不可达 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {device_ip}:{probe_port}")
                        if consecutive_failures >= OFFLINE_THRESHOLD:
                            mt_api.logged_in = False
                            await send_device_offline(websocket, device_ip, "设备端口不可达")
                            break
                        continue
                except asyncio.TimeoutError:
                    consecutive_failures += 1
                    logger.warning(f"[watch_device_status] TCP探测超时 ({consecutive_failures}/{OFFLINE_THRESHOLD}): {device_ip}")
                    if consecutive_failures >= OFFLINE_THRESHOLD:
                        mt_api.logged_in = False
                        await send_device_offline(websocket, device_ip, "设备探测超时")
                        break
                    continue
            else:
                logger.debug(f"[watch_device_status] 宽限期内，跳过TCP探测 (剩余{grace_period_remaining}秒): {device_ip}")

            # 所有检查通过，设备在线
            consecutive_failures = 0

            # 发送心跳消息给前端
            try:
                await websocket.send(json.dumps({'action': 'ping'}))
            except Exception:
                logger.info(f"[watch_device_status] 发送心跳失败，WebSocket可能已断开: {device_ip}")
                break

        logger.info(f"[watch_device_status] ===== 监控结束: {device_ip} =====")

    except asyncio.CancelledError:
        logger.info(f"[watch_device_status] 任务被取消: {device_ip}")
    except Exception as e:
        logger.error(f"[watch_device_status] 异常退出: {device_ip} - {e}")
        import traceback
        traceback.print_exc()


async def send_device_offline(websocket: WebSocketConn, device_ip: str, reason: str) -> None:
    """发送设备离线消息"""
    # 清理日志缓存
    clear_log_cache(device_ip)
    logger.info(f"[设备离线] 已清理设备 {device_ip} 的日志缓存")
    
    try:
        message = {'status': 'device_offline', 'message': f'设备连接已断开: {reason}'}
        await websocket.send(json.dumps(message))
        print(f"[watch_device_status] >>> device_offline 消息已发送: {reason}")
    except websockets.exceptions.ConnectionClosed:
        print(f"[watch_device_status] WebSocket已关闭，无法发送device_offline")
    except Exception as send_err:
        print(f"[watch_device_status] 发送 device_offline 失败: {send_err}")


async def get_interface_list(mt_api: MikroTikAPI) -> list[dict[str, Any]] | None:
    """获取接口列表信息"""
    try:
        interfaces = mt_api.get_interfaces()
        return interfaces
    except Exception as e:
        print(f"获取接口列表失败: {e}")
        return None


async def interface_polling_task(device_ip: str, websocket: WebSocketConn, mt_api: MikroTikAPI) -> None:
    """接口列表轮询任务"""
    consecutive_errors = 0
    
    try:
        while True:
            try:
                interfaces = await get_interface_list(mt_api)
                if interfaces is not None:
                    consecutive_errors = 0
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'success',
                        'interfaces': interfaces
                    }, ensure_ascii=False))
                else:
                    consecutive_errors += 1
                    print(f"接口列表获取返回空, 连续错误次数: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'error',
                        'message': '获取接口列表失败'
                    }, ensure_ascii=False))
            except websockets.exceptions.ConnectionClosed:
                print(f"接口列表WebSocket连接已关闭: {device_ip}")
                break
            except Exception as e:
                consecutive_errors += 1
                print(f"接口列表轮询错误: {e}, 连续错误次数: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"连续错误达到 {MAX_CONSECUTIVE_ERRORS} 次，停止轮询")
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'error',
                        'message': f'连接异常，连续错误{MAX_CONSECUTIVE_ERRORS}次'
                    }, ensure_ascii=False))
                    break
                
                await websocket.send(json.dumps({
                    'type': 'interface_list',
                    'status': 'error',
                    'message': f'获取失败，正在重试({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})...'
                }, ensure_ascii=False))
            
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        print(f"接口列表轮询任务已取消: {device_ip}")
    except Exception as e:
        print(f"接口列表轮询任务异常: {e}")


async def handle_interface_polling(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """处理接口列表长连接"""
    mt_api = None
    polling_task = None
    traffic_manager = None
    
    try:
        # 注意：不复用 device_api_connections 中的共享连接，
        # 因为本函数在 finally 中会关闭 mt_api，会影响主连接的 watch_device_status 任务
        # 始终创建独立的 API 连接供本 WebSocket 使用
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'interface_list',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        # 不将独立连接存入 device_api_connections，避免与主连接冲突
        # with interface_api_lock:
        #     interface_api_connections[device_ip] = mt_api
        
        await websocket.send(json.dumps({
            'type': 'interface_list',
            'status': 'connected',
            'message': '接口列表连接已建立'
        }, ensure_ascii=False))
        
        traffic_manager = TrafficMonitorManager(device_ip, username, password)
        with traffic_managers_lock:
            traffic_managers[device_ip] = traffic_manager
        
        await traffic_manager.start_send_task(websocket)
        
        polling_task = asyncio.create_task(interface_polling_task_with_traffic(device_ip, websocket, mt_api, traffic_manager))
        
        with interface_polling_lock:
            interface_polling_tasks[device_ip] = polling_task
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get('action') == 'stop':
                        break
                except:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        
    except Exception as e:
        print(f"接口列表长连接错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'interface_list',
                'status': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if polling_task:
            _ = polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        
        if traffic_manager:
            await traffic_manager.stop_all()
            with traffic_managers_lock:
                if device_ip in traffic_managers:
                    del traffic_managers[device_ip]
        
        with interface_polling_lock:
            if device_ip in interface_polling_tasks:
                del interface_polling_tasks[device_ip]
        
        with interface_api_lock:
            if device_ip in interface_api_connections:
                del interface_api_connections[device_ip]
        
        if mt_api:
            try:
                mt_api.close()
                print(f"接口列表连接已关闭: {device_ip}")
            except:
                pass


async def interface_polling_task_with_traffic(device_ip: str, websocket: WebSocketConn, mt_api: MikroTikAPI, traffic_manager: TrafficMonitorManager) -> None:
    """接口列表轮询任务（带流量监控）"""
    consecutive_errors = 0
    
    try:
        while True:
            try:
                interfaces = await get_interface_list(mt_api)
                if interfaces is not None:
                    consecutive_errors = 0
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'success',
                        'interfaces': interfaces
                    }, ensure_ascii=False))
                    
                    await traffic_manager.update_interfaces(interfaces)
                else:
                    consecutive_errors += 1
                    print(f"接口列表获取返回空, 连续错误次数: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'error',
                        'message': '获取接口列表失败'
                    }, ensure_ascii=False))
            except websockets.exceptions.ConnectionClosed:
                print(f"接口列表WebSocket连接已关闭: {device_ip}")
                break
            except Exception as e:
                consecutive_errors += 1
                print(f"接口列表轮询错误: {e}, 连续错误次数: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}")
                
                if consecutive_errors >= 2:
                    print(f"[接口轮询] 开始检测设备连接状态...")
                    loop = asyncio.get_event_loop()
                    try:
                        is_alive = await asyncio.wait_for(
                            loop.run_in_executor(None, mt_api.is_alive),
                            timeout=5
                        )
                        if not is_alive:
                            print(f"[接口轮询] is_alive() 返回 False，设备离线，发送 device_offline 消息")
                            try:
                                await websocket.send(json.dumps({
                                    'type': 'interface_list',
                                    'status': 'device_offline',
                                    'message': '设备连接已断开'
                                }, ensure_ascii=False))
                                print(f"[接口轮询] device_offline 消息已发送")
                            except Exception as send_err:
                                print(f"[接口轮询] 发送 device_offline 消息失败: {send_err}")
                            break
                    except asyncio.TimeoutError:
                        print(f"[接口轮询] is_alive() 超时，设备离线，发送 device_offline 消息")
                        try:
                            await websocket.send(json.dumps({
                                'type': 'interface_list',
                                'status': 'device_offline',
                                'message': '设备连接已断开'
                            }, ensure_ascii=False))
                            print(f"[接口轮询] device_offline 消息已发送")
                        except Exception as send_err:
                            print(f"[接口轮询] 发送 device_offline 消息失败: {send_err}")
                        break
                    except Exception as ex:
                        print(f"[接口轮询] is_alive() 异常: {ex}，设备离线，发送 device_offline 消息")
                        try:
                            await websocket.send(json.dumps({
                                'type': 'interface_list',
                                'status': 'device_offline',
                                'message': '设备连接已断开'
                            }, ensure_ascii=False))
                            print(f"[接口轮询] device_offline 消息已发送")
                        except Exception as send_err:
                            print(f"[接口轮询] 发送 device_offline 消息失败: {send_err}")
                        break
                
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"连续错误达到 {MAX_CONSECUTIVE_ERRORS} 次，停止轮询")
                    await websocket.send(json.dumps({
                        'type': 'interface_list',
                        'status': 'error',
                        'message': f'连接异常，连续错误{MAX_CONSECUTIVE_ERRORS}次'
                    }, ensure_ascii=False))
                    break
                
                await websocket.send(json.dumps({
                    'type': 'interface_list',
                    'status': 'error',
                    'message': f'获取失败，正在重试({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})...'
                }, ensure_ascii=False))
            
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        print(f"接口列表轮询任务已取消: {device_ip}")
    except Exception as e:
        print(f"接口列表轮询任务异常: {e}")


async def handle_get_wireless_interfaces_list(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """获取无线接口列表"""
    mt_api = None
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        mt_api.write_sentence(['/interface/wireless/print'])
        
        wireless_interfaces = []
        while True:
            try:
                response = mt_api.read_sentence(timeout=10)
            except Exception:
                break
            
            if '!done' in response:
                break
            if '!trap' in response:
                break
            if '!re' in response:
                iface = {}
                for line in response:
                    if line.startswith('='):
                        parts = line[1:].split('=', 1)
                        if len(parts) == 2:
                            key, value = parts
                            iface[key] = value
                
                if iface and iface.get('name'):
                    wireless_interfaces.append({
                        'name': iface.get('name'),
                        'frequency': iface.get('frequency', '--'),
                        'band': iface.get('band', '--'),
                        'running': iface.get('running', 'false') == 'true',
                        'disabled': iface.get('disabled', 'false') == 'true'
                    })
        
        await websocket.send(json.dumps({
            'type': 'wireless_interfaces_list',
            'interfaces': wireless_interfaces
        }, ensure_ascii=False))
        
    except Exception as e:
        print(f"获取无线接口列表错误: {e}")
        await websocket.send(json.dumps({
            'type': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_start_interference_scan(websocket: WebSocketConn, device_ip: str, username: str, password: str, interface_name: str, background: bool = False) -> None:
    """处理干扰扫描长连接"""
    mt_api = None
    
    print(f"[干扰扫描] 设备: {device_ip}, 接口: '{interface_name}', 后台扫描: {background}")
    
    if not interface_name:
        await websocket.send(json.dumps({
            'type': 'error',
            'message': '接口名称不能为空'
        }, ensure_ascii=False))
        return
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        command = ['/interface/wireless/scan', f'=.id={interface_name}', '=duration=3600']
        if background:
            command.append('=background=yes')
        
        print(f"干扰扫描命令: {command}")
        mt_api.write_sentence(command)
        
        while True:
            try:
                response = mt_api.read_sentence(timeout=30)
            except Exception as e:
                print(f"扫描读取错误: {e}")
                break
            
            if '!done' in response:
                break
            if '!trap' in response:
                error_msg = ''
                for line in response:
                    if line.startswith('=message='):
                        error_msg = line[9:]
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': error_msg or '扫描失败'
                }, ensure_ascii=False))
                break
            if '!re' in response:
                item = {}
                for line in response:
                    if line.startswith('='):
                        parts = line[1:].split('=', 1)
                        if len(parts) == 2:
                            key, value = parts
                            item[key] = value
                
                if item:
                    result = {
                        'address': item.get('address', '--'),
                        'ssid': item.get('ssid', '--'),
                        'channel': item.get('channel', '--'),
                        'signal_strength': item.get('sig', '--'),
                        'noise': item.get('nf', '--'),
                        'snr': item.get('snr', '--')
                    }
                    
                    try:
                        await websocket.send(json.dumps({
                            'type': 'scan_result',
                            'result': result
                        }, ensure_ascii=False))
                    except websockets.exceptions.ConnectionClosed:
                        break
            
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                data = json.loads(message)
                if data.get('action') == 'stop_scan':
                    break
            except asyncio.TimeoutError:
                pass
            except websockets.exceptions.ConnectionClosed:
                break
        
    except Exception as e:
        print(f"干扰扫描错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                print(f"干扰扫描连接已关闭: {device_ip}")
            except:
                pass


async def handle_wireless_config_polling(websocket: WebSocketConn, device_ip: str, username: str, password: str, interface_name: str) -> None:
    """无线配置页面轮询模式，独立连接每秒读取所有无线信息"""
    import time
    mt_api: MikroTikAPI | None = None
    POLL_INTERVAL: int = 1
    READ_TIMEOUT: int = 10
    MAX_RETRIES: int = 3
    RETRY_BASE_DELAY: float = 1.0

    if not interface_name:
        await websocket.send(json.dumps({
            'type': 'wireless_config',
            'status': 'error',
            'message': '接口名称不能为空'
        }, ensure_ascii=False))
        return

    cached_nlevel: int | None = None

    def _read_config_sync(api: MikroTikAPI) -> dict[str, str] | None:
        config: dict[str, str] = {}
        api.write_sentence(['/interface/wireless/print', f'?name={interface_name}'])
        while True:
            response = api.read_sentence(timeout=READ_TIMEOUT)
            if '!done' in response:
                break
            if '!trap' in response:
                return None
            if '!re' in response:
                for line in response:
                    if line.startswith('='):
                        parts = line[1:].split('=', 1)
                        if len(parts) == 2:
                            key, value = parts
                            config[key] = value
        return config if config else None

    def _read_security_profiles_sync(api: MikroTikAPI) -> list[str]:
        profiles: list[str] = []
        api.write_sentence(['/interface/wireless/security-profiles/print'])
        while True:
            response = api.read_sentence(timeout=READ_TIMEOUT)
            if '!done' in response:
                break
            if '!trap' in response:
                break
            if '!re' in response:
                for line in response:
                    if line.startswith('=name='):
                        profiles.append(line[6:])
                        break
        return profiles

    def _read_license_nlevel_sync(api: MikroTikAPI) -> int | None:
        api.write_sentence(['/system/license/print'])
        while True:
            response = api.read_sentence(timeout=READ_TIMEOUT)
            if '!done' in response:
                break
            if '!trap' in response:
                break
            if '!re' in response:
                for line in response:
                    if line.startswith('=nlevel='):
                        try:
                            return int(line[8:])
                        except:
                            pass
                break
        return None

    async def _read_all(api: MikroTikAPI) -> tuple[dict[str, str] | None, list[str] | None]:
        """在一次轮询中读取所有配置信息"""
        loop = asyncio.get_event_loop()
        
        config = await loop.run_in_executor(None, lambda: _read_config_sync(api))
        if not config:
            return None, None
        
        security_profiles = await loop.run_in_executor(None, lambda: _read_security_profiles_sync(api))
        
        nonlocal cached_nlevel
        if cached_nlevel is None:
            cached_nlevel = await loop.run_in_executor(None, lambda: _read_license_nlevel_sync(api))
        
        band: str = str(config.get('band', ''))
        vht_mcs: str = str(config.get('vht-supported-mcs', ''))
        has_ac: bool = 'ac' in band.lower() or vht_mcs != ''
        
        await websocket.send(json.dumps({
            'type': 'wireless_config',
            'status': 'success',
            'config': config,
            'has_ac': has_ac,
            'security_profiles': security_profiles,
            'nlevel': cached_nlevel,
            'data_complete': True
        }, ensure_ascii=False))
        return config, security_profiles

    async def ensure_connected() -> str | None:
        nonlocal mt_api
        if mt_api is not None:
            try:
                mt_api.close()
            except:
                pass
            mt_api = None
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        if not success:
            return message
        return None

    consecutive_errors: int = 0

    try:
        conn_err = await ensure_connected()
        if conn_err:
            await websocket.send(json.dumps({
                'type': 'wireless_config',
                'status': 'error',
                'message': f'连接失败: {conn_err}'
            }, ensure_ascii=False))
            return
        
        assert mt_api is not None  # ensure_connected 成功后 mt_api 必定有效
        
        while True:
            try:
                loop_start = time.monotonic()

                config, _ = await _read_all(mt_api)

                if config is None:
                    consecutive_errors += 1
                    retry_delay = min(RETRY_BASE_DELAY * (2 ** (consecutive_errors - 1)), 30)
                    print(f"[无线配置] 读取失败 ({consecutive_errors}/{MAX_RETRIES})，{retry_delay}s 后重连...")

                    if consecutive_errors >= MAX_RETRIES:
                        conn_err = await ensure_connected()
                        if conn_err:
                            print(f"[无线配置] 重连失败: {conn_err}")
                            await websocket.send(json.dumps({
                                'type': 'wireless_config',
                                'status': 'error',
                                'message': f'重连失败: {conn_err}'
                            }, ensure_ascii=False))
                            break
                        print(f"[无线配置] 重连成功: {device_ip}")
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(retry_delay)
                    continue
                else:
                    consecutive_errors = 0

                elapsed = time.monotonic() - loop_start
                wait_time = max(0.2, POLL_INTERVAL - elapsed)

                try:
                    msg_data = await asyncio.wait_for(websocket.recv(), timeout=wait_time)
                    msg_json = json.loads(msg_data)
                    if msg_json.get('action') == 'close':
                        break
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    break

            except websockets.exceptions.ConnectionClosed:
                print(f"[无线配置] WebSocket连接已关闭: {device_ip}")
                break
            except Exception as e:
                print(f"[无线配置] 轮询错误: {e}")
                consecutive_errors += 1
                if consecutive_errors >= MAX_RETRIES:
                    conn_err = await ensure_connected()
                    if conn_err:
                        print(f"[无线配置] 重连失败: {conn_err}")
                        break
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(POLL_INTERVAL)

    except Exception as e:
        print(f"[无线配置] 连接错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                print(f"[无线配置] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_set_wireless_interface_config(websocket: WebSocketConn, device_ip: str, username: str, password: str, interface_name: str, config_changes: dict[str, Any]) -> None:
    """更新无线接口配置"""
    mt_api = None
    
    if not interface_name:
        await websocket.send(json.dumps({
            'type': 'wireless_config_update',
            'status': 'error',
            'message': '接口名称不能为空'
        }, ensure_ascii=False))
        return
    
    if not config_changes:
        await websocket.send(json.dumps({
            'type': 'wireless_config_update',
            'status': 'success',
            'message': '没有配置变更'
        }, ensure_ascii=False))
        return
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'wireless_config_update',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        command = ['/interface/wireless/set', f'=numbers={interface_name}']
        for key, value in config_changes.items():
            command.append(f'={key}={value}')
        
        print(f"[无线配置更新] 发送命令: {command}")
        mt_api.write_sentence(command)
        
        response = mt_api.read_sentence(timeout=10)
        print(f"[无线配置更新] 响应: {response}")
        
        if '!done' in response:
            await websocket.send(json.dumps({
                'type': 'wireless_config_update',
                'status': 'success',
                'message': '配置更新成功'
            }, ensure_ascii=False))
        elif '!trap' in response:
            error_msg = ''
            for line in response:
                if line.startswith('=message='):
                    error_msg = line[9:]
            await websocket.send(json.dumps({
                'type': 'wireless_config_update',
                'status': 'error',
                'message': error_msg or '配置更新失败'
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'wireless_config_update',
                'status': 'success',
                'message': '配置已发送'
            }, ensure_ascii=False))
        
    except Exception as e:
        print(f"[无线配置更新] 错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'wireless_config_update',
                'status': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                print(f"[无线配置更新] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_wireless_interfaces_polling(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """处理无线接口长连接"""
    import time
    mt_api: MikroTikAPI | None = None
    POLL_INTERVAL: int = WIRELESS_INTERVAL
    READ_TIMEOUT: int = 5
    MAX_RETRIES: int = 3
    RETRY_BASE_DELAY: float = 1.0

    async def get_wireless_interfaces(api: MikroTikAPI) -> tuple[list[dict[str, str | bool]] | None, str | None]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: _get_wireless_interfaces_sync(api))

    def _get_wireless_interfaces_sync(api: MikroTikAPI) -> tuple[list[dict[str, str | bool]] | None, str | None]:
        max_internal_retries = 2
        for attempt in range(max_internal_retries + 1):
            interfaces = []
            try:
                api.write_sentence(['/interface/wireless/print',
                                    '.proplist=name,running,disabled,mode,ssid,frequency,band,channel-width,wireless-protocol'])
                while True:
                    response = api.read_sentence(timeout=READ_TIMEOUT)
                    
                    if '!done' in response:
                        break
                    if '!trap' in response:
                        break
                    if '!re' in response:
                        iface = {}
                        for line in response:
                            if line.startswith('='):
                                parts = line[1:].split('=', 1)
                                if len(parts) == 2:
                                    key, value = parts
                                    iface[key] = value
                        
                        if iface:
                            interfaces.append({
                                'name': iface.get('name', '--'),
                                'running': iface.get('running', 'false') == 'true',
                                'disabled': iface.get('disabled', 'false') == 'true',
                                'mode': iface.get('mode', '--'),
                                'ssid': iface.get('ssid', '--'),
                                'frequency': iface.get('frequency', '--'),
                                'band': iface.get('band', '--'),
                                'channel_width': iface.get('channel-width', '--'),
                                'protocol': iface.get('wireless-protocol', '--')
                            })
                
                return interfaces, None
            except Exception as e:
                if attempt >= max_internal_retries:
                    return None, str(e)
        
        return None, "获取无线接口失败（重试耗尽）"

    async def ensure_connected() -> tuple[MikroTikAPI | None, str | None]:
        """确保 API 连接有效，失效则重新建立"""
        nonlocal mt_api
        if mt_api is not None:
            try:
                mt_api.close()
            except:
                pass
            mt_api = None
        
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        if not success:
            return None, message
        return mt_api, None

    consecutive_errors: int = 0
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'wireless_interfaces',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await websocket.send(json.dumps({
            'type': 'wireless_interfaces',
            'status': 'connected',
            'message': '无线接口连接已建立'
        }, ensure_ascii=False))
        
        while True:
            try:
                loop_start = time.monotonic()
                
                interfaces, error = await get_wireless_interfaces(mt_api)
                
                if error:
                    consecutive_errors += 1
                    retry_delay = min(RETRY_BASE_DELAY * (2 ** (consecutive_errors - 1)), 30)
                    print(f"无线接口读取错误 ({consecutive_errors}/{MAX_RETRIES}): {error}，{retry_delay}s 后重连...")
                    
                    if consecutive_errors >= MAX_RETRIES:
                        _, conn_err = await ensure_connected()
                        if conn_err:
                            print(f"无线接口重连失败: {conn_err}")
                            await websocket.send(json.dumps({
                                'type': 'wireless_interfaces',
                                'status': 'error',
                                'message': f'重连失败: {conn_err}'
                            }, ensure_ascii=False))
                            break
                        print(f"无线接口重连成功: {device_ip}")
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(retry_delay)
                    continue
                else:
                    consecutive_errors = 0
                
                if interfaces is not None:
                    await websocket.send(json.dumps({
                        'type': 'wireless_interfaces',
                        'status': 'success',
                        'interfaces': interfaces
                    }, ensure_ascii=False))
                
                elapsed = time.monotonic() - loop_start
                wait_time = max(0.2, POLL_INTERVAL - elapsed)
                
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=wait_time)
                    data = json.loads(message)
                    action = data.get('action')
                    
                    if action == 'stop':
                        break
                    elif action == 'set_wireless_interface_config':
                        interface_name = data.get('interface_name')
                        config_changes = data.get('config_changes', {})
                        print(f"[无线接口轮询] 收到配置更新请求: {interface_name}, 变更: {config_changes}")
                        
                        if mt_api and interface_name and config_changes:
                            try:
                                command = ['/interface/wireless/set', f'=numbers={interface_name}']
                                for key, value in config_changes.items():
                                    command.append(f'={key}={value}')
                                
                                print(f"[无线接口轮询] 发送命令: {command}")
                                mt_api.write_sentence(command)
                                response = mt_api.read_sentence(timeout=10)
                                print(f"[无线接口轮询] 响应: {response}")
                                
                                if '!done' in response:
                                    await websocket.send(json.dumps({
                                        'type': 'wireless_config_update',
                                        'status': 'success',
                                        'message': '配置更新成功'
                                    }, ensure_ascii=False))
                                elif '!trap' in response:
                                    error_msg = ''
                                    for line in response:
                                        if line.startswith('=message='):
                                            error_msg = line[9:]
                                    await websocket.send(json.dumps({
                                        'type': 'wireless_config_update',
                                        'status': 'error',
                                        'message': error_msg or '配置更新失败'
                                    }, ensure_ascii=False))
                            except Exception as e:
                                print(f"[无线接口轮询] 配置更新错误: {e}")
                                await websocket.send(json.dumps({
                                    'type': 'wireless_config_update',
                                    'status': 'error',
                                    'message': str(e)
                                }, ensure_ascii=False))
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                print(f"无线接口WebSocket连接已关闭: {device_ip}")
                break
            except Exception as e:
                print(f"无线接口轮询错误: {e}")
                try:
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': f'获取无线接口失败: {str(e)}'
                    }, ensure_ascii=False))
                except:
                    pass
                consecutive_errors += 1
                if consecutive_errors >= MAX_RETRIES:
                    _, conn_err = await ensure_connected()
                    if conn_err:
                        print(f"无线接口重连失败: {conn_err}")
                        break
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(POLL_INTERVAL)
        
    except Exception as e:
        print(f"无线接口长连接错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                print(f"无线接口连接已关闭: {device_ip}")
            except:
                pass


async def handle_wireless_clients_monitor(websocket: WebSocketConn, device_ip: str, username: str, password: str, _interface_name: str) -> None:
    """处理终端列表监控长连接"""
    mt_api: MikroTikAPI | None = None
    POLL_INTERVAL: int = CLIENT_INTERVAL
    READ_TIMEOUT: int = 10

    print(f"[终端监控] 开始监控: {device_ip}")

    def get_wireless_clients_sync(api: MikroTikAPI) -> tuple[list[dict[str, str]] | None, str | None]:
        clients = []
        try:
            api.write_sentence(['/interface/wireless/registration-table/print'])
            
            while True:
                try:
                    response = api.read_sentence(timeout=READ_TIMEOUT)
                except Exception as e:
                    return None, str(e)
                
                if '!done' in response:
                    break
                if '!trap' in response:
                    break
                if '!re' in response:
                    client = {}
                    for line in response:
                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                client[key] = value
                    
                    if client:
                        signal_strength = client.get('signal-strength', '')
                        tx_signal = ''
                        if signal_strength:
                            tx_signal = signal_strength.split('@')[0] if '@' in signal_strength else signal_strength
                        
                        clients.append({
                            'interface': client.get('interface', '--'),
                            'mac': client.get('mac-address', '--'),
                            'uptime': client.get('uptime', '--'),
                            'tx_signal': tx_signal,
                            'rx_signal': '',
                            'tx_signal_quality': client.get('tx-ccq', ''),
                            'rx_signal_quality': '',
                            'tx_rate': client.get('tx-rate', ''),
                            'rx_rate': client.get('rx-rate', '')
                        })
            
            return clients, None
        except Exception as e:
            return None, str(e)
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'wireless_clients',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await websocket.send(json.dumps({
            'type': 'wireless_clients',
            'status': 'connected',
            'message': '终端监控连接已建立'
        }, ensure_ascii=False))
        
        while True:
            try:
                loop = asyncio.get_event_loop()
                clients, error = await loop.run_in_executor(None, lambda: get_wireless_clients_sync(mt_api))
                
                if error:
                    print(f"[终端监控] 读取错误: {error}")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                
                if clients is not None:
                    await websocket.send(json.dumps({
                        'type': 'wireless_clients',
                        'status': 'success',
                        'clients': clients
                    }, ensure_ascii=False))
                
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=POLL_INTERVAL)
                    data = json.loads(message)
                    if data.get('action') == 'stop':
                        print(f"[终端监控] 收到停止命令")
                        break
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                print(f"[终端监控] WebSocket连接已关闭: {device_ip}")
                break
            except Exception as e:
                print(f"[终端监控] 轮询错误: {e}")
                await asyncio.sleep(POLL_INTERVAL)
        
    except Exception as e:
        print(f"终端监控错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'wireless_clients',
                'status': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                print(f"[终端监控] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_security_profiles_monitor(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """处理加密配置监控长连接"""
    mt_api: MikroTikAPI | None = None
    POLL_INTERVAL: int = SECURITY_PROFILE_INTERVAL
    READ_TIMEOUT: int = 10
    stop_requested: bool = False

    print(f"[加密配置] 开始监控: {device_ip}")

    def get_security_profiles_sync(api: MikroTikAPI) -> tuple[list[dict[str, str]] | None, str | None]:
        profiles = []
        try:
            api.write_sentence(['/interface/wireless/security-profiles/print'])
            
            while True:
                try:
                    response = api.read_sentence(timeout=READ_TIMEOUT)
                except Exception as e:
                    return None, str(e)
                
                if '!done' in response:
                    break
                if '!trap' in response:
                    break
                if '!re' in response:
                    profile = {}
                    for line in response:
                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                profile[key] = value
                    
                    if profile:
                        auth_types = profile.get('authentication-types', '')
                        if 'wpa-psk' in auth_types and 'wpa2-psk' in auth_types:
                            auth_display = 'WPA/WPA2-PSK'
                        elif 'wpa2-psk' in auth_types:
                            auth_display = 'WPA2-PSK'
                        elif 'wpa-psk' in auth_types:
                            auth_display = 'WPA-PSK'
                        else:
                            auth_display = auth_types.upper() if auth_types else '--'
                        
                        unicast = profile.get('unicast-ciphers', '')
                        group = profile.get('group-ciphers', '')
                        ciphers = set()
                        if unicast:
                            ciphers.update([c.strip() for c in unicast.split(',')])
                        if group:
                            ciphers.update([c.strip() for c in group.split(',')])
                        
                        if 'aes-ccm' in ciphers and 'tkip' in ciphers:
                            cipher_display = 'AES/TKIP'
                        elif 'aes-ccm' in ciphers:
                            cipher_display = 'AES'
                        elif 'tkip' in ciphers:
                            cipher_display = 'TKIP'
                        else:
                            cipher_display = '--'
                        
                        wpa_key = profile.get('wpa-pre-shared-key', '')
                        wpa2_key = profile.get('wpa2-pre-shared-key', '')
                        
                        if wpa_key and wpa2_key and wpa_key == wpa2_key:
                            password_display = wpa_key
                        elif wpa2_key:
                            password_display = wpa2_key
                        elif wpa_key:
                            password_display = wpa_key
                        else:
                            password_display = '--'
                        
                        profiles.append({
                            'name': profile.get('name', '--'),
                            'authentication': auth_display,
                            'cipher': cipher_display,
                            'password': password_display
                        })
            
            return profiles, None
        except Exception as e:
            return None, str(e)
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'security_profiles',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await websocket.send(json.dumps({
            'type': 'security_profiles',
            'status': 'connected',
            'message': '加密配置监控连接已建立'
        }, ensure_ascii=False))
        
        while not stop_requested:
            try:
                if not mt_api or not mt_api.logged_in:
                    if stop_requested:
                        break
                    print(f"[加密配置] 连接断开，尝试重连...")
                    if mt_api:
                        try:
                            mt_api.close()
                        except:
                            pass
                    mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    if not success:
                        print(f"[加密配置] 重连失败: {message}")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    print(f"[加密配置] 重连成功")
                
                loop = asyncio.get_event_loop()
                assert mt_api is not None
                _api = mt_api
                profiles, error = await loop.run_in_executor(None, lambda: get_security_profiles_sync(_api))
                
                if stop_requested:
                    break
                
                if error:
                    if '10054' in str(error) or '远程主机强迫关闭' in str(error):
                        if stop_requested:
                            break
                        print(f"[加密配置] 连接被重置，将在下次轮询时重连")
                        if mt_api:
                            try:
                                mt_api.close()
                            except:
                                pass
                            mt_api = None
                    else:
                        print(f"[加密配置] 读取错误: {error}")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                
                if profiles is not None:
                    await websocket.send(json.dumps({
                        'type': 'security_profiles',
                        'status': 'success',
                        'profiles': profiles
                    }, ensure_ascii=False))
                
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=POLL_INTERVAL)
                    data = json.loads(message)
                    if data.get('action') == 'stop':
                        print(f"[加密配置] 收到停止命令")
                        stop_requested = True
                        break
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    stop_requested = True
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                print(f"[加密配置] WebSocket连接已关闭: {device_ip}")
                stop_requested = True
                break
            except Exception as e:
                if stop_requested:
                    break
                print(f"[加密配置] 轮询错误: {e}")
                await asyncio.sleep(POLL_INTERVAL)
        
    except Exception as e:
        if not stop_requested:
            print(f"加密配置监控错误: {e}")
            try:
                await websocket.send(json.dumps({
                    'type': 'security_profiles',
                    'status': 'error',
                    'message': str(e)
                }, ensure_ascii=False))
            except:
                pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                if not stop_requested:
                    print(f"[加密配置] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_ip_addresses_monitor(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """处理IP地址监控长连接"""
    mt_api: MikroTikAPI | None = None
    POLL_INTERVAL: int = 3
    READ_TIMEOUT: int = 10
    stop_requested: bool = False

    print(f"[IP地址] 开始监控: {device_ip}")

    def get_ip_addresses_sync(api: MikroTikAPI) -> tuple[list[dict[str, str]] | None, str | None]:
        addresses = []
        try:
            api.write_sentence(['/ip/address/print'])
            
            while True:
                try:
                    response = api.read_sentence(timeout=READ_TIMEOUT)
                except Exception as e:
                    return None, str(e)
                
                if '!done' in response:
                    break
                if '!trap' in response:
                    break
                if '!re' in response:
                    addr = {}
                    for line in response:
                        if line.startswith('='):
                            parts = line[1:].split('=', 1)
                            if len(parts) == 2:
                                key, value = parts
                                addr[key] = value
                    
                    if addr:
                        addresses.append({
                            '.id': addr.get('.id', ''),
                            'address': addr.get('address', '--'),
                            'network': addr.get('network', '--'),
                            'interface': addr.get('interface', '--'),
                            'name': addr.get('name', ''),
                            'disabled': addr.get('disabled', 'false'),
                            'dynamic': addr.get('dynamic', 'false')
                        })
            
            return addresses, None
        except Exception as e:
            return None, str(e)
    
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_addresses',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await websocket.send(json.dumps({
            'type': 'ip_addresses',
            'status': 'connected',
            'message': 'IP地址监控连接已建立'
        }, ensure_ascii=False))
        
        while not stop_requested:
            try:
                if not mt_api or not mt_api.logged_in:
                    if stop_requested:
                        break
                    print(f"[IP地址] 连接断开，尝试重连...")
                    if mt_api:
                        try:
                            mt_api.close()
                        except:
                            pass
                    mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    if not success:
                        print(f"[IP地址] 重连失败: {message}")
                        await asyncio.sleep(POLL_INTERVAL)
                        continue
                    print(f"[IP地址] 重连成功")
                
                loop = asyncio.get_event_loop()
                assert mt_api is not None
                _api = mt_api
                addresses, error = await loop.run_in_executor(None, lambda: get_ip_addresses_sync(_api))
                
                if stop_requested:
                    break
                
                if error:
                    if '10054' in str(error) or '远程主机强迫关闭' in str(error):
                        if stop_requested:
                            break
                        print(f"[IP地址] 连接被重置，将在下次轮询时重连")
                        if mt_api:
                            try:
                                mt_api.close()
                            except:
                                pass
                            mt_api = None
                    else:
                        print(f"[IP地址] 读取错误: {error}")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                
                if addresses is not None:
                    await websocket.send(json.dumps({
                        'type': 'ip_addresses',
                        'status': 'success',
                        'addresses': addresses
                    }, ensure_ascii=False))
                
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=POLL_INTERVAL)
                    data = json.loads(message)
                    if data.get('action') == 'stop':
                        print(f"[IP地址] 收到停止命令")
                        stop_requested = True
                        break
                    elif data.get('action') == 'add_ip_address':
                        await handle_add_ip_address_sync(mt_api, data, websocket)
                    elif data.get('action') == 'edit_ip_address':
                        await handle_edit_ip_address_sync(mt_api, data, websocket)
                    elif data.get('action') == 'delete_ip_address':
                        await handle_delete_ip_address_sync(mt_api, data, websocket)
                    elif data.get('action') == 'enable_ip_address':
                        await handle_enable_ip_address_sync(mt_api, data, websocket)
                    elif data.get('action') == 'disable_ip_address':
                        await handle_disable_ip_address_sync(mt_api, data, websocket)
                except asyncio.TimeoutError:
                    pass
                except websockets.exceptions.ConnectionClosed:
                    stop_requested = True
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                print(f"[IP地址] WebSocket连接已关闭: {device_ip}")
                stop_requested = True
                break
            except Exception as e:
                if stop_requested:
                    break
                print(f"[IP地址] 轮询错误: {e}")
                await asyncio.sleep(POLL_INTERVAL)
        
    except Exception as e:
        if not stop_requested:
            print(f"IP地址监控错误: {e}")
            try:
                await websocket.send(json.dumps({
                    'type': 'ip_addresses',
                    'status': 'error',
                    'message': str(e)
                }, ensure_ascii=False))
            except:
                pass
    finally:
        if mt_api:
            try:
                mt_api.close()
                if not stop_requested:
                    print(f"[IP地址] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_add_ip_address_sync(api: MikroTikAPI, data: dict[str, Any], websocket: WebSocketConn) -> None:
    """同步添加IP地址"""
    try:
        address = data.get('address', '')
        iface = data.get('interface', '')
        network = data.get('network', '')
        name = data.get('name', '')
        disabled = data.get('disabled', False)
        
        command = ['/ip/address/add', f'=address={address}', f'=interface={iface}']
        if network:
            command.append(f'=network={network}')
        if name:
            command.append(f'=comment={name}')
        if disabled:
            command.append('=disabled=yes')
        
        api.write_sentence(command)
        response = api.read_sentence(timeout=10)
        
        if '!trap' in response:
            error_msg = ''.join([line for line in response if line.startswith('=message=')])
            error_msg = error_msg.replace('=message=', '') if error_msg else '添加失败'
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': error_msg
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'success',
                'message': '添加成功'
            }, ensure_ascii=False))
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))


async def handle_edit_ip_address_sync(api: MikroTikAPI, data: dict[str, Any], websocket: WebSocketConn) -> None:
    """同步编辑IP地址"""
    try:
        id_val = data.get('id', '')
        address = data.get('address', '')
        iface = data.get('interface', '')
        network = data.get('network', '')
        name = data.get('name', '')
        disabled = data.get('disabled', False)
        
        command = ['/ip/address/set', f'=.id={id_val}']
        if address:
            command.append(f'=address={address}')
        if iface:
            command.append(f'=interface={iface}')
        if network:
            command.append(f'=network={network}')
        if name:
            command.append(f'=comment={name}')
        command.append(f'=disabled={"yes" if disabled else "no"}')
        
        api.write_sentence(command)
        response = api.read_sentence(timeout=10)
        
        if '!trap' in response:
            error_msg = ''.join([line for line in response if line.startswith('=message=')])
            error_msg = error_msg.replace('=message=', '') if error_msg else '修改失败'
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': error_msg
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'success',
                'message': '修改成功'
            }, ensure_ascii=False))
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))


async def handle_delete_ip_address_sync(api: MikroTikAPI, data: dict[str, Any], websocket: WebSocketConn) -> None:
    """同步删除IP地址"""
    try:
        id_val = data.get('id', '')
        
        command = ['/ip/address/remove', f'=.id={id_val}']
        api.write_sentence(command)
        response = api.read_sentence(timeout=10)
        
        if '!trap' in response:
            error_msg = ''.join([line for line in response if line.startswith('=message=')])
            error_msg = error_msg.replace('=message=', '') if error_msg else '删除失败'
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': error_msg
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'success',
                'message': '删除成功'
            }, ensure_ascii=False))
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))


async def handle_enable_ip_address_sync(api: MikroTikAPI, data: dict[str, Any], websocket: WebSocketConn) -> None:
    """同步启用IP地址"""
    try:
        id_val = data.get('id', '')
        
        command = ['/ip/address/set', f'=.id={id_val}', '=disabled=no']
        api.write_sentence(command)
        response = api.read_sentence(timeout=10)
        
        if '!trap' in response:
            error_msg = ''.join([line for line in response if line.startswith('=message=')])
            error_msg = error_msg.replace('=message=', '') if error_msg else '启用失败'
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': error_msg
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'success',
                'message': '启用成功'
            }, ensure_ascii=False))
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))


async def handle_disable_ip_address_sync(api: MikroTikAPI, data: dict[str, Any], websocket: WebSocketConn) -> None:
    """同步禁用IP地址"""
    try:
        id_val = data.get('id', '')
        
        command = ['/ip/address/set', f'=.id={id_val}', '=disabled=yes']
        api.write_sentence(command)
        response = api.read_sentence(timeout=10)
        
        if '!trap' in response:
            error_msg = ''.join([line for line in response if line.startswith('=message=')])
            error_msg = error_msg.replace('=message=', '') if error_msg else '禁用失败'
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': error_msg
            }, ensure_ascii=False))
        else:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'success',
                'message': '禁用成功'
            }, ensure_ascii=False))
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))


async def handle_get_interfaces_list(websocket: WebSocketConn, device_ip: str, username: str, password: str) -> None:
    """获取接口列表"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'interfaces_list',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        interfaces = []
        mt_api.write_sentence(['/interface/print'])
        
        while True:
            try:
                response = mt_api.read_sentence(timeout=10)
            except:
                break
            
            if '!done' in response:
                break
            if '!trap' in response:
                break
            if '!re' in response:
                iface = {}
                for line in response:
                    if line.startswith('='):
                        parts = line[1:].split('=', 1)
                        if len(parts) == 2:
                            key, value = parts
                            iface[key] = value
                
                if iface:
                    interfaces.append({
                        'name': iface.get('name', '--'),
                        'type': iface.get('type', '--'),
                        'disabled': iface.get('disabled', 'false')
                    })
        
        await websocket.send(json.dumps({
            'type': 'interfaces_list',
            'status': 'success',
            'interfaces': interfaces
        }, ensure_ascii=False))
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'interfaces_list',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_add_ip_address(websocket: WebSocketConn, device_ip: str, username: str, password: str, data: dict[str, Any]) -> None:
    """添加IP地址"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await handle_add_ip_address_sync(mt_api, data, websocket)
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_edit_ip_address(websocket: WebSocketConn, device_ip: str, username: str, password: str, data: dict[str, Any]) -> None:
    """编辑IP地址"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await handle_edit_ip_address_sync(mt_api, data, websocket)
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_delete_ip_address(websocket: WebSocketConn, device_ip: str, username: str, password: str, data: dict[str, Any]) -> None:
    """删除IP地址"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await handle_delete_ip_address_sync(mt_api, data, websocket)
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_enable_ip_address(websocket: WebSocketConn, device_ip: str, username: str, password: str, data: dict[str, Any]) -> None:
    """启用IP地址"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await handle_enable_ip_address_sync(mt_api, data, websocket)
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_disable_ip_address(websocket: WebSocketConn, device_ip: str, username: str, password: str, data: dict[str, Any]) -> None:
    """禁用IP地址"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'type': 'ip_address_action',
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        await handle_disable_ip_address_sync(mt_api, data, websocket)
        
    except Exception as e:
        await websocket.send(json.dumps({
            'type': 'ip_address_action',
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass

async def handle_set_device_name(websocket: WebSocketConn, device_ip: str, username: str, password: str, new_name: str) -> None:
    """设置设备名称"""
    mt_api = None
    try:
        mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
        success, message = mt_api.login()
        
        if not success:
            await websocket.send(json.dumps({
                'status': 'error',
                'message': f'连接失败: {message}'
            }, ensure_ascii=False))
            return
        
        result = mt_api.talk(['/system/identity/set', f'=name={new_name}'])
        
        if result and len(result) > 0 and result[0].get('!trap'):
            error_msg = result[0].get('message', '未知错误')
            print(f"[设备名称] 设置失败: {error_msg}")
            await websocket.send(json.dumps({
                'status': 'error',
                'message': f'设置失败: {error_msg}'
            }, ensure_ascii=False))
        else:
            print(f"[设备名称] 设置成功: {new_name}")
            await websocket.send(json.dumps({
                'status': 'success',
                'message': '设备名称修改成功'
            }, ensure_ascii=False))
        
    except Exception as e:
        print(f"[设备名称] 异常: {e}")
        await websocket.send(json.dumps({
            'status': 'error',
            'message': str(e)
        }, ensure_ascii=False))
    finally:
        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def handle_logs_monitor(websocket: WebSocketConn, device_ip: str, username: str, password: str, device_mac: str = None) -> None:
    """处理日志监控 WebSocket 长连接
    
    使用 MikroTik follow=yes 模式实现真正的实时日志推送：
    1. 先通过 API 读取历史日志
    2. 使用 follow=yes 模式监听新日志，设备有新日志时主动推送
    3. 没有新日志时不产生任何网络请求
    4. 缓存仅在登出或断开连接时清除
    """
    loop = asyncio.get_event_loop()
    stop_event = threading.Event()
    ws_monitor_task = None
    cache = get_log_cache(device_ip)
    mt_api = None
    follow_thread = None

    async def _monitor_ws_connection():
        try:
            async for _ in websocket:
                pass
        except Exception:
            pass
        finally:
            stop_event.set()
            if mt_api:
                try:
                    mt_api.close()
                except:
                    pass
            print(f"[日志监控] WebSocket断开: {device_ip}")

    def follow_logs_callback(log_entry):
        """follow模式回调函数，当有新日志时调用"""
        try:
            if stop_event.is_set():
                return
            
            # 检查是否是新日志（通过ID判断）
            log_id = log_entry.get('id', '') or log_entry.get('.id', '')
            with cache['lock']:
                last_cached_id = cache.get('last_id', '')
                # 如果ID为空或与缓存中的ID相同，说明是旧日志，忽略
                if not log_id or log_id == last_cached_id:
                    return
                
                # 检查是否已经在缓存中（避免follow模式启动时推送的旧日志）
                cached_ids = cache.get('log_ids', set())
                if log_id in cached_ids:
                    return
                
                # 是新日志，添加到缓存并推送
                counter = cache.get('log_counter', 0)
                log_entry['seq'] = counter
                cache['log_counter'] = counter + 1
                cache['logs'].append(log_entry)
                cache['last_id'] = log_id
                cache['last_time'] = log_entry.get('time', '')
                cache['last_raw_time'] = log_entry.get('raw_time', '')
                
                # 记录ID到集合中
                if 'log_ids' not in cache:
                    cache['log_ids'] = set()
                cache['log_ids'].add(log_id)
                
                # 限制缓存大小
                if len(cache['logs']) > 10000:
                    cache['logs'] = cache['logs'][-5000:]
                    # 同时清理ID集合
                    if len(cache['log_ids']) > 10000:
                        cache['log_ids'] = set(list(cache['log_ids'])[-5000:])
                
                # 异步推送到前端
                asyncio.run_coroutine_threadsafe(
                    websocket.send(json.dumps({
                        'type': 'logs',
                        'status': 'incremental',
                        'logs': [log_entry],
                        'count': 1
                    }, ensure_ascii=False)),
                    loop
                )
        except Exception as e:
            print(f"[日志监控] follow回调错误: {e}")

    def start_follow_mode():
        """启动follow模式监听新日志"""
        try:
            nonlocal mt_api
            if not mt_api:
                print(f"[日志监控] 建立API连接到 {device_ip}...")
                mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
                success, message = mt_api.login()
                if not success:
                    print(f"[日志监控] API登录失败: {message}")
                    return
            
            print(f"[日志监控] 启动follow模式监听新日志: {device_ip}")
            # 使用follow=yes模式，设备有新日志时会主动推送
            mt_api.follow_logs(callback=follow_logs_callback, stop_event=stop_event, timeout=5)
            
        except Exception as e:
            print(f"[日志监控] follow模式启动失败: {e}")
            import traceback
            traceback.print_exc()

    def read_log_file_via_api():
        """通过 API 读取历史日志"""
        try:
            nonlocal mt_api
            if not mt_api:
                print(f"[日志监控] 建立API连接到 {device_ip}...")
                mt_api = MikroTikAPI(device_ip, username, password, port=8728, use_ssl=False)
                success, message = mt_api.login()
                if not success:
                    print(f"[日志监控] API登录失败: {message}")
                    return [], 'error'
                print(f"[日志监控] API连接成功: {device_ip}")
            
            # 使用 get_logs 获取历史日志
            logs = mt_api.get_logs(limit=2000)
            print(f"[日志监控] 获取到 {len(logs)} 条历史日志")
            return logs, 'api'
            
        except Exception as e:
            print(f"[日志监控] 读取日志失败: {e}")
            import traceback
            traceback.print_exc()
            return [], 'error'
            
    try:
        ws_monitor_task = asyncio.create_task(_monitor_ws_connection())

        await websocket.send(json.dumps({
            'type': 'logs',
            'status': 'connected',
            'message': '日志连接已建立'
        }, ensure_ascii=False))

        # 1. 先检查是否有缓存
        use_cache = False
        with cache['lock']:
            use_cache = cache.get('ftp_done', False) and bool(cache['logs'])
            print(f"[日志监控] 缓存状态: logs_count={len(cache.get('logs', []))}, use_cache={use_cache}")

        # 2. 如果有缓存，直接推送缓存日志
        if use_cache:
            with cache['lock']:
                cached_logs = list(cache['logs'])
                cached_seq = cache.get('log_counter', 0)
                # 确保缓存中有log_ids集合
                if 'log_ids' not in cache:
                    cache['log_ids'] = set()
                    for log in cached_logs:
                        log_id = log.get('id', '') or log.get('.id', '')
                        if log_id:
                            cache['log_ids'].add(log_id)

            print(f"[日志监控] 使用缓存: {len(cached_logs)} 条日志, seq={cached_seq}")
            await websocket.send(json.dumps({
                'type': 'logs',
                'status': 'cache_info',
                'total': len(cached_logs),
                'last_seq': cached_seq
            }, ensure_ascii=False))

            batch_size = 1000
            for i in range(0, len(cached_logs), batch_size):
                if stop_event.is_set():
                    break
                batch = cached_logs[i:i + batch_size]
                await websocket.send(json.dumps({
                    'type': 'logs',
                    'status': 'batch',
                    'logs': batch,
                    'offset': i,
                    'total': len(cached_logs)
                }, ensure_ascii=False))
                if i + batch_size < len(cached_logs):
                    await asyncio.sleep(0.5)

            if not stop_event.is_set():
                await websocket.send(json.dumps({
                    'type': 'logs',
                    'status': 'ftp_done',
                    'total': len(cached_logs)
                }, ensure_ascii=False))
        else:
            # 3. 没有缓存，获取历史日志
            print(f"[日志监控] 开始读取历史日志: {device_ip}")
            await websocket.send(json.dumps({
                'type': 'logs',
                'status': 'downloading',
                'message': '正在读取日志...'
            }, ensure_ascii=False))

            def fetch_logs():
                return read_log_file_via_api()

            try:
                all_logs, source = await asyncio.wait_for(
                    loop.run_in_executor(None, fetch_logs),
                    timeout=120
                )

                if stop_event.is_set():
                    return

                if not all_logs:
                    await websocket.send(json.dumps({
                        'type': 'logs',
                        'status': 'error',
                        'message': '无法获取日志'
                    }, ensure_ascii=False))
                    return

                # 添加序号并记录ID
                result_logs = []
                log_ids = set()
                for i, log in enumerate(all_logs):
                    entry = log.copy()
                    entry['seq'] = i
                    result_logs.append(entry)
                    log_id = entry.get('id', '') or entry.get('.id', '')
                    if log_id:
                        log_ids.add(log_id)

                counter = len(result_logs)

                # 更新缓存
                with cache['lock']:
                    cache['log_counter'] = counter
                    cache['logs'] = result_logs
                    cache['log_ids'] = log_ids
                    if result_logs:
                        cache['last_time'] = result_logs[-1].get('time', '')
                        cache['last_raw_time'] = result_logs[-1].get('raw_time', '')
                        cache['last_id'] = result_logs[-1].get('id', '') or result_logs[-1].get('.id', '')
                    cache['ftp_done'] = True

                print(f"[日志监控] 获取到 {len(result_logs)} 条日志，开始分批推送")

                # 分批推送
                batch_size = 1000
                total_batches = (len(result_logs) + batch_size - 1) // batch_size
                
                for i in range(0, len(result_logs), batch_size):
                    if stop_event.is_set():
                        print(f"[日志监控] 推送被中断，已推送 {i} 条")
                        break
                    batch = result_logs[i:i + batch_size]
                    batch_num = i // batch_size + 1
                    try:
                        await websocket.send(json.dumps({
                            'type': 'logs',
                            'status': 'batch',
                            'logs': batch,
                            'offset': i,
                            'total': len(result_logs)
                        }, ensure_ascii=False))
                        print(f"[日志监控] 批次 {batch_num}/{total_batches} 已发送")
                    except Exception as e:
                        print(f"[日志监控] 批次 {batch_num} 发送失败: {e}")
                        break
                    if i + batch_size < len(result_logs):
                        await asyncio.sleep(0.5)

                if not stop_event.is_set():
                    await websocket.send(json.dumps({
                        'type': 'logs',
                        'status': 'ftp_done',
                        'total': len(result_logs)
                    }, ensure_ascii=False))
                    print(f"[日志监控] 所有批次推送完成")

            except asyncio.TimeoutError:
                await websocket.send(json.dumps({
                    'type': 'logs',
                    'status': 'error',
                    'message': '获取日志超时'
                }, ensure_ascii=False))
                return

        # 4. 启动 follow 模式监听新日志（真正的实时推送，有新日志时才推送）
        print(f"[日志监控] 启动follow模式监听新日志: {device_ip}")
        follow_thread = threading.Thread(target=start_follow_mode, daemon=True)
        follow_thread.start()

        # 5. 等待 WebSocket 断开或停止事件
        try:
            while not stop_event.is_set():
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    except websockets.exceptions.ConnectionClosed:
        pass
    except asyncio.TimeoutError:
        try:
            await websocket.send(json.dumps({
                'type': 'logs',
                'status': 'error',
                'message': '操作超时'
            }, ensure_ascii=False))
        except:
            pass
    except Exception as e:
        print(f"日志监控错误: {e}")
        try:
            await websocket.send(json.dumps({
                'type': 'logs',
                'status': 'error',
                'message': str(e)
            }, ensure_ascii=False))
        except:
            pass
    finally:
        stop_event.set()
        if ws_monitor_task:
            ws_monitor_task.cancel()
            try:
                await ws_monitor_task
            except asyncio.CancelledError:
                pass
        if mt_api:
            try:
                mt_api.close()
                print(f"[日志监控] 连接已关闭: {device_ip}")
            except:
                pass


async def handle_websocket(websocket: WebSocketConn) -> None:
    """处理 WebSocket 连接"""
    device_ip = None
    device_mac = None
    mt_api = None

    try:
        message = await websocket.recv()
        data = json.loads(message)

        device_ip = data.get('ip')
        device_mac = data.get('mac')
        username = data.get('username')
        password = data.get('password')
        is_interface_polling = data.get('is_interface_polling', False)
        is_wireless_interfaces = data.get('is_wireless_interfaces', False)
        is_wireless_clients = data.get('is_wireless_clients', False)
        is_security_profiles = data.get('is_security_profiles', False)
        is_ip_addresses = data.get('is_ip_addresses', False)
        is_logs = data.get('is_logs', False)

        if not device_ip:
            await websocket.send(json.dumps({'error': '缺少设备 IP 地址'}))
            return

        if is_interface_polling:
            await handle_interface_polling(websocket, device_ip, username, password)
            return

        if is_wireless_interfaces:
            await handle_wireless_interfaces_polling(websocket, device_ip, username, password)
            return
        
        if is_wireless_clients:
            interface_name = data.get('interface_name')
            await handle_wireless_clients_monitor(websocket, device_ip, username, password, interface_name)
            return
        
        if is_security_profiles:
            await handle_security_profiles_monitor(websocket, device_ip, username, password)
            return
        
        if is_ip_addresses:
            await handle_ip_addresses_monitor(websocket, device_ip, username, password)
            return
        
        if is_logs:
            await handle_logs_monitor(websocket, device_ip, username, password, device_mac)
            return
        
        action = data.get('action')
        if action == 'get_wireless_interfaces_list':
            await handle_get_wireless_interfaces_list(websocket, device_ip, username, password)
            return
        if action == 'start_interference_scan':
            interface_name = data.get('interface_name')
            background = data.get('background', False)
            print(f"[干扰扫描请求] 接口名称: '{interface_name}', 后台扫描: {background}")
            await handle_start_interference_scan(websocket, device_ip, username, password, interface_name, background)
            return
        if action == 'get_wireless_interface_config':
            interface_name = data.get('interface_name')
            print(f"[无线配置请求] 接口名称: '{interface_name}'")
            await handle_wireless_config_polling(websocket, device_ip, username, password, interface_name)
            return
        if action == 'set_wireless_interface_config':
            interface_name = data.get('interface_name')
            config_changes = data.get('config_changes', {})
            print(f"[无线配置更新请求] 接口名称: '{interface_name}', 变更: {config_changes}")
            await handle_set_wireless_interface_config(websocket, device_ip, username, password, interface_name, config_changes)
            return
        if action == 'get_interfaces_list':
            await handle_get_interfaces_list(websocket, device_ip, username, password)
            return
        if action == 'add_ip_address':
            await handle_add_ip_address(websocket, device_ip, username, password, data)
            return
        if action == 'edit_ip_address':
            await handle_edit_ip_address(websocket, device_ip, username, password, data)
            return
        if action == 'delete_ip_address':
            await handle_delete_ip_address(websocket, device_ip, username, password, data)
            return
        if action == 'enable_ip_address':
            await handle_enable_ip_address(websocket, device_ip, username, password, data)
            return
        if action == 'disable_ip_address':
            await handle_disable_ip_address(websocket, device_ip, username, password, data)
            return
        if action == 'set_device_name':
            new_name = data.get('name', '')
            print(f"[设备名称] 修改请求: IP={device_ip}, 新名称='{new_name}'")
            await handle_set_device_name(websocket, device_ip, username, password, new_name)
            return

        with tasks_lock:
            if device_ip in device_watch_tasks:
                old_task = device_watch_tasks[device_ip]
                if not old_task.done():
                    print(f"取消设备 {device_ip} 的旧 watch_device_status 任务")
                    _ = old_task.cancel()
                    try:
                        await old_task
                    except asyncio.CancelledError:
                        pass
                del device_watch_tasks[device_ip]

        with api_conn_lock:
            if device_ip in device_api_connections:
                old_api = device_api_connections[device_ip]
                if old_api:
                    try:
                        old_api.close()
                        print(f"关闭设备 {device_ip} 的旧连接")
                    except:
                        pass
                del device_api_connections[device_ip]

        # 注意：不清除 active_connections，因为可能还有其他子连接（如接口轮询、无线接口轮询等）正在使用
        # 只在 unregister_connection 时，当最后一个连接断开时才清理
        # with connections_lock:
        #     if device_ip in active_connections:
        #         active_connections[device_ip].clear()

        await register_connection(websocket, device_ip)

        mt_api = get_api_connection(device_ip, username, password)

        if not mt_api:
            await websocket.send(json.dumps({'error': '连接设备失败'}))
            await unregister_connection(websocket, device_ip, device_mac)
            return

        await websocket.send(json.dumps({'status': 'connected', 'message': '已连接'}))

        watch_task = asyncio.create_task(watch_device_status(mt_api, device_ip, device_mac, websocket))

        with tasks_lock:
            device_watch_tasks[device_ip] = watch_task

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    action = data.get('action')

                    if action == 'pong':
                        pass

                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"处理客户端消息错误：{e}")
        finally:
            # 主 WebSocket 循环退出，记录退出原因
            ws_closed = is_ws_closed(websocket)
            ws_code = getattr(websocket, 'close_code', None)
            ws_reason = getattr(websocket, 'close_reason', None)
            logger.warning(f"[主WS] 循环退出: device={device_ip}, closed={ws_closed}, code={ws_code}, reason={ws_reason}")

            # 取消 watch 任务
            with tasks_lock:
                if device_ip in device_watch_tasks and device_watch_tasks[device_ip] is watch_task:
                    if not watch_task.done():
                        _ = watch_task.cancel()
                        try:
                            await watch_task
                        except asyncio.CancelledError:
                            pass
                    del device_watch_tasks[device_ip]
                    logger.info(f"清除设备 {device_ip} 的 watch_device_status 任务")

    except websockets.exceptions.ConnectionClosed as e:
        logger.warning(f"[主WS] 连接关闭: device={device_ip}, code={e.code}, reason={e.reason}")
    except Exception as e:
        logger.error(f"WebSocket 处理错误: device={device_ip}, error={e}")
    finally:
        if device_ip:
            await unregister_connection(websocket, device_ip, device_mac)

        if mt_api:
            try:
                mt_api.close()
            except:
                pass


async def start_websocket_server(port: int = 32996) -> None:
    """启动 WebSocket 服务器（支持 TLS）"""
    tls_config = CONFIG.get('tls', {})
    ssl_context = None
    
    if tls_config.get('enabled') and tls_config.get('cert_file') and tls_config.get('key_file'):
        from ssl_context import get_server_ssl_context
        ssl_context = get_server_ssl_context(tls_config['cert_file'], tls_config['key_file'])
        logger.info(f"WebSocket TLS 已启用 (cert={tls_config['cert_file']})")
    
    protocol = 'wss' if ssl_context else 'ws'
    logger.info(f"WebSocket 服务器启动在 {protocol}://0.0.0.0:{port}")
    
    try:
        async with websockets.serve(
            handle_websocket, '0.0.0.0', port, ssl=ssl_context,
            ping_interval=20, ping_timeout=10, close_timeout=5
        ):
            await asyncio.Future()
    except AttributeError:
        async with websockets.serve(
            handle_websocket, '0.0.0.0', port, ssl=ssl_context,
            ping_interval=20, ping_timeout=10, close_timeout=5
        ):
            await asyncio.Future()


def run_websocket_server(port: int = 32996) -> None:
    """在新线程中运行 WebSocket 服务器"""
    asyncio.run(start_websocket_server(port))


if __name__ == '__main__':
    run_websocket_server()
