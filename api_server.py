#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChinanTool FastAPI 服务器
替代原 mndp_server.py 中的 http.server
"""

import os
import glob
import socket
import struct
import threading
import json
import time
import platform
import sys
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

import psutil
import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from mikrotik_api import MikroTikAPI
from api_connection import api_connection, execute_with_api
from ssl_context import get_ssl_context

logger = logging.getLogger(__name__)

# ==================== 路径工具 ====================

def get_base_dir():
    """获取程序根目录（兼容 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ==================== 配置加载 ====================

def load_config() -> dict:
    """加载配置文件"""
    config_path = os.path.join(get_base_dir(), 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

# ==================== 常量 ====================

MNDP_TYPES = {
    0x01: "MAC-Address",
    0x05: "Identity",
    0x07: "Version",
    0x08: "Platform",
    0x0a: "Uptime",
    0x10: "Interface name",
    0x11: "IPv4-Address"
}

VIRTUAL_ADAPTER_KEYWORDS = [
    'virtual', 'vmware', 'virtualbox', 'vbox', 'hyper-v', 'loopback',
    'bluetooth', 'tunnel', 'teredo', 'isatap', '6to4', 'pseudo',
    'docker', 'veth', 'bridge', 'vnic', 'wan miniport', 'ras',
    'cisco anyconnect', 'fortinet', 'checkpoint', 'pulse secure',
    'vpn', 'tap', 'tun', 'wintun', 'wireguard'
]

# ==================== 全局状态 ====================

discovered_devices: Dict[str, dict] = {}
devices_lock = threading.Lock()
api_pool: Dict[str, MikroTikAPI] = {}
api_pool_lock = threading.Lock()

DEVICE_EXPIRE_SECONDS = CONFIG.get('mndp', {}).get('device_expire_seconds', 10)

# ==================== Pydantic 模型 ====================

class ConnectRequest(BaseModel):
    """设备连接请求"""
    ip: str
    username: str
    password: str = ""

class CheckArpRequest(BaseModel):
    """ARP 检查请求"""
    ip: str

class SecurityProfileAddRequest(BaseModel):
    """加密配置添加请求"""
    ip: str
    username: str
    password: str = ""
    name: str = ""
    authTypes: str = ""
    unicastCiphers: str = ""
    groupCiphers: str = ""
    wpaKey: str = ""
    wpa2Key: str = ""

class SecurityProfileDeleteRequest(BaseModel):
    """加密配置删除请求"""
    ip: str
    username: str
    password: str = ""
    name: str = ""

class SecurityProfileSetModeRequest(BaseModel):
    """加密配置模式设置请求"""
    ip: str
    username: str
    password: str = ""
    name: str = ""
    mode: str = "dynamic-keys"

class SecurityProfileEditRequest(BaseModel):
    """加密配置编辑请求"""
    ip: str
    username: str
    password: str = ""
    originalName: str = ""
    name: str = ""
    authTypes: str = ""
    unicastCiphers: str = ""
    groupCiphers: str = ""
    wpaKey: str = ""
    wpa2Key: str = ""

# ==================== 工具函数 ====================

def get_network_interfaces() -> List[dict]:
    """获取网络接口列表"""
    interfaces = []
    try:
        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()
        
        for iface_name, addrs in net_if_addrs.items():
            try:
                iface_lower = iface_name.lower()
                is_virtual = any(kw in iface_lower for kw in VIRTUAL_ADAPTER_KEYWORDS)
                
                ip_list = []
                mac = None
                
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        if ip and not ip.startswith('127.'):
                            ip_list.append(ip)
                    elif addr.family == psutil.AF_LINK:
                        mac = addr.address if addr.address else None
                
                is_up = False
                if iface_name in net_if_stats:
                    is_up = net_if_stats[iface_name].isup
                
                interfaces.append({
                    'name': iface_name,
                    'friendly_name': iface_name,
                    'ips': ip_list,
                    'mac': mac,
                    'is_virtual': is_virtual,
                    'is_up': is_up
                })
            except Exception:
                continue
    except Exception as e:
        logger.error(f"获取网卡列表失败: {e}")
    
    return interfaces


def format_bytes(bytes_val) -> str:
    """格式化字节数"""
    try:
        b = int(bytes_val)
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.1f} GB"
    except (ValueError, TypeError):
        return '--'


