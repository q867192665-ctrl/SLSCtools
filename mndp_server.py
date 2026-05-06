#!/usr/bin/env python3
import os
import glob
import socket
import struct
import threading
import json
import time
import platform
import sys
import psutil
import re
from routeros_api import RouterOsApiPool
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
socket.setdefaulttimeout(15)
from datetime import datetime
from mikrotik_api import MikroTikAPI
from websocket_server import mark_device_offline

MNDP_TYPES = {
    0x01: "MAC-Address",
    0x05: "Identity",
    0x07: "Version",
    0x08: "Platform",
    0x0a: "Uptime",
    0x10: "Interface name",
    0x11: "IPv4-Address"
}

discovered_devices = {}
devices_lock = threading.Lock()
api_pool = {}
api_pool_lock = threading.Lock()
ros_pool = {}
ros_pool_lock = threading.Lock()
log_api_pool = {}
log_api_pool_lock = threading.Lock()
log_api_locks = {}
log_api_locks_lock = threading.Lock()
DEVICE_EXPIRE_SECONDS = 10

LOG_CACHE_TTL = 5
LOG_CACHE_MAX = 2000
log_cache = {}
log_cache_lock = threading.Lock()


def get_or_init_log_cache(ip):
    with log_cache_lock:
        if ip not in log_cache:
            log_cache[ip] = {
                'logs': [],
                'last_id': None,
                'last_update': 0,
                'lock': threading.Lock(),
                'updating': False,
            }
        return log_cache[ip]


def refresh_log_cache(ip, username, password):
    cache = get_or_init_log_cache(ip)
    if cache['updating']:
        return True
    if not cache['lock'].acquire(timeout=10):
        print(f"refresh_log_cache: 获取锁超时 {ip}")
        return False
    try:
        cache['updating'] = True
        log_api = get_log_api_connection(ip, username, password)
        if not log_api:
            return False
        logs = log_api.get_logs(limit=LOG_CACHE_MAX)
        if logs is None:
            logs = []
        cache['logs'] = logs[-LOG_CACHE_MAX:] if len(logs) > LOG_CACHE_MAX else logs
        if logs:
            cache['last_id'] = logs[-1].get('id', '')
        cache['last_update'] = time.time()
        print(f"refresh_log_cache: {ip} 缓存更新, {len(cache['logs'])} 条日志")
        return True
    except Exception as e:
        print(f"refresh_log_cache 失败: {ip} - {e}")
        return False
    finally:
        cache['updating'] = False
        cache['lock'].release()


def get_cached_logs(ip, last_id=None):
    cache = get_or_init_log_cache(ip)
    logs = cache['logs']
    if not logs:
        return []
    if last_id:
        for i in range(len(logs) - 1, -1, -1):
            if logs[i].get('id') == last_id:
                return logs[i + 1:]
    return logs


def clear_log_cache(ip):
    with log_cache_lock:
        if ip in log_cache:
            cache = log_cache.pop(ip)
            cache['logs'] = []
            print(f"已清理日志缓存: {ip}")


def get_log_api_lock(ip):
    with log_api_locks_lock:
        if ip not in log_api_locks:
            log_api_locks[ip] = threading.Lock()
        return log_api_locks[ip]


def get_log_api_connection(ip, username, password):
    with log_api_pool_lock:
        if ip in log_api_pool:
            mt_api = log_api_pool[ip]
            if mt_api.logged_in:
                return mt_api
            else:
                try:
                    mt_api.close()
                except:
                    pass
                del log_api_pool[ip]

    mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
    success, message = mt_api.login()
    if success:
        with log_api_pool_lock:
            if ip in log_api_pool:
                try:
                    log_api_pool[ip].close()
                except:
                    pass
            log_api_pool[ip] = mt_api
        return mt_api
    else:
        print(f"日志连接失败: {ip} - {message}")
        return None


def close_log_api_connection(ip):
    with log_api_pool_lock:
        if ip in log_api_pool:
            mt_api = log_api_pool[ip]
            try:
                mt_api.close()
            except:
                pass
            del log_api_pool[ip]
            print(f"已关闭日志连接: {ip}")
    with log_api_locks_lock:
        if ip in log_api_locks:
            del log_api_locks[ip]

VIRTUAL_ADAPTER_KEYWORDS = [
    'virtual', 'vmware', 'virtualbox', 'vbox', 'hyper-v', 'loopback',
    'bluetooth', 'tunnel', 'teredo', 'isatap', '6to4', 'pseudo',
    'docker', 'veth', 'bridge', 'vnic', 'wan miniport', 'ras',
    'cisco anyconnect', 'fortinet', 'checkpoint', 'pulse secure',
    'vpn', 'tap', 'tun', 'wintun', 'wireguard'
]

def get_network_interfaces():
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
            except Exception as e:
                continue
    except Exception as e:
        print(f"获取网卡列表失败: {e}")
    
    return interfaces

class MNDPCore:
    def __init__(self):
        self.devices = []
        self.is_running = False
        self.sock = None
        self.listener_thread = None
    
    def _create_udp_socket(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", 5678))
            
            try:
                mreq = struct.pack("4sl", socket.inet_aton("239.255.255.255"), socket.INADDR_ANY)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except Exception as e:
                print(f"加入组播组失败（可能在Windows上）: {e}")
            
            return sock
        except Exception as e:
            print(f"创建MNDP套接字失败: {e}")
            return None
    
    def send_discovery_packet(self, interface_name=None):
        discovery_packet = b"\x00\x00\x00\x00\x00\x01\x00\x00"
        target_addresses = ["255.255.255.255", "239.255.255.255"]

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
                        for i in range(2):
                            try:
                                temp_sock.sendto(discovery_packet, (addr, 5678))
                                print(f"已发送MNDP发现包 (网卡: {iface['friendly_name']}, IP: {local_ip}, 目标: {addr}, 第 {i+1}/2 次)")
                            except Exception as e:
                                print(f"发送失败 (网卡: {iface['friendly_name']}, 目标: {addr}): {e}")

                    temp_sock.close()
                    sent_count += 1
                except Exception as e:
                    print(f"网卡 {iface['friendly_name']} 发送失败: {e}")
                    continue

            if sent_count > 0:
                print(f"已通过 {sent_count} 个网卡发送MNDP发现包，每个网卡向 {len(target_addresses)} 个地址各发送 2 次")
                return True
            else:
                print("没有可用的网卡发送MNDP发现包")
                return False

        except Exception as e:
            print(f"发送MNDP发现包失败: {e}")
            return False

    def _get_broadcast_address(self, ip_address):
        import re
        netmask_bits = 24
        ip_int = 0
        parts = ip_address.split('.')
        for part in parts:
            ip_int = (ip_int << 8) | int(part)
        broadcast_int = ip_int | ((1 << (32 - netmask_bits)) - 1)
        broadcast_parts = []
        temp = broadcast_int
        for _ in range(4):
            broadcast_parts.insert(0, str(temp & 0xFF))
            temp >>= 8
        return '.'.join(broadcast_parts)
    
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
                        except:
                            continue
                    else:
                        dev[field_name] = field_value.decode('utf-8', 'replace').strip()
            
            return dev if dev["MAC-Address"] else None
        except Exception as e:
            print(f"解析MNDP数据包失败: {e}")
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
                            print(f"发现新设备: {device_info.get('Identity', 'Unknown')} ({device_info.get('IPv4-Address', 'N/A')})")
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    print(f"监听MNDP数据包出错: {e}")
                    continue
    
    def start_listener(self):
        if not self.is_running:
            self.sock = self._create_udp_socket()
            if self.sock:
                self.is_running = True
                self.listener_thread = threading.Thread(target=self._listener, daemon=True)
                self.listener_thread.start()
                print("MNDP监听已启动（端口5678）")
                return True
        return False
    
    def stop_listener(self):
        self.is_running = False
        if self.sock:
            self.sock.close()
        print("MNDP监听已停止")
    
    def get_devices(self):
        with devices_lock:
            return list(discovered_devices.values())

    def cleanup_expired_devices(self):
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

class APIHandler(BaseHTTPRequestHandler):
    mndp_core = None
    
    def log_message(self, format, *args):
        pass
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path.startswith('/api/'):
            if parsed_path.path == '/api/devices':
                self.handle_get_devices()
            elif parsed_path.path == '/api/interfaces':
                self.handle_get_interfaces()
            elif parsed_path.path == '/api/refresh':
                self.handle_refresh()
            elif parsed_path.path == '/api/discover':
                self.handle_discover()
            elif parsed_path.path == '/api/connect':
                self.handle_connect(parsed_path)
            elif parsed_path.path == '/api/logout':
                self.handle_logout(parsed_path)
            elif parsed_path.path == '/api/logs':
                self.handle_logs(parsed_path)
            elif parsed_path.path == '/api/cpu-usage':
                self.handle_get_cpu_usage(parsed_path)
            elif parsed_path.path == '/api/system-time':
                self.handle_get_system_time(parsed_path)
            elif parsed_path.path == '/api/device-info':
                self.handle_device_info(parsed_path)
            elif parsed_path.path == '/api/interface-toggle':
                self.handle_interface_toggle(parsed_path)
            elif parsed_path.path == '/api/wireless-interfaces':
                self.handle_wireless_interfaces(parsed_path)
            else:
                self.send_error(404, "Not Found")
        else:
            # 处理静态文件请求
            self.handle_static_file(parsed_path.path)
    
    def do_POST(self):
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/api/connect':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_connect_post(post_data)
        elif parsed_path.path == '/api/logout':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_logout_post(post_data)
        elif parsed_path.path == '/api/refresh':
            self.handle_refresh()
        elif parsed_path.path == '/api/discover':
            self.handle_discover_post()
        elif parsed_path.path == '/api/check-arp':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_check_arp(post_data)
        elif parsed_path.path == '/api/security-profile/add':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_security_profile_add(post_data)
        elif parsed_path.path == '/api/security-profile/delete':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_security_profile_delete(post_data)
        elif parsed_path.path == '/api/security-profile/set-mode':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_security_profile_set_mode(post_data)
        elif parsed_path.path == '/api/security-profile/edit':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_security_profile_edit(post_data)
        elif parsed_path.path == '/api/slsc-tools':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            self.handle_slsc_tools(post_data)
        elif parsed_path.path == '/api/slsc-tools/close':
            self.handle_slsc_tools_close()
        else:
            self.send_error(404, "Not Found")

    def handle_check_arp(self, post_data):
        """检查设备是否可达（通过ARP广播到所有网卡，并行发送）"""
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip', '')

            if not ip:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'reachable': False}, ensure_ascii=False).encode('utf-8'))
                return

            import ctypes
            from ctypes import wintypes, POINTER, byref
            import struct
            import socket
            import threading
            import time

            INETOPT = ctypes.windll.iphlpapi
            SendARP = INETOPT.SendARP
            SendARP.argtypes = [wintypes.ULONG, wintypes.ULONG, POINTER(wintypes.ULONG), POINTER(wintypes.ULONG)]
            SendARP.restype = wintypes.DWORD

            dstAddr = struct.unpack('<I', socket.inet_aton(ip))[0]

            print(f"[ARP] 检查 {ip} 是否可达，同时向所有网卡发送...")

            reachable = False
            lock = threading.Lock()
            threads = []
            results = {}

            def send_arp_from_interface(name, localIp):
                nonlocal reachable
                try:
                    print(f"[ARP] 从 {localIp} ({name}) 发送ARP请求...")
                    srcAddr = struct.unpack('<I', socket.inet_aton(localIp))[0]
                    macAddr = wintypes.ULONG()
                    macAddrLen = wintypes.ULONG(6)
                    arpResult = SendARP(dstAddr, srcAddr, byref(macAddr), byref(macAddrLen))
                    with lock:
                        results[name] = {
                            'success': arpResult == 0,
                            'mac': None,
                            'error': arpResult
                        }
                        if arpResult == 0:
                            mac = ':'.join(f'{(macAddr.value >> (i*8)) & 0xff:02x}' for i in range(5, -1, -1))
                            results[name]['mac'] = mac
                            reachable = True
                            print(f"[ARP] {localIp} 收到响应! MAC: {mac}")
                except Exception as e:
                    print(f"[ARP] {localIp} 请求失败: {e}")
                    with lock:
                        results[name] = {'success': False, 'error': str(e)}

            for name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address != '127.0.0.1':
                        localIp = addr.address
                        t = threading.Thread(target=send_arp_from_interface, args=(name, localIp))
                        threads.append(t)
                        t.start()

            for t in threads:
                t.join(timeout=3)

            print(f"[ARP] 结果: {'可达' if reachable else '不可达'}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'reachable': reachable}, ensure_ascii=False).encode('utf-8'))

        except Exception as e:
            print(f"ARP检查失败: {e}")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'reachable': False, 'error': str(e)}, ensure_ascii=False).encode('utf-8'))
    
    def handle_get_devices(self):
        devices_list = self.mndp_core.get_devices()
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(devices_list, ensure_ascii=False).encode('utf-8'))
    
    def handle_get_interfaces(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]

        if not ip:
            result = {'status': 'error', 'message': '缺少设备IP参数'}
        else:
            temp_api = None
            try:
                with api_pool_lock:
                    if ip not in api_pool:
                        result = {'status': 'error', 'message': f'设备 {ip} 未登录', 'interfaces': []}
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                        return
                    mt_api = api_pool[ip]
                    username = mt_api.username
                    password = mt_api.password
                
                print(f"handle_get_interfaces: 创建独立连接到 {ip}...")
                temp_api = MikroTikAPI(ip, username, password)
                success, message = temp_api.login()
                
                if not success:
                    result = {'status': 'error', 'message': f'连接失败: {message}', 'interfaces': []}
                else:
                    print(f"handle_get_interfaces: 调用 get_interfaces()...")
                    interfaces = temp_api.get_interfaces()
                    print(f"handle_get_interfaces: 获取到 {len(interfaces)} 个接口")
                    result = {'status': 'success', 'interfaces': interfaces}
            except Exception as e:
                result = {'status': 'error', 'message': f'获取接口列表失败: {str(e)}', 'interfaces': []}
                print(f"获取接口列表错误: {ip} - {str(e)}")
                import traceback
                traceback.print_exc()
            finally:
                if temp_api:
                    try:
                        temp_api.close()
                        print(f"handle_get_interfaces: 已关闭独立连接")
                    except:
                        pass

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    
    def handle_get_cpu_usage(self, parsed_path):
        query = parsed_path.query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]
        
        if not ip:
            result = {
                'status': 'error',
                'message': '缺少设备IP参数'
            }
        else:
            try:
                with api_pool_lock:
                    if ip in api_pool:
                        mt_api = api_pool[ip]
                        cpu_info = mt_api.get_cpu_usage()
                        result = {
                            'status': 'success',
                            'cpu_usage': cpu_info.get('cpu_usage', '0%')
                        }
                    else:
                        result = {
                            'status': 'error',
                            'message': f'设备 {ip} 未登录'
                        }
            except Exception as e:
                result = {
                    'status': 'error',
                    'message': f'获取CPU使用率失败: {str(e)}'
                }
                print(f"获取CPU使用率错误: {ip} - {str(e)}")
                error_str = str(e).lower()
                if '10054' in error_str or 'reset' in error_str or 'refused' in error_str or 'timed out' in error_str or '关闭' in error_str:
                    print(f"[HTTP离线检测] 检测到设备 {ip} 连接异常，通知watch_logs")
                    try:
                        mark_device_offline(ip)
                    except Exception as notify_err:
                        print(f"[HTTP离线检测] 通知失败: {notify_err}")
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    
    def handle_get_system_time(self, parsed_path):
        query = parsed_path.query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]
        
        if not ip:
            result = {
                'status': 'error',
                'message': '缺少设备IP参数'
            }
        else:
            try:
                with api_pool_lock:
                    if ip in api_pool:
                        mt_api = api_pool[ip]
                        import time
                        current_time = time.time()
                        time_info = mt_api.get_system_time()
                        if time_info.get('system_time'):
                            mt_api._cached_system_time = time_info
                            mt_api._cached_system_time_time = current_time
                        elif hasattr(mt_api, '_cached_system_time') and mt_api._cached_system_time.get('system_time'):
                            cache_age = current_time - getattr(mt_api, '_cached_system_time_time', 0)
                            if cache_age <= 120:
                                time_info = mt_api._cached_system_time
                        result = {
                            'status': 'success',
                            'system_time': time_info.get('system_time', '')
                        }
                    else:
                        result = {
                            'status': 'error',
                            'message': f'设备 {ip} 未登录'
                        }
            except Exception as e:
                result = {
                    'status': 'error',
                    'message': f'获取系统时间失败: {str(e)}'
                }
                print(f"获取系统时间错误: {ip} - {str(e)}")
                error_str = str(e).lower()
                if '10054' in error_str or 'reset' in error_str or 'refused' in error_str or 'timed out' in error_str or '关闭' in error_str:
                    print(f"[HTTP离线检测] 检测到设备 {ip} 连接异常，通知watch_logs")
                    try:
                        mark_device_offline(ip)
                    except Exception as notify_err:
                        print(f"[HTTP离线检测] 通知失败: {notify_err}")
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_device_info(self, parsed_path):
        query = parsed_path.query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]
        force_refresh = params.get('force_refresh', ['false'])[0].lower() == 'true'

        if not ip:
            result = {
                'status': 'error',
                'message': '缺少设备IP参数'
            }
        else:
            try:
                with api_pool_lock:
                    if ip in api_pool:
                        mt_api = api_pool[ip]
                        info = mt_api.get_system_info(force_refresh=force_refresh)
                        identity = mt_api.get_identity()

                        import time
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
                        
                        def format_bytes(bytes_val):
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
                            except:
                                return '--'
                        
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
                    else:
                        result = {
                            'status': 'error',
                            'message': f'设备 {ip} 未登录'
                        }
            except Exception as e:
                result = {
                    'status': 'error',
                    'message': f'获取设备信息失败: {str(e)}'
                }
                print(f"获取设备信息错误: {ip} - {str(e)}")
                error_str = str(e).lower()
                if '10054' in error_str or 'reset' in error_str or 'refused' in error_str or 'timed out' in error_str or '关闭' in error_str:
                    print(f"[HTTP离线检测] 检测到设备 {ip} 连接异常，通知watch_logs")
                    try:
                        mark_device_offline(ip)
                    except Exception as notify_err:
                        print(f"[HTTP离线检测] 通知失败: {notify_err}")
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def _format_bitrate(self, bps):
        if bps >= 1_000_000_000:
            return f"{bps / 1_000_000_000:.2f} Gbps"
        elif bps >= 1_000_000:
            return f"{bps / 1_000_000:.2f} Mbps"
        elif bps >= 1_000:
            return f"{bps / 1_000:.2f} Kbps"
        else:
            return f"{bps} bps"

    def handle_interface_toggle(self, parsed_path):
        query = parsed_path.query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]
        interface_name = params.get('interface', [None])[0]
        action = params.get('action', ['disable'])[0]

        if not ip or not interface_name:
            result = {'status': 'error', 'message': '缺少参数'}
        else:
            try:
                with api_pool_lock:
                    if ip not in api_pool:
                        result = {'status': 'error', 'message': f'设备 {ip} 未登录'}
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                        return
                    mt_api = api_pool[ip]

                if action == 'disable':
                    command = ['/interface/disable', f'=numbers={interface_name}']
                else:
                    command = ['/interface/enable', f'=numbers={interface_name}']
                
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
                    result = {'status': 'success', 'message': f'接口 {interface_name} 已{"禁用" if action == "disable" else "启用"}'}
                else:
                    result = {'status': 'error', 'message': f'操作失败: {response}'}
            except Exception as e:
                result = {'status': 'error', 'message': f'操作失败: {str(e)}'}
                print(f"接口切换错误: {ip} - {str(e)}")

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_wireless_interfaces(self, parsed_path):
        query = parsed_path.query
        params = parse_qs(query)
        ip = params.get('ip', [None])[0]
        username = params.get('username', [None])[0]
        password = params.get('password', [''])[0]

        if not ip:
            result = {'success': False, 'message': '缺少设备IP参数'}
        else:
            try:
                with api_pool_lock:
                    if ip in api_pool:
                        mt_api = api_pool[ip]
                    else:
                        from mikrotik_api import MikroTikAPI
                        mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
                        success, message = mt_api.login()
                        if not success:
                            result = {'success': False, 'message': f'连接失败: {message}'}
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                            return
                
                mt_api.write_sentence(['/interface/wireless/print'])
                
                interfaces = []
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
                            interfaces.append({
                                'name': iface.get('name'),
                                'frequency': iface.get('frequency', '--'),
                                'band': iface.get('band', '--'),
                                'running': iface.get('running', 'false') == 'true',
                                'disabled': iface.get('disabled', 'false') == 'true'
                            })
                
                result = {'success': True, 'interfaces': interfaces}
            except Exception as e:
                result = {'success': False, 'message': f'获取无线接口失败: {str(e)}'}
                print(f"获取无线接口错误: {ip} - {str(e)}")

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_refresh(self):
        self.mndp_core.cleanup_expired_devices()
        self.mndp_core.send_discovery_packet()
        # 等待设备响应
        time.sleep(3)
        devices_list = self.mndp_core.get_devices()

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(devices_list, ensure_ascii=False).encode('utf-8'))
    
    def handle_discover(self, interface_name=None):
        success = self.mndp_core.send_discovery_packet(interface_name)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        if success:
            self.wfile.write(json.dumps({'status': 'success', 'message': 'MNDP发现包已发送'}, ensure_ascii=False).encode('utf-8'))
        else:
            self.wfile.write(json.dumps({'status': 'error', 'message': '发送MNDP发现包失败'}, ensure_ascii=False).encode('utf-8'))
    
    def handle_discover_post(self):
        self.handle_discover(None)
    
    def handle_connect(self, parsed_path):
        query_params = parse_qs(parsed_path.query)
        ip = query_params.get('ip', [''])[0]
        username = query_params.get('username', [''])[0]
        password = query_params.get('password', [''])[0]
        
        result = {
            'status': 'success',
            'message': f'正在连接到 {ip}...',
            'ip': ip,
            'username': username
        }
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_logout_post(self, post_data):
        """处理POST请求的登出"""
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip', '')
            mac = data.get('mac', '')
            result = self._do_logout(ip, mac)
        except Exception as e:
            result = {
                'status': 'error',
                'message': f'登出失败: {str(e)}'
            }
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_logout(self, parsed_path):
        query_params = parse_qs(parsed_path.query)
        ip = query_params.get('ip', [''])[0]
        mac = query_params.get('mac', [''])[0]
        result = self._do_logout(ip, mac)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def _do_logout(self, ip, mac):
        """执行登出逻辑"""
        if not ip:
            return {
                'status': 'error',
                'message': '请提供设备IP地址'
            }
        
        try:
            was_logged_in = False
            with api_pool_lock:
                if ip in api_pool:
                    was_logged_in = True
                    mt_api = api_pool[ip]
                    mt_api.close()
                    del api_pool[ip]
                    print(f"已登出设备: {ip}")

                    with ros_pool_lock:
                        if ip in ros_pool:
                            ros_pool[ip].disconnect()
                            del ros_pool[ip]
                            print(f"已关闭连接: {ip}")

                    try:
                        from websocket_server import device_api_connections, api_conn_lock as ws_api_conn_lock
                        with ws_api_conn_lock:
                            if ip in device_api_connections:
                                old_ws_api = device_api_connections[ip]
                                try:
                                    old_ws_api.close()
                                except:
                                    pass
                                del device_api_connections[ip]
                                print(f"已关闭 WebSocket 侧连接: {ip}")
                    except Exception as ws_cleanup_err:
                        print(f"清理 WebSocket 连接时出错: {ws_cleanup_err}")

                    try:
                        from websocket_server import interface_api_connections, interface_api_lock as ws_iface_lock
                        with ws_iface_lock:
                            if ip in interface_api_connections:
                                old_iface_api = interface_api_connections[ip]
                                try:
                                    old_iface_api.close()
                                except:
                                    pass
                                del interface_api_connections[ip]
                                print(f"已关闭 WebSocket 侧接口连接: {ip}")
                    except Exception as ws_cleanup_err:
                        print(f"清理 WebSocket 接口连接时出错: {ws_cleanup_err}")

            close_log_api_connection(ip)
            clear_log_cache(ip)
            print(f"[登出] 清理mndp_server日志缓存: {ip}")

            try:
                from websocket_server import clear_log_cache as ws_clear_log_cache
                from websocket_server import log_cache_store, log_cache_store_lock
                with log_cache_store_lock:
                    print(f"[登出] websocket日志缓存当前keys: {list(log_cache_store.keys())}")
                ws_clear_log_cache(ip)
            except Exception as ws_cache_err:
                print(f"清理 WebSocket 日志缓存时出错: {ws_cache_err}")

            device_id = mac if mac else ip
            device_id = device_id.replace(':', '_').replace('-', '_')
            
            log_file = f'{device_id}_log_file.txt'
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    ftp_file = f.read().strip()
                if ftp_file and os.path.exists(ftp_file):
                    try:
                        os.remove(ftp_file)
                        print(f"删除FTP日志文件：{ftp_file}")
                    except Exception as e:
                        print(f"删除FTP日志文件失败：{e}")
                try:
                    os.remove(log_file)
                except:
                    pass
            
            position_file = f'{device_id}_position.txt'
            if os.path.exists(position_file):
                try:
                    os.remove(position_file)
                except:
                    pass
            
            patterns = [
                f'device_{device_id}*.txt',
                f'{ip}_log_file.txt',
                f'{ip}_position.txt'
            ]
            for pattern in patterns:
                files = glob.glob(pattern)
                for file_path in files:
                    try:
                        os.remove(file_path)
                        print(f"清理残留文件：{file_path}")
                    except Exception as e:
                        print(f"清理文件失败 {file_path}：{e}")
            
            try:
                from websocket_server import clear_device_download_status
                clear_device_download_status(ip)
                if mac:
                    clear_device_download_status(mac)
            except Exception as dl_err:
                print(f"清除下载状态时出错: {dl_err}")
            
            if was_logged_in:
                return {
                    'status': 'success',
                    'message': f'已成功登出设备 {ip}',
                    'ip': ip
                }
            else:
                return {
                    'status': 'success',
                    'message': f'设备 {ip} 缓存已清理',
                    'ip': ip
                }
        except Exception as e:
            print(f"登出错误: {ip} - {str(e)}")
            return {
                'status': 'error',
                'message': f'登出失败: {str(e)}',
                'ip': ip
            }
    
    def handle_logs(self, parsed_path):
        query_params = parse_qs(parsed_path.query)
        ip = query_params.get('ip', [''])[0]
        last_id = query_params.get('last_id', [None])[0]
        
        if not ip:
            result = {
                'status': 'error',
                'message': '请提供设备IP地址'
            }
        else:
            try:
                with api_pool_lock:
                    if ip not in api_pool:
                        result = {
                            'status': 'error',
                            'message': f'设备 {ip} 未登录'
                        }
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                        return
                    mt_api = api_pool[ip]
                    username = mt_api.username
                    password = mt_api.password
                
                cache = get_or_init_log_cache(ip)
                now = time.time()
                
                if now - cache['last_update'] > LOG_CACHE_TTL:
                    refresh_log_cache(ip, username, password)
                
                logs = get_cached_logs(ip, last_id)
                result = {
                    'status': 'success',
                    'logs': logs
                }
            except Exception as e:
                result = {
                    'status': 'error',
                    'message': f'获取日志失败: {str(e)}'
                }
                print(f"获取日志错误: {ip} - {str(e)}")
                import traceback
                traceback.print_exc()
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    
    def handle_static_file(self, path):
        # 处理静态文件请求
        import os
        
        print(f"静态文件请求: path={path}")
        
        # 默认返回index.html
        if path == '/' or path == '':
            file_path = 'static/index.html'
        else:
            # 移除开头的'/'
            file_path = path[1:]
        
        print(f"处理文件路径: {file_path}")
        print(f"当前工作目录: {os.getcwd()}")
        print(f"文件是否存在: {os.path.exists(file_path)}")
        print(f"是否为文件: {os.path.isfile(file_path) if os.path.exists(file_path) else 'N/A'}")
        
        # 确保文件存在且在当前目录
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            print(f"文件不存在或不是文件: {file_path}")
            self.send_error(404, f"File Not Found: {file_path}")
            return
        
        # 确定文件类型
        content_type = 'text/plain'
        if file_path.endswith('.html'):
            content_type = 'text/html'
        elif file_path.endswith('.css'):
            content_type = 'text/css'
        elif file_path.endswith('.js'):
            content_type = 'application/javascript'
        elif file_path.endswith('.json'):
            content_type = 'application/json'
        elif file_path.endswith('.png'):
            content_type = 'image/png'
        elif file_path.endswith('.jpg') or file_path.endswith('.jpeg'):
            content_type = 'image/jpeg'
        elif file_path.endswith('.gif'):
            content_type = 'image/gif'
        
        print(f"文件类型: {content_type}")
        
        try:
            print(f"尝试打开文件: {file_path}")
            with open(file_path, 'rb') as f:
                content = f.read()
            
            print(f"文件大小: {len(content)} 字节")
            
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
            print(f"文件发送成功: {file_path}")
        except Exception as e:
            print(f"文件读取错误: {str(e)}")
            self.send_error(500, f"Internal Server Error: {str(e)}")
    
    def handle_connect_post(self, post_data):
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip', '')
            username = data.get('username', '')
            password = data.get('password', '')
            
            if not ip:
                result = {
                    'status': 'error',
                    'message': '请输入设备IP地址'
                }
            elif not username:
                result = {
                    'status': 'error',
                    'message': '请输入用户名'
                }
            else:
                try:
                    print(f"尝试登录设备: {ip} (用户: {username})")
                    
                    # 总是创建新连接，避免使用失效的连接池
                    print(f"创建新连接：{ip}")
                    mt_api = MikroTikAPI(ip, username, password)
                    success, message = mt_api.login()
                    
                    if success:
                        print(f"登录成功，开始获取系统信息...")
                        system_info = mt_api.get_system_info()
                        print(f"系统信息: {system_info}")
                        routeros_version = system_info.get('version', 'Unknown') if system_info else 'Unknown'
                        board_name = system_info.get('board-name', 'Unknown') if system_info else 'Unknown'

                        print(f"开始获取设备名称...")
                        identity = mt_api.get_identity()
                        print(f"设备名称: {identity}")
                        if not identity:
                            identity = ip

                        # 将连接加入连接池
                        with api_pool_lock:
                            api_pool[ip] = mt_api
                        
                        result = {
                            'status': 'success',
                            'message': message,
                            'ip': ip,
                            'username': username,
                            'api_version': mt_api.api_version,
                            'routeros_version': routeros_version,
                            'board_name': board_name,
                            'identity': identity
                        }
                        print(f"登录成功: {ip} (版本 {routeros_version}, Identity: {identity})")
                    else:
                        result = {
                            'status': 'error',
                            'message': message,
                            'ip': ip,
                            'username': username
                        }
                        print(f"登录失败: {ip} - {message}")
                        # 登录失败时关闭连接
                        if 'mt_api' in locals():
                            mt_api.close()
                except Exception as e:
                    result = {
                        'status': 'error',
                        'message': f'连接错误: {str(e)}',
                        'ip': ip,
                        'username': username
                    }
                    print(f"连接错误: {ip} - {str(e)}")
                    # 发生错误时关闭连接
                    if 'mt_api' in locals():
                        mt_api.close()
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.send_error(400, str(e))

    def handle_security_profile_add(self, post_data):
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip')
            username = data.get('username')
            password = data.get('password', '')
            name = data.get('name', '')
            auth_types = data.get('authTypes', '')
            unicast_ciphers = data.get('unicastCiphers', '')
            group_ciphers = data.get('groupCiphers', '')
            wpa_key = data.get('wpaKey', '')
            wpa2_key = data.get('wpa2Key', '')

            if not ip or not name:
                result = {'success': False, 'message': '缺少必要参数'}
            else:
                try:
                    from mikrotik_api import MikroTikAPI
                    mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    
                    if not success:
                        result = {'success': False, 'message': f'连接失败: {message}'}
                    else:
                        cmd = ['/interface/wireless/security-profiles/add']
                        cmd.append(f'=name={name}')
                        if auth_types:
                            cmd.append(f'=authentication-types={auth_types}')
                        if unicast_ciphers:
                            cmd.append(f'=unicast-ciphers={unicast_ciphers}')
                        if group_ciphers:
                            cmd.append(f'=group-ciphers={group_ciphers}')
                        if wpa_key:
                            cmd.append(f'=wpa-pre-shared-key={wpa_key}')
                        if wpa2_key:
                            cmd.append(f'=wpa2-pre-shared-key={wpa2_key}')
                        
                        mt_api.write_sentence(cmd)
                        
                        response = mt_api.read_sentence(timeout=10)
                        
                        if '!trap' in response:
                            error_msg = ''
                            for line in response:
                                if line.startswith('=message='):
                                    error_msg = line[9:]
                            result = {'success': False, 'message': error_msg or '添加失败'}
                        else:
                            result = {'success': True, 'message': '添加成功'}
                        
                        mt_api.close()
                except Exception as e:
                    result = {'success': False, 'message': f'添加失败: {str(e)}'}
                    print(f"添加加密配置错误: {ip} - {str(e)}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.send_error(400, str(e))

    def handle_security_profile_delete(self, post_data):
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip')
            username = data.get('username')
            password = data.get('password', '')
            name = data.get('name', '')

            if not ip or not name:
                result = {'success': False, 'message': '缺少必要参数'}
            else:
                try:
                    from mikrotik_api import MikroTikAPI
                    mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    
                    if not success:
                        result = {'success': False, 'message': f'连接失败: {message}'}
                    else:
                        mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={name}'])
                        
                        profile_id = None
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
                                for line in response:
                                    if line.startswith('=.id='):
                                        profile_id = line[5:]
                        
                        if not profile_id:
                            result = {'success': False, 'message': '未找到该加密配置'}
                        else:
                            mt_api.write_sentence(['/interface/wireless/security-profiles/remove', f'=.id={profile_id}'])
                            response = mt_api.read_sentence(timeout=10)
                            
                            if '!trap' in response:
                                error_msg = ''
                                for line in response:
                                    if line.startswith('=message='):
                                        error_msg = line[9:]
                                result = {'success': False, 'message': error_msg or '删除失败'}
                            else:
                                result = {'success': True, 'message': '删除成功'}
                        
                        mt_api.close()
                except Exception as e:
                    result = {'success': False, 'message': f'删除失败: {str(e)}'}
                    print(f"删除加密配置错误: {ip} - {str(e)}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.send_error(400, str(e))

    def handle_security_profile_set_mode(self, post_data):
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip')
            username = data.get('username')
            password = data.get('password', '')
            name = data.get('name', '')
            mode = data.get('mode', 'dynamic-keys')

            if not ip or not name:
                result = {'success': False, 'message': '缺少必要参数'}
            else:
                try:
                    from mikrotik_api import MikroTikAPI
                    mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    
                    if not success:
                        result = {'success': False, 'message': f'连接失败: {message}'}
                    else:
                        mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={name}'])
                        
                        profile_id = None
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
                                for line in response:
                                    if line.startswith('=.id='):
                                        profile_id = line[5:]
                        
                        if not profile_id:
                            result = {'success': False, 'message': '未找到该加密配置'}
                        else:
                            mt_api.write_sentence(['/interface/wireless/security-profiles/set', f'=.id={profile_id}', f'=mode={mode}'])
                            response = mt_api.read_sentence(timeout=10)
                            
                            if '!trap' in response:
                                error_msg = ''
                                for line in response:
                                    if line.startswith('=message='):
                                        error_msg = line[9:]
                                result = {'success': False, 'message': error_msg or '设置失败'}
                            else:
                                result = {'success': True, 'message': '设置成功'}
                        
                        mt_api.close()
                except Exception as e:
                    result = {'success': False, 'message': f'设置失败: {str(e)}'}
                    print(f"设置加密配置模式错误: {ip} - {str(e)}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.send_error(400, str(e))

    def handle_security_profile_edit(self, post_data):
        try:
            data = json.loads(post_data.decode('utf-8'))
            ip = data.get('ip')
            username = data.get('username')
            password = data.get('password', '')
            original_name = data.get('originalName', '')
            name = data.get('name', '')
            auth_types = data.get('authTypes', '')
            unicast_ciphers = data.get('unicastCiphers', '')
            group_ciphers = data.get('groupCiphers', '')
            wpa_key = data.get('wpaKey', '')
            wpa2_key = data.get('wpa2Key', '')

            if not ip or not original_name:
                result = {'success': False, 'message': '缺少必要参数'}
            else:
                try:
                    from mikrotik_api import MikroTikAPI
                    mt_api = MikroTikAPI(ip, username, password, port=8728, use_ssl=False)
                    success, message = mt_api.login()
                    
                    if not success:
                        result = {'success': False, 'message': f'连接失败: {message}'}
                    else:
                        mt_api.write_sentence(['/interface/wireless/security-profiles/print', f'?name={original_name}'])
                        
                        profile_id = None
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
                                for line in response:
                                    if line.startswith('=.id='):
                                        profile_id = line[5:]
                        
                        if not profile_id:
                            result = {'success': False, 'message': '未找到该加密配置'}
                        else:
                            cmd = ['/interface/wireless/security-profiles/set', f'=.id={profile_id}']
                            if name:
                                cmd.append(f'=name={name}')
                            if auth_types:
                                cmd.append(f'=authentication-types={auth_types}')
                            if unicast_ciphers:
                                cmd.append(f'=unicast-ciphers={unicast_ciphers}')
                            if group_ciphers:
                                cmd.append(f'=group-ciphers={group_ciphers}')
                            if wpa_key:
                                cmd.append(f'=wpa-pre-shared-key={wpa_key}')
                            if wpa2_key:
                                cmd.append(f'=wpa2-pre-shared-key={wpa2_key}')
                            
                            mt_api.write_sentence(cmd)
                            response = mt_api.read_sentence(timeout=10)
                            
                            if '!trap' in response:
                                error_msg = ''
                                for line in response:
                                    if line.startswith('=message='):
                                        error_msg = line[9:]
                                result = {'success': False, 'message': error_msg or '修改失败'}
                            else:
                                result = {'success': True, 'message': '修改成功'}
                        
                        mt_api.close()
                except Exception as e:
                    result = {'success': False, 'message': f'修改失败: {str(e)}'}
                    print(f"修改加密配置错误: {ip} - {str(e)}")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        except Exception as e:
            self.send_error(400, str(e))

    def handle_slsc_tools(self, post_data):
        """启动 WinBox (SLSCtools.exe) 并传递 MAC 地址到 Connect To 栏"""
        import ctypes
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            mac = data.get('mac', '')
            
            print(f"收到启动 SLSCtools 请求: mac={mac}")
            
            if not mac:
                result = {'status': 'error', 'message': '请提供 MAC 地址'}
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                slsc_path = os.path.join(base_dir, 'SLSCtools.exe')
                
                print(f"程序路径: {slsc_path}, 存在: {os.path.exists(slsc_path)}")
                
                if not os.path.exists(slsc_path):
                    result = {'status': 'error', 'message': f'程序不存在: {slsc_path}'}
                else:
                    ret = ctypes.windll.shell32.ShellExecuteW(
                        None, "open", slsc_path, mac, os.path.dirname(slsc_path), 1
                    )
                    
                    if ret <= 32:
                        print(f"ShellExecuteW 返回错误码: {ret}")
                        result = {'status': 'error', 'message': f'启动失败，错误码: {ret}'}
                    else:
                        print(f"已启动 WinBox，MAC 地址已传递到 Connect To 栏: {mac}")
                        result = {'status': 'success', 'message': '已启动', 'mac': mac}
        except Exception as e:
            print(f"启动 SLSCtools 失败: {e}")
            result = {'status': 'error', 'message': f'启动失败: {e}'}
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

    def handle_slsc_tools_close(self):
        """关闭 SLSCtools.exe 进程"""
        import subprocess
        
        result = {'status': 'success', 'message': 'SLSCtools 已关闭'}
        
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'SLSCtools.exe'], 
                          capture_output=True, timeout=5)
            print("已关闭 SLSCtools.exe")
        except subprocess.TimeoutExpired:
            result = {'status': 'error', 'message': '关闭超时'}
        except Exception as e:
            print(f"关闭 SLSCtools.exe 时出错: {e}")
            result = {'status': 'error', 'message': f'关闭失败: {e}'}
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))