def _check_connection_error(error_str: str, ip: str):
    """检查连接错误并通知 WebSocket 模块"""
    error_lower = str(error_str).lower()
    if any(kw in error_lower for kw in ['10054', 'reset', 'refused', 'timed out', '关闭']):
        logger.warning(f"[HTTP离线检测] 检测到设备 {ip} 连接异常")
        try:
            from websocket_server import mark_device_offline
            mark_device_offline(ip)
        except Exception as notify_err:
            logger.error(f"[HTTP离线检测] 通知失败: {notify_err}")


# ==================== MNDP Core ====================

class MNDPCore:
    """MNDP 设备发现核心"""
    
    def __init__(self):
        self.devices: List[dict] = []
        self.is_running = False
        self.sock = None
        self.listener_thread = None
        self._auto_discover_running = False
        self._auto_discover_thread = None
    
    def _create_udp_socket(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            mndp_port = CONFIG.get('mndp', {}).get('port', 5678)
            sock.bind(("0.0.0.0", mndp_port))
            
            try:
                mreq = struct.pack("4sl", socket.inet_aton("239.255.255.255"), socket.INADDR_ANY)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except Exception as e:
                logger.debug(f"加入组播组失败: {e}")
            
            return sock
        except Exception as e:
            logger.error(f"创建MNDP套接字失败: {e}")
            return None
    
    def send_discovery_packet(self, interface_name=None) -> bool:
        discovery_packet = b"\x00\x00\x00\x00\x00\x01\x00\x00"
        target_addresses = ["255.255.255.255", "239.255.255.255"]
        discovery_count = CONFIG.get('mndp', {}).get('discovery_count', 2)
        
        try:
            interfaces = get_network_interfaces()
            sent_count = 0
            
            for iface in interfaces:
                if not iface['ips']:
                    continue
                
                local_ip = iface['ips'][0]
                
                try:
                    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    temp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    temp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    temp_sock.bind((local_ip, 0))
                    
                    for addr in target_addresses:
                        for i in range(discovery_count):
                            try:
                                temp_sock.sendto(discovery_packet, (addr, 5678))
                            except Exception as e:
                                logger.debug(f"发送失败 ({iface['friendly_name']} -> {addr}): {e}")
                    
                    temp_sock.close()
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"网卡 {iface['friendly_name']} 发送失败: {e}")
                    continue
            
            return sent_count > 0
        except Exception as e:
            logger.error(f"发送MNDP发现包失败: {e}")
            return False
    
    def _parse_mndp_packet(self, data):
        dev = {
            "MAC-Address": "",
            "Identity": "",
            "Platform": "",
            "Version": "",
            "Uptime": "",
            "Interface name": "",
            "IPv4-Address": "",
            "discovered_at": datetime.now().isoformat()
        }
        
        try:
            if len(data) < 4:
                return None
            
            offset = 4
            while offset + 4 <= len(data):
                field_type, field_len = struct.unpack("!HH", data[offset:offset+4])
                offset += 4
                
                if offset + field_len > len(data):
                    break
                
                field_value = data[offset:offset+field_len]
                offset += field_len
                
                field_name = MNDP_TYPES.get(field_type)
                if field_name == "MAC-Address" and len(field_value) == 6:
                    dev[field_name] = ":".join(f"{b:02X}" for b in field_value)
                elif field_name == "IPv4-Address" and len(field_value) == 4:
                    dev[field_name] = socket.inet_ntoa(field_value)
                elif field_name == "Uptime" and len(field_value) == 4:
                    reversed_val = field_value[::-1]
                    uptime_seconds = struct.unpack('!I', reversed_val)[0]
                    days = uptime_seconds // (24 * 3600)
                    remaining = uptime_seconds % (24 * 3600)
                    hours = remaining // 3600
                    remaining %= 3600
                    minutes = remaining // 60
                    seconds = remaining % 60
                    dev[field_name] = f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"
                elif field_name in ["Identity", "Platform", "Interface name", "Version"]:
                    for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                        try:
                            dev[field_name] = field_value.decode(encoding).strip()
                            break
                        except Exception:
                            continue
                    else:
                        dev[field_name] = field_value.decode('utf-8', 'replace').strip()
            
            return dev if dev["MAC-Address"] else None
        except Exception as e:
            logger.error(f"解析MNDP数据包失败: {e}")
            return None
    
    def _listener(self):
        while self.is_running:
            try:
                self.sock.settimeout(1)
                data, addr = self.sock.recvfrom(8192)
                device_info = self._parse_mndp_packet(data)
                
                if device_info and device_info["MAC-Address"]:
                    with devices_lock:
                        device_key = device_info["MAC-Address"]
                        device_info["last_seen"] = time.time()
                        discovered_devices[device_key] = device_info
                        
                        if not any(d["MAC-Address"] == device_info["MAC-Address"] for d in self.devices):
                            self.devices.append(device_info)
                            logger.info(f"发现新设备: {device_info.get('Identity', 'Unknown')} ({device_info.get('IPv4-Address', 'N/A')})")
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    logger.error(f"监听MNDP数据包出错: {e}")
                    continue
    
    def start_listener(self) -> bool:
        if not self.is_running:
            self.sock = self._create_udp_socket()
            if self.sock:
                self.is_running = True
                self.listener_thread = threading.Thread(target=self._listener, daemon=True)
                self.listener_thread.start()
                logger.info("MNDP监听已启动（端口5678）")
                return True
        return False
    
    def stop_listener(self):
        self.is_running = False
        if self.sock:
            self.sock.close()
        logger.info("MNDP监听已停止")
    
    def get_devices(self) -> List[dict]:
        with devices_lock:
            return list(discovered_devices.values())
    
    def cleanup_expired_devices(self):
        """删除所有last_seen超过过期时间的设备（仅刷新时调用）"""
        with devices_lock:
            current_time = time.time()
            expired_keys = [k for k, v in discovered_devices.items()
                          if current_time - v.get('last_seen', 0) > DEVICE_EXPIRE_SECONDS]
            for k in expired_keys:
                del discovered_devices[k]
                self.devices = [d for d in self.devices if d.get('MAC-Address') != k]
    
    def clear_devices(self):
        with devices_lock:
            discovered_devices.clear()
            self.devices.clear()
    
    def start_auto_discover(self, interval: float = 10.0):
        """启动自动发现定时器，每interval秒发送一次MNDP发现包，不删除设备"""
        def _auto_discover():
            while self._auto_discover_running:
                try:
                    self.send_discovery_packet()
                except Exception as e:
                    logger.error(f"自动发现发送失败: {e}")
                time.sleep(interval)
        
        self._auto_discover_running = True
        self._auto_discover_thread = threading.Thread(target=_auto_discover, daemon=True)
        self._auto_discover_thread.start()
        logger.info(f"自动发现已启动，间隔 {interval} 秒")
    
    def stop_auto_discover(self):
        """停止自动发现定时器"""
        self._auto_discover_running = False