def run_api_server(port=32995, mndp_core=None):
    APIHandler.mndp_core = mndp_core
    server = ThreadingHTTPServer(('0.0.0.0', port), APIHandler)
    print(f"服务已启动 http://0.0.0.0:{port}")
    print(f"前端网页地址: http://localhost:{port}")
    server.serve_forever()

if __name__ == '__main__':
    if platform.system() == "Windows":
        import ctypes
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin():
                print("警告：建议以管理员权限运行以获得最佳效果")
        except:
            pass
    
    mndp_core = MNDPCore()
    
    if mndp_core.start_listener():
        mndp_core.send_discovery_packet()
        
        # 启动 WebSocket 服务器（在新线程中）
        try:
            from websocket_server import run_websocket_server
            import threading
            ws_thread = threading.Thread(target=run_websocket_server, args=(32996,), daemon=True)
            ws_thread.start()
            print("WebSocket 服务器已启动在 ws://0.0.0.0:32996")
        except Exception as e:
            print(f"WebSocket 服务器启动失败: {e}")
        
        try:
            run_api_server(32995, mndp_core)
        except KeyboardInterrupt:
            print("\n正在关闭服务...")
            mndp_core.stop_listener()
    else:
        print("无法启动MNDP监听服务")
        sys.exit(1)