# ==================== 全局 MNDP 实例 ====================

mndp_core = MNDPCore()

# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    if mndp_core.start_listener():
        mndp_core.send_discovery_packet()
        mndp_core.start_auto_discover(interval=10)
        logger.info("MNDP 设备发现已启动")
    
    # 启动 WebSocket 服务器线程
    try:
        from websocket_server import run_websocket_server
        ws_port = CONFIG.get('server', {}).get('ws_port', 32996)
        ws_thread = threading.Thread(target=run_websocket_server, args=(ws_port,), daemon=True)
        ws_thread.start()
        logger.info(f"WebSocket 服务器已启动在 ws://0.0.0.0:{ws_port}")
    except Exception as e:
        logger.error(f"WebSocket 服务器启动失败: {e}")
    
    yield
    
    # 关闭时
    mndp_core.stop_auto_discover()
    mndp_core.stop_listener()
    logger.info("服务已关闭")


app = FastAPI(
    title="ChinanTool API",
    description="MikroTik 网络设备管理工具 API",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 配置
cors_origins = CONFIG.get('server', {}).get('cors_origins', ["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 禁用静态文件缓存，确保前端代码更新后立即生效
@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# 静态文件
static_dir = CONFIG.get('static', {}).get('directory', 'static')
static_path = os.path.join(get_base_dir(), static_dir)
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


# ==================== API 路由 ====================

@app.get("/")
async def serve_index():
    """提供前端首页"""
    # 优先使用根目录的 index.html，其次使用 static 目录的
    root_index = os.path.join(get_base_dir(), 'index.html')
    if os.path.exists(root_index):
        return FileResponse(root_index)
    index_file = CONFIG.get('static', {}).get('index_file', 'index.html')
    index_path = os.path.join(static_path, index_file)
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Frontend not found")


@app.get("/api/devices")
async def get_devices():
    """获取已发现的设备列表"""
    devices_list = mndp_core.get_devices()
    return devices_list


@app.post("/api/refresh")
async def refresh_devices():
    """刷新设备：清空列表 → 发送发现包 → 等待回应 → 返回在线设备列表"""
    import asyncio
    # 1. 清空已有设备列表
    mndp_core.clear_devices()
    # 2. 发送MNDP发现包
    mndp_core.send_discovery_packet()
    # 3. 等待3秒让在线设备回应
    await asyncio.sleep(3)
    # 4. 返回当前在线设备列表
    devices_list = mndp_core.get_devices()
    return devices_list


@app.post("/api/discover")
async def discover_devices():
    """发送 MNDP 发现包"""
    success = mndp_core.send_discovery_packet()
    if success:
        return {"status": "success", "message": "MNDP发现包已发送"}
    return {"status": "error", "message": "发送MNDP发现包失败"}


@app.post("/api/connect")
async def connect_device(request: ConnectRequest):
    """连接设备（使用 POST body 传递凭证）"""
    if not request.ip:
        raise HTTPException(status_code=400, detail="请输入设备IP地址")
    if not request.username:
        raise HTTPException(status_code=400, detail="请输入用户名")
    
    try:
        logger.info(f"尝试登录设备: {request.ip} (用户: {request.username})")
        
        mt_api = MikroTikAPI(request.ip, request.username, request.password)
        success, message = mt_api.login()
        
        if success:
            system_info = mt_api.get_system_info()
            routeros_version = system_info.get('version', 'Unknown') if system_info else 'Unknown'
            board_name = system_info.get('board-name', 'Unknown') if system_info else 'Unknown'
            identity = mt_api.get_identity() or request.ip
            
            with api_pool_lock:
                api_pool[request.ip] = mt_api
            
            logger.info(f"登录成功: {request.ip} (RouterOS {routeros_version})")
            
            return {
                "status": "success",
                "message": message,
                "ip": request.ip,
                "username": request.username,
                "api_version": mt_api.api_version,
                "routeros_version": routeros_version,
                "board_name": board_name,
                "identity": identity
            }
        else:
            if mt_api:
                mt_api.close()
            logger.warning(f"登录失败: {request.ip} - {message}")
            return {"status": "error", "message": message, "ip": request.ip, "username": request.username}
            
    except Exception as e:
        logger.error(f"连接错误: {request.ip} - {e}")
        return {"status": "error", "message": f"连接错误: {e}", "ip": request.ip, "username": request.username}


@app.post("/api/logout")
async def logout_device(request: Request):
    """登出设备（使用 POST body 传递凭证）"""
    # 同时支持 POST body 和 Query 参数
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    ip = body.get('ip', '') or request.query_params.get('ip', '')
    mac = body.get('mac', '') or request.query_params.get('mac', '')
    
    if not ip:
        raise HTTPException(status_code=400, detail="请提供设备IP地址")
    
    try:
        with api_pool_lock:
            if ip in api_pool:
                mt_api = api_pool[ip]
                mt_api.close()
                del api_pool[ip]
                logger.info(f"已登出设备: {ip}")
                
                return {"status": "success", "message": f"已成功登出设备 {ip}", "ip": ip}
            else:
                return {"status": "error", "message": f"设备 {ip} 未登录"}
    except Exception as e:
        logger.error(f"登出错误: {ip} - {e}")
        return {"status": "error", "message": f"登出失败: {e}", "ip": ip}


class SLSCtoolsRequest(BaseModel):
    """SLSCtools 启动请求"""
    mac: str = ""


slsc_process = None
slsc_process_lock = threading.Lock()


@app.post("/api/slsc-tools")
async def open_slsc_tools(request: SLSCtoolsRequest):
    """启动 WinBox (SLSCtools.exe) 并传递 MAC 地址到 Connect To 栏"""
    import ctypes
    
    mac = request.mac
    logger.info(f"收到启动请求: mac={mac}")
    
    if not mac:
        return {"status": "error", "message": "请提供 MAC 地址"}
    
    try:
        base_dir = get_base_dir()
        slsc_path = os.path.join(base_dir, 'SLSCtools.exe')
        
        logger.info(f"程序路径: {slsc_path}, 存在: {os.path.exists(slsc_path)}")
        
        if not os.path.exists(slsc_path):
            return {"status": "error", "message": f"程序不存在: {slsc_path}"}
        
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "open", slsc_path, mac, os.path.dirname(slsc_path), 1
        )
        
        if result <= 32:
            logger.error(f"ShellExecuteW 返回错误码: {result}")
            return {"status": "error", "message": f"启动失败，错误码: {result}"}
        
        logger.info(f"已启动 WinBox，MAC 地址已传递到 Connect To 栏: {mac}")
        
        return {"status": "success", "message": "已启动", "mac": mac}
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        return {"status": "error", "message": f"启动失败: {e}"}


@app.post("/api/slsc-tools/close")
async def close_slsc_tools():
    """关闭 SLSCtools.exe 进程"""
    global slsc_process
    
    try:
        with slsc_process_lock:
            if slsc_process is not None:
                try:
                    slsc_process.terminate()
                    slsc_process.wait(timeout=3)
                    logger.info("已关闭 SLSCtools.exe")
                except subprocess.TimeoutExpired:
                    slsc_process.kill()
                    logger.info("已强制关闭 SLSCtools.exe")
                except Exception as e:
                    logger.warning(f"关闭 SLSCtools.exe 时出错: {e}")
                finally:
                    slsc_process = None
        
        return {"status": "success", "message": "SLSCtools 已关闭"}
    except Exception as e:
        logger.error(f"关闭 SLSCtools 失败: {e}")
        return {"status": "error", "message": f"关闭失败: {e}"}


@app.get("/api/interfaces")
async def get_interfaces(ip: str = Query(...)):
    """获取接口列表（复用 api_pool 连接）"""
    if not ip:
        raise HTTPException(status_code=400, detail="缺少设备IP参数")
    
    try:
        with api_pool_lock:
            if ip not in api_pool:
                return {"status": "error", "message": f"设备 {ip} 未登录", "interfaces": []}
            mt_api = api_pool[ip]
            username = mt_api.username
            password = mt_api.password
        
        # 使用独立连接获取接口（避免锁冲突）
        with api_connection(ip, username, password) as temp_api:
            interfaces = temp_api.get_interfaces()
            return {"status": "success", "interfaces": interfaces}
            
    except Exception as e:
        logger.error(f"获取接口列表错误: {ip} - {e}")
        return {"status": "error", "message": f"获取接口列表失败: {e}", "interfaces": []}


@app.get("/api/cpu-usage")
async def get_cpu_usage(ip: str = Query(...)):
    """获取 CPU 使用率"""
    if not ip:
        raise HTTPException(status_code=400, detail="缺少设备IP参数")
    
    try:
        with api_pool_lock:
            if ip in api_pool:
                mt_api = api_pool[ip]
                cpu_info = mt_api.get_cpu_usage()
                return {"status": "success", "cpu_usage": cpu_info.get('cpu_usage', '0%')}
            else:
                return {"status": "error", "message": f"设备 {ip} 未登录"}
    except Exception as e:
        logger.error(f"获取CPU使用率错误: {ip} - {e}")
        _check_connection_error(str(e), ip)
        return {"status": "error", "message": f"获取CPU使用率失败: {e}"}


@app.get("/api/system-time")
async def get_system_time(ip: str = Query(...)):
    """获取系统时间"""
    if not ip:
        raise HTTPException(status_code=400, detail="缺少设备IP参数")
    
    try:
        with api_pool_lock:
            if ip in api_pool:
                mt_api = api_pool[ip]
                current_time = time.time()
                time_info = mt_api.get_system_time()
                if time_info.get('system_time'):
                    mt_api._cached_system_time = time_info
                    mt_api._cached_system_time_time = current_time
                elif hasattr(mt_api, '_cached_system_time') and mt_api._cached_system_time.get('system_time'):
                    cache_age = current_time - getattr(mt_api, '_cached_system_time_time', 0)
                    if cache_age <= 120:
                        time_info = mt_api._cached_system_time
                return {"status": "success", "system_time": time_info.get('system_time', '')}
            else:
                return {"status": "error", "message": f"设备 {ip} 未登录"}
    except Exception as e:
        logger.error(f"获取系统时间错误: {ip} - {e}")
        _check_connection_error(str(e), ip)
        return {"status": "error", "message": f"获取系统时间失败: {e}"}


@app.get("/api/device-info")
async def get_device_info(ip: str = Query(...), force_refresh: bool = False):
    """获取设备信息"""
    if not ip:
        raise HTTPException(status_code=400, detail="缺少设备IP参数")
    
    try:
        with api_pool_lock:
            if ip in api_pool:
                mt_api = api_pool[ip]
                info = mt_api.get_system_info(force_refresh=force_refresh)
                identity = mt_api.get_identity()
                
                current_time = time.time()
                system_time = mt_api.get_system_time()
                if system_time.get('system_time'):
                    mt_api._cached_system_time = system_time
                    mt_api._cached_system_time_time = current_time
                elif hasattr(mt_api, '_cached_system_time') and mt_api._cached_system_time.get('system_time'):
                    cache_age = current_time - getattr(mt_api, '_cached_system_time_time', 0)
                    if cache_age > 120:
                        system_time = {'system_time': '', 'date': '', 'time': ''}
                    else:
                        system_time = mt_api._cached_system_time
                
                result = {
                    'status': 'success',
                    'info': {
                        'time': system_time.get('system_time', '--'),
                        'date': system_time.get('system_time', '').split(' ')[0] if system_time.get('system_time') else '',
                        'device_time': system_time.get('time', ''),
                        'cpu_load': info.get('cpu-load', '0'),
                        'version': info.get('version', '--'),
                        'voltage': info.get('voltage', '--'),
                        'identity': identity or '--',
                        'uptime': info.get('uptime', '--'),
                        'cpu': info.get('cpu', '--'),
                        'cpu_count': info.get('cpu-count', '--'),
                        'cpu_frequency': str(info.get('cpu-frequency', '--')) + ' MHz' if info.get('cpu-frequency') else '--',
                        'memory_used': format_bytes(int(info.get('total-memory', 0)) - int(info.get('free-memory', 0))) if info.get('free-memory') and info.get('total-memory') else '--',
                        'memory_free': format_bytes(info.get('free-memory', 0)) if info.get('free-memory') else '--',
                        'memory_total': format_bytes(info.get('total-memory', 0)) if info.get('total-memory') else '--',
                        'hdd_used': format_bytes(int(info.get('total-hdd-space', 0)) - int(info.get('free-hdd-space', 0))) if info.get('free-hdd-space') and info.get('total-hdd-space') else '--',
                        'hdd_free': format_bytes(info.get('free-hdd-space', 0)) if info.get('free-hdd-space') else '--',
                        'hdd_total': format_bytes(info.get('total-hdd-space', 0)) if info.get('total-hdd-space') else '--',
                        'architecture': info.get('architecture-name', '--'),
                        'board': info.get('board-name', '--'),
                        'platform': info.get('platform', '--')
                    }
                }
                return result
            else:
                return {"status": "error", "message": f"设备 {ip} 未登录"}
    except Exception as e:
        logger.error(f"获取设备信息错误: {ip} - {e}")
        _check_connection_error(str(e), ip)
        return {"status": "error", "message": f"获取设备信息失败: {e}"}


@app.get("/api/interface-toggle")
async def interface_toggle(ip: str = Query(...), interface: str = Query(...), action: str = Query("disable")):
    """切换接口启用/禁用状态"""
    if not ip or not interface:
        raise HTTPException(status_code=400, detail="缺少参数")
    
    try:
        with api_pool_lock:
            if ip not in api_pool:
                return {"status": "error", "message": f"设备 {ip} 未登录"}
            mt_api = api_pool[ip]
        
        if action == 'disable':
            command = ['/interface/disable', f'=numbers={interface}']
        else:
            command = ['/interface/enable', f'=numbers={interface}']
        
        mt_api.write_sentence(command)
        
        done = False
        for _ in range(100):
            response = mt_api.read_sentence(timeout=10)
            if '!done' in response:
                done = True
                break
            if '!trap' in response:
                break
        
        if done:
            return {"status": "success", "message": f'接口 {interface} 已{"禁用" if action == "disable" else "启用"}'}
        else:
            return {"status": "error", "message": f'操作失败: {response}'}
    except Exception as e:
        logger.error(f"接口切换错误: {ip} - {e}")
        return {"status": "error", "message": f"操作失败: {e}"}


@app.get("/api/wireless-interfaces")
async def get_wireless_interfaces(ip: str = Query(...)):
    """获取无线接口列表"""
    if not ip:
        raise HTTPException(status_code=400, detail="缺少设备IP参数")
    
    try:
        with api_pool_lock:
            if ip in api_pool:
                mt_api = api_pool[ip]
                username = mt_api.username
                password = mt_api.password
            else:
                return {"success": False, "message": f"设备 {ip} 未登录"}
        
        with api_connection(ip, username, password) as temp_api:
            temp_api.write_sentence(['/interface/wireless/print'])
            
            interfaces = []
            while True:
                try:
                    response = temp_api.read_sentence(timeout=10)
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
                                iface[parts[0]] = parts[1]
                    
                    if iface and iface.get('name'):
                        interfaces.append({
                            'name': iface.get('name'),
                            'frequency': iface.get('frequency', '--'),
                            'band': iface.get('band', '--'),
                            'running': iface.get('running', 'false') == 'true',
                            'disabled': iface.get('disabled', 'false') == 'true'
                        })
            
            return {"success": True, "interfaces": interfaces}
    except Exception as e:
        logger.error(f"获取无线接口错误: {ip} - {e}")
        return {"success": False, "message": f"获取无线接口失败: {e}"}


@app.post("/api/check-arp")
async def check_arp(request: CheckArpRequest):
    """检查设备是否可达（通过ARP广播）"""
    ip = request.ip
    if not ip:
        return {"reachable": False}
    
    try:
        import ctypes
        from ctypes import wintypes, POINTER, byref
        
        INETOPT = ctypes.windll.iphlpapi
        SendARP = INETOPT.SendARP
        SendARP.argtypes = [wintypes.ULONG, wintypes.ULONG, POINTER(wintypes.ULONG), POINTER(wintypes.ULONG)]
        SendARP.restype = wintypes.DWORD
        
        dstAddr = struct.unpack('<I', socket.inet_aton(ip))[0]
        
        reachable = False
        lock = threading.Lock()
        threads = []
        
        def send_arp_from_interface(name, localIp):
            nonlocal reachable
            try:
                srcAddr = struct.unpack('<I', socket.inet_aton(localIp))[0]
                macAddr = wintypes.ULONG()
                macAddrLen = wintypes.ULONG(6)
                arpResult = SendARP(dstAddr, srcAddr, byref(macAddr), byref(macAddrLen))
                if arpResult == 0:
                    with lock:
                        reachable = True
            except Exception:
                pass
        
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address != '127.0.0.1':
                    t = threading.Thread(target=send_arp_from_interface, args=(name, addr.address))
                    threads.append(t)
                    t.start()
        
        for t in threads:
            t.join(timeout=3)
        
        return {"reachable": reachable}
    except Exception as e:
        logger.error(f"ARP检查失败: {e}")
        return {"reachable": False, "error": str(e)}


@app.post("/api/security-profile/add")
async def security_profile_add(request: SecurityProfileAddRequest):
    """添加加密配置"""
    if not request.ip or not request.name:
        return {"success": False, "message": "缺少必要参数"}
    
    try:
        with api_connection(request.ip, request.username, request.password) as mt_api:
            cmd = ['/interface/wireless/security-profiles/add']
            cmd.append(f'=name={request.name}')
            if request.authTypes:
                cmd.append(f'=authentication-types={request.authTypes}')
            if request.unicastCiphers:
                cmd.append(f'=unicast-ciphers={request.unicastCiphers}')
            if request.groupCiphers:
                cmd.append(f'=group-ciphers={request.groupCiphers}')
            if request.wpaKey:
                cmd.append(f'=wpa-pre-shared-key={request.wpaKey}')
            if request.wpa2Key:
                cmd.append(f'=wpa2-pre-shared-key={request.wpa2Key}')
            
            mt_api.write_sentence(cmd)
            response = mt_api.read_sentence(timeout=10)
            
            if '!trap' in response:
                error_msg = ''
                for line in response:
                    if line.startswith('=message='):
                        error_msg = line[9:]
                return {"success": False, "message": error_msg or '添加失败'}
            return {"success": True, "message": '添加成功'}
    except Exception as e:
        logger.error(f"添加加密配置错误: {request.ip} - {e}")
        return {"success": False, "message": f"添加失败: {e}"}


@app.post("/api/security-profile/delete")
async def security_profile_delete(request: SecurityProfileDeleteRequest):
    """删除加密配置"""
    if not request.ip or not request.name:
        return {"success": False, "message": "缺少必要参数"}
    
    try:
        with api_connection(request.ip, request.username, request.password) as mt_api:
            # 先查找 profile ID
            mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={request.name}'])
            profile_id = None
            while True:
                try:
                    response = mt_api.read_sentence(timeout=10)
                except Exception:
                    break
                if '!done' in response or '!trap' in response:
                    break
                if '!re' in response:
                    for line in response:
                        if line.startswith('=.id='):
                            profile_id = line[5:]
            
            if not profile_id:
                return {"success": False, "message": "未找到该加密配置"}
            
            mt_api.write_sentence(['/interface/wireless/security-profiles/remove', f'=.id={profile_id}'])
            response = mt_api.read_sentence(timeout=10)
            
            if '!trap' in response:
                error_msg = ''
                for line in response:
                    if line.startswith('=message='):
                        error_msg = line[9:]
                return {"success": False, "message": error_msg or '删除失败'}
            return {"success": True, "message": '删除成功'}
    except Exception as e:
        logger.error(f"删除加密配置错误: {request.ip} - {e}")
        return {"success": False, "message": f"删除失败: {e}"}


@app.post("/api/security-profile/set-mode")
async def security_profile_set_mode(request: SecurityProfileSetModeRequest):
    """设置加密配置模式"""
    if not request.ip or not request.name:
        return {"success": False, "message": "缺少必要参数"}
    
    try:
        with api_connection(request.ip, request.username, request.password) as mt_api:
            mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={request.name}'])
            profile_id = None
            while True:
                try:
                    response = mt_api.read_sentence(timeout=10)
                except Exception:
                    break
                if '!done' in response or '!trap' in response:
                    break
                if '!re' in response:
                    for line in response:
                        if line.startswith('=.id='):
                            profile_id = line[5:]
            
            if not profile_id:
                return {"success": False, "message": "未找到该加密配置"}
            
            mt_api.write_sentence(['/interface/wireless/security-profiles/set', f'=.id={profile_id}', f'=mode={request.mode}'])
            response = mt_api.read_sentence(timeout=10)
            
            if '!trap' in response:
                error_msg = ''
                for line in response:
                    if line.startswith('=message='):
                        error_msg = line[9:]
                return {"success": False, "message": error_msg or '设置失败'}
            return {"success": True, "message": '设置成功'}
    except Exception as e:
        logger.error(f"设置加密配置模式错误: {request.ip} - {e}")
        return {"success": False, "message": f"设置失败: {e}"}


@app.post("/api/security-profile/edit")
async def security_profile_edit(request: SecurityProfileEditRequest):
    """编辑加密配置"""
    if not request.ip or not request.originalName:
        return {"success": False, "message": "缺少必要参数"}
    
    try:
        with api_connection(request.ip, request.username, request.password) as mt_api:
            mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={request.originalName}'])
            profile_id = None
            while True:
                try:
                    response = mt_api.read_sentence(timeout=10)
                except Exception:
                    break
                if '!done' in response or '!trap' in response:
                    break
                if '!re' in response:
                    for line in response:
                        if line.startswith('=.id='):
                            profile_id = line[5:]
            
            if not profile_id:
                return {"success": False, "message": "未找到该加密配置"}
            
            cmd = ['/interface/wireless/security-profiles/set', f'=.id={profile_id}']
            if request.name:
                cmd.append(f'=name={request.name}')
            if request.authTypes:
                cmd.append(f'=authentication-types={request.authTypes}')
            if request.unicastCiphers:
                cmd.append(f'=unicast-ciphers={request.unicastCiphers}')
            if request.groupCiphers:
                cmd.append(f'=group-ciphers={request.groupCiphers}')
            if request.wpaKey:
                cmd.append(f'=wpa-pre-shared-key={request.wpaKey}')
            if request.wpa2Key:
                cmd.append(f'=wpa2-pre-shared-key={request.wpa2Key}')
            
            mt_api.write_sentence(cmd)
            response = mt_api.read_sentence(timeout=10)
            
            if '!trap' in response:
                error_msg = ''
                for line in response:
                    if line.startswith('=message='):
                        error_msg = line[9:]
                return {"success": False, "message": error_msg or '修改失败'}
            return {"success": True, "message": '修改成功'}
    except Exception as e:
        logger.error(f"修改加密配置错误: {request.ip} - {e}")
        return {"success": False, "message": f"修改失败: {e}"}


# ==================== 入口 ====================

if __name__ == '__main__':
    import uvicorn
    
    # 配置日志
    log_level = CONFIG.get('logging', {}).get('level', 'INFO')
    log_format = CONFIG.get('logging', {}).get('format', '%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logging.basicConfig(level=getattr(logging, log_level), format=log_format)
    
    if platform.system() == "Windows":
        import ctypes
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                logger.warning("建议以管理员权限运行以获得最佳效果")
        except Exception:
            pass
    
    host = CONFIG.get('server', {}).get('host', '0.0.0.0')
    port = CONFIG.get('server', {}).get('http_port', 32995)
    tls_config = CONFIG.get('tls', {})
    
    ssl_kwargs = {}
    if tls_config.get('enabled') and tls_config.get('cert_file') and tls_config.get('key_file'):
        from ssl_context import get_server_ssl_context
        ssl_kwargs['ssl'] = get_server_ssl_context(tls_config['cert_file'], tls_config['key_file'])
        logger.info(f"TLS 已启用 (cert={tls_config['cert_file']})")
    
    logger.info(f"API Server 启动在 {'https' if ssl_kwargs else 'http'}://{host}:{port}")
    logger.info(f"前端网页地址: {'https' if ssl_kwargs else 'http'}://localhost:{port}")
    logger.info(f"API 文档地址: {'https' if ssl_kwargs else 'http'}://localhost:{port}/docs")
    
    uvicorn.run(app, host=host, port=port, **ssl_kwargs)
