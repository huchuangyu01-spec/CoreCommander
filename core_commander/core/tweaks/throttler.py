# -*- coding: utf-8 -*-
import os
import subprocess
import psutil
import threading
import time
import queue
import socket
import struct
import ctypes
import pythoncom
import win32com.client
import win32service
import win32serviceutil
from core_commander.utils.logger import logger
from ctypes import wintypes

try:
    import pydivert
    HAS_PYDIVERT = True
except ImportError:
    HAS_PYDIVERT = False

PROXY_PROCESS_NAMES = {
    "clash.exe", "clash-meta.exe", "verge-mihomo.exe", "mihomo.exe", 
    "v2ray.exe", "xray.exe", "socks5.exe", "trojan.exe", "shadowsocks.exe",
    "clash-win64.exe", "clash-meta-win64.exe", "clash-verge.exe", "sing-box.exe",
    "nekobox.exe", "v2rayn.exe"
}

# Well-known public DNS resolver IPs  ?must never be treated as game server IPs.
# Adding them to remote_ips causes WinDivert to intercept DNS traffic and
# _kill_proxy_game_connections to RST DoT/DoH connections, breaking internet access.
KNOWN_DNS_IPS = frozenset({
    # Google Public DNS
    "8.8.8.8", "8.8.4.4",
    # Cloudflare
    "1.1.1.1", "1.0.0.1",
    # Alibaba DNS
    "223.5.5.5", "223.6.6.6",
    # Tencent DNS (DNSPod)
    "119.29.29.29", "182.254.116.116",
    # DNSPod new addresses
    "1.12.12.12", "120.53.53.53",
    # Baidu DNS
    "180.76.76.76",
    # OpenDNS
    "208.67.222.222", "208.67.220.220",
    # Quad9
    "9.9.9.9", "149.112.112.112",
    # AdGuard
    "94.140.14.14", "94.140.15.15",
    # China Telecom / ISP resolvers
    "114.114.114.114", "114.114.115.115",
})

# Ports that belong to system/infrastructure services and must never be treated
# as game ports in remote_ports or used for TCP-RST kills.
SYSTEM_RESERVED_PORTS = frozenset({
    
    
    
    123,   # NTP
    80,    # HTTP
    443,   # HTTPS
    22,    # SSH
    3389,  # RDP
    8080,  # HTTP alt
    9090,  # Clash dashboard / common proxy
    9097,  # Clash dashboard alt
    7890,  # Clash HTTPS proxy
    7897,  # Clash HTTPS proxy alt
    7892,  # Clash SOCKS5
    1080,  # SOCKS5
    465,   # SMTPS
    587,   # SMTP submission
    993,   # IMAPS
    995,   # POP3S
})

# Executables inside the game folder that should NOT be treated as game processes.
# These are crash reporters, launchers, and other utilities whose network connections
# (e.g., crash telemetry to Unity/Microsoft servers) must not be blocked or throttled.
EXCLUDED_GAME_DIR_EXES = frozenset({
    "unitycrashandler64.exe",
    "unitycrashandler.exe",
    "crashreporter.exe",
    "crashsender.exe",
    "crashpad_handler.exe",
    "dxsetup.exe",
    "vcredist_x64.exe",
    "vcredist_x86.exe",
    "ue4crashreporter.exe",
    "unrealcefsubprocess.exe",
    "unrealenginecrashcontext.exe",
})

def detect_tun_ips():
    tun_ips = []
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            iface_lower = iface.lower()
            is_tun = any(k in iface_lower for k in ["wintun", "clash", "sing-box", "tun", "tap", "vpn", "mihomo"])
            for addr in addrs:
                if addr.family == 2:  # AF_INET
                    ip = addr.address
                    if ip not in ("127.0.0.1", "0.0.0.0"):
                        if is_tun or ip.startswith("198.18."):
                            if ip not in tun_ips:
                                tun_ips.append(ip)
    except Exception:
        pass
    return tun_ips

def is_local_or_lan_ip(ip_str):
    if not ip_str:
        return True
    if ip_str in ('127.0.0.1', '::1', 'localhost', '0.0.0.0'):
        return True
    try:
        parts = [int(p) for p in ip_str.split('.')]
        if len(parts) == 4:
            if parts[0] == 10:
                return True
            if parts[0] == 172 and (16 <= parts[1] <= 31):
                return True
            if parts[0] == 192 and parts[1] == 168:
                return True
            if parts[0] == 169 and parts[1] == 254:
                return True
    except Exception:
        pass
    return False

class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD)
    ]

def close_tcp_connection(local_ip, local_port, remote_ip, remote_port):
    try:
        import socket
        import struct
        
        l_addr = struct.unpack("I", socket.inet_aton(local_ip))[0]
        r_addr = struct.unpack("I", socket.inet_aton(remote_ip))[0]
        
        l_port = socket.htons(local_port)
        r_port = socket.htons(remote_port)
        
        row = MIB_TCPROW()
        row.dwState = 12  # MIB_TCP_STATE_DELETE_TCB
        row.dwLocalAddr = l_addr
        row.dwLocalPort = l_port
        row.dwRemoteAddr = r_addr
        row.dwRemotePort = r_port
        
        iphlpapi = ctypes.windll.iphlpapi
        res = iphlpapi.SetTcpEntry(ctypes.byref(row))
        if res == 0:
            logger.info(f"Successfully closed TCP connection: {local_ip}:{local_port} -> {remote_ip}:{remote_port}")
            return True
        else:
            logger.warning(f"Failed to close TCP connection, error code: {res}")
            return False
    except Exception as e:
        logger.error(f"Error in close_tcp_connection: {e}")
        return False

def get_service_start_type(service_name):
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_QUERY_CONFIG)
            try:
                config = win32service.QueryServiceConfig(hs)
                return config[1] # Start type
            finally:
                win32service.CloseServiceHandle(hs)
        finally:
            win32service.CloseServiceHandle(hscm)
    except Exception:
        return None

def set_service_start_type(service_name, start_type):
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            hs = win32service.OpenService(hscm, service_name, win32service.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig(
                    hs,
                    win32service.SERVICE_NO_CHANGE,
                    start_type,
                    win32service.SERVICE_NO_CHANGE,
                    None, None, 0, None, None, None, None
                )
            finally:
                win32service.CloseServiceHandle(hs)
        finally:
            win32service.CloseServiceHandle(hscm)
    except Exception as e:
        logger.error(f"Failed to change service {service_name} start type: {e}")

class WinDivertSenderThread(threading.Thread):
    """
    Helper thread for executing delayed packet sending to implement high-precision latency delay injection.
    Allows non-blocking packet receiving in the main WinDivert loop.
    """
    def __init__(self, wd, stop_event):
        super().__init__()
        self.wd = wd
        self.stop_event = stop_event
        self.queue = []
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.daemon = True
        self._burst_counter = 0

    def push(self, packet, delay_sec):
        now = time.perf_counter()
        release_time = now + delay_sec
        with self.lock:
            self.queue.append((packet, release_time))
            # Sort by release time ascending
            self.queue.sort(key=lambda x: x[1])
            self.cond.notify_all()

    def clear(self, send_remaining=True):
        with self.lock:
            if not send_remaining:
                self.queue.clear()
            else:
                # Reset all release times to now to flush them via the sender thread loop
                # This prevents blocking the intercept loop and naturally smooths the TX burst
                now = time.perf_counter()
                self.queue = [(p, now) for p, _ in self.queue]
            self.cond.notify_all()

    def run(self):
        while not self.stop_event.is_set():
            with self.lock:
                if not self.queue:
                    self.cond.wait(timeout=0.005)
                    self._burst_counter = 0
                    continue
                
                now = time.perf_counter()
                packet, release_time = self.queue[0]
                sleep_time = 0
                if now >= release_time:
                    self.queue.pop(0)
                else:
                    sleep_time = release_time - now
                    if sleep_time > 0.002:
                        self.cond.wait(timeout=sleep_time - 0.002)
                        self._burst_counter = 0
                        continue
                    else:
                        self.queue.pop(0)

            if sleep_time > 0:
                while time.perf_counter() < release_time:
                    pass
            # Send packet outside lock to avoid blocking push()
            try:
                self.wd.send(packet)
                self._burst_counter += 1
                # Yield slightly to OS to prevent WSAENOBUFS on massive queue flushes
                # But do NOT sleep on every single packet, otherwise the 'sync' burst gets horribly delayed!
                if self._burst_counter > 30:
                    time.sleep(0.0001)
                    self._burst_counter = 0
            except Exception:
                pass

class WinDivertThrottlerThread(threading.Thread):
    def __init__(self, wd, stop_event):
        super().__init__()
        self.wd = wd
        self.stop_event = stop_event
        self.daemon = True
        
        # Start helper sender thread for latency delay injection
        self.sender_thread = WinDivertSenderThread(wd, stop_event)
        self.sender_thread.start()

    def run(self):
        try:
            logger.info("WinDivertThrottlerThread started.")
            
            # Token bucket variables (initialized)
            last_time = time.time()
            tokens = 0.0
            
            _debug_logged_packets = 0
            _debug_logged_drop = False

            while not self.stop_event.is_set():
                try:
                    packet = self.wd.recv()
                except Exception:
                    break
                
                if _debug_logged_packets < 100:
                    # logger.info(f"[DEBUG] WinDivert intercepted packet: Dir={packet.direction}, Protocol={packet.protocol}, Size={len(packet.payload)}")
                    _debug_logged_packets += 1

                # Atomic lookup of states from NetworkThrottlerService
                is_throttling = NetworkThrottlerService._is_throttling
                limit_type = NetworkThrottlerService._current_limit_type
                upload_rate_value = NetworkThrottlerService._current_upload_rate_value
                download_rate_value = NetworkThrottlerService._current_download_rate_value
                unit = NetworkThrottlerService._current_unit
                direction_mode = getattr(NetworkThrottlerService, '_current_direction', 'both')

                if not is_throttling:
                    # Pass-through mode: forward instantly
                    # Flush any delayed packets immediately to avoid any post-release delay
                    self.sender_thread.clear(send_remaining=True)
                    try:
                        self.wd.send(packet)
                    except Exception:
                        pass
                    last_time = time.perf_counter()  # Keep last_time updated to avoid giant elapsed values on activation
                    tokens = 0.0             # Reset tokens for instant activation state
                    self.pulse_start_time = None # Reset pulse alignment
                    continue
                    
                # Evaluate direction bypass
                if direction_mode == "outbound" and packet.is_inbound:
                    # We only throttle outbound, but packet is inbound. Pass it immediately.
                    try:
                        self.wd.send(packet)
                    except Exception:
                        pass
                    continue
                    
                if direction_mode == "inbound" and packet.is_outbound:
                    # We only throttle inbound, but packet is outbound. Pass it immediately.
                    try:
                        self.wd.send(packet)
                    except Exception:
                        pass
                    continue



                if limit_type == "firewall":
                    if not _debug_logged_drop:
                        logger.info(f"[DEBUG] WinDivert explicitly DROPPED a packet in Blocker mode! Dir={packet.direction}")
                        _debug_logged_drop = True
                    # Blocker mode: drop packet instantly
                    continue

                elif limit_type == "qos":
                    if unit == "ms":
                        # Delay injection mode: pure consistent delay for every packet
                        delay_sec = float(download_rate_value) / 1000.0
                        self.sender_thread.push(packet, delay_sec)
                        continue
                    else:
                        # QoS mode: apply rate limiting (token bucket)
                        val = float(download_rate_value)
                        if unit == "MB/s":
                            rate_kbs = val * 1024.0
                        elif unit == "Mbps":
                            rate_kbs = val * 125.0
                        else:
                            rate_kbs = val
                            
                        bytes_per_sec = rate_kbs * 1024.0
                        if bytes_per_sec <= 0:
                            bytes_per_sec = 1024.0
                            
                        bucket_capacity = max(bytes_per_sec, 1500.0)

                        now = time.perf_counter()
                        elapsed = now - last_time
                        last_time = now

                        tokens += elapsed * bytes_per_sec
                        if tokens > bucket_capacity:
                            tokens = bucket_capacity

                        try:
                            packet_len = len(packet.raw) if hasattr(packet, 'raw') and packet.raw else 1500
                        except Exception:
                            packet_len = 1500
                            
                        if tokens >= packet_len:
                            tokens -= packet_len
                            # If there are items in the queue (shouldn't happen often if tokens > 0), flush them
                            self.sender_thread.clear(send_remaining=True)
                            try:
                                self.wd.send(packet)
                            except Exception:
                                pass
                        else:
                            # Shape traffic instead of policing (dropping). 
                            # Calculate how long we need to wait for enough tokens
                            required_tokens = packet_len - tokens
                            delay = required_tokens / bytes_per_sec
                            tokens -= packet_len
                            
                            # Max queue delay 2 seconds to avoid infinite latency or OOM
                            if delay > 2.0:
                                tokens = - (2.0 * bytes_per_sec) # Cap the debt
                            else:
                                self.sender_thread.push(packet, delay)
        except Exception as e:
            logger.error(f"WinDivertThrottlerThread exception: {e}")
        finally:
            if self.sender_thread:
                self.sender_thread.clear(send_remaining=True)
            if self.wd:
                try:
                    self.wd.close()
                except Exception:
                    pass
            logger.info("WinDivertThrottlerThread stopped.")

class QosPulsingThread(threading.Thread):
    """
    Background thread that simulates network rate limiting (throttling/lag)
    by pulsing (enabling/disabling) only the UDP rules with high precision.
    TCP rules are bypassed to maintain anti-cheat connection and session heartbeats.
    """
    def __init__(self, upload_rate, download_rate, unit, stop_event):
        super().__init__()
        self.upload_rate = upload_rate
        self.download_rate = download_rate
        self.unit = unit
        self.stop_event = stop_event
        self.daemon = True
        self._rate_lock = threading.Lock()

    def update_rate(self, upload_rate, download_rate, unit):
        with self._rate_lock:
            self.upload_rate = upload_rate
            self.download_rate = download_rate
            self.unit = unit

    def run(self):
        pythoncom.CoInitializeEx(0)
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            logger.info("QosPulsingThread: Starting pulsing loop using EnableRuleGroup on TCP and UDP rules.")

            while not self.stop_event.is_set():
                with self._rate_lock:
                    # Use min rate for overall firewall toggle pulsing to be conservative
                    val = float(self.download_rate)
                    unit = self.unit

                if unit == "MB/s":
                    rate_kbs = val * 1024.0
                elif unit == "Mbps":
                    rate_kbs = val * 125.0
                else:
                    rate_kbs = val

                ratio_allow = rate_kbs / (rate_kbs + 50.0)
                ratio_allow = max(0.02, min(0.95, ratio_allow))

                T_cycle = 0.150  # 150ms cycle
                T_allow = T_cycle * ratio_allow
                T_block = T_cycle * (1.0 - ratio_allow)

                # Block state - enable both TCP and UDP block groups
                t0 = time.time()
                try:
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", True)
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", True)
                except Exception as e:
                    logger.debug(f"EnableRuleGroup True failed: {e}")
                dt = time.time() - t0
                sleep_block = max(0.001, T_block - dt)
                if self.stop_event.wait(sleep_block):
                    break

                # Allow state - disable block groups
                t1 = time.time()
                try:
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", False)
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", False)
                except Exception as e:
                    logger.debug(f"EnableRuleGroup False failed: {e}")
                dt = time.time() - t1
                sleep_allow = max(0.001, T_allow - dt)
                if self.stop_event.wait(sleep_allow):
                    break

        except Exception as e:
            logger.error(f"QosPulsingThread exception: {e}")
        finally:
            # Clean up: ensure rules are left disabled (allow traffic)
            try:
                policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", False)
                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", False)
            except Exception as e:
                logger.debug(f"EnableRuleGroup False cleanup failed: {e}")
            pythoncom.CoUninitialize()
            logger.info("QosPulsingThread stopped, firewall rules disabled.")

class ThrottlerBackgroundWorker(threading.Thread):
    """
    Dedicated background worker thread that processes firewall COM tasks sequentially
    via a thread-safe Queue, eliminating UI main thread lockups.
    """
    def __init__(self):
        super().__init__()
        self.queue = queue.Queue()
        self.daemon = True

    def run(self):
        pythoncom.CoInitializeEx(0)
        try:
            while True:
                task = self.queue.get()
                if task is None:
                    break
                func, args, kwargs = task
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"ThrottlerBackgroundWorker: Task execution error: {e}")
                self.queue.task_done()
        finally:
            pythoncom.CoUninitialize()
            logger.info("ThrottlerBackgroundWorker thread exited.")

    def submit(self, func, *args, **kwargs):
        self.queue.put((func, args, kwargs))

    def join_queue(self):
        self.queue.join()

class NetworkThrottlerService:
    """
    Manages process-based network speed control and blocking.
    Supports two modes:
    1. 'qos': Single-rule high-precision pulsing firewall (delay spiker targeting UDP).
    2. 'firewall': Network blocker with firewall service management & game accelerator mapping.
    """
    _ps_process = None
    _ps_lock = threading.Lock()
    _enabled_profiles = []  # Track firewall profiles we temporarily enabled
    _original_service_status = None # "RUNNING", "STOPPED", or None
    _original_service_start_type = None # 2, 3, 4, or None
    _pulsing_thread = None
    _pulsing_stop_event = None
    _suspended_pids = []  # Track suspended process PIDs during card blocker

    _rules_installed = False

    _cache_lock = threading.RLock()
    _cache_target_key = None
    _cache_paths = set()
    _cache_pids = []
    _cache_remote_ports = set()
    _cache_local_ports = set()
    _cache_local_udp_ports = set()
    _cache_proxy_ports = set() # Store detected Clash/v2ray proxy ports
    _cache_remote_ips = set() # Cache detected game server IPs
    _current_limit_type = None # Track whether rate limit / blocker is active ('qos', 'firewall', or None)
    _current_upload_rate_value = 0.0
    _current_download_rate_value = 0.0
    _current_unit = "MBps"
    _is_throttling = False # Atomic memory flag for instant keypress activation
    _wd_thread = None
    _wd_stop_event = None

    @classmethod
    def _restart_windivert(cls):
        if not HAS_PYDIVERT:
            raise RuntimeError("pydivert is not installed")
            
        with cls._cache_lock:
            local_ports = sorted(list(cls._cache_local_ports), reverse=True)
            local_udp_ports = sorted(list(getattr(cls, "_cache_local_udp_ports", [])), reverse=True)
            proxy_ports = sorted(list(getattr(cls, "_cache_proxy_ports", set())))
            remote_ports = sorted(list(cls._cache_remote_ports))
            remote_ips = sorted(list(cls._cache_remote_ips))
            
        # We need at least remote_ips or local_ports or local_udp_ports or remote_ports or proxy_ports to construct a filter
        if not remote_ips and not local_ports and not local_udp_ports and not remote_ports and not proxy_ports:
            return
 
        # Stop existing WinDivert thread
        if getattr(cls, "_wd_stop_event", None) is not None:
            cls._wd_stop_event.set()
            if getattr(cls, "_wd_thread", None) is not None:
                if cls._wd_thread.wd:
                    try:
                        cls._wd_thread.wd.close()
                    except Exception:
                        pass
                cls._wd_thread.join(timeout=1.0)
            cls._wd_stop_event = None
            cls._wd_thread = None
 
        # Build filter string with strict length budget to prevent WinError 87
        # Priority: subnet ranges > individual IPs > remote ports > UDP local > TCP local
        FILTER_MAX_LEN = 2048
        filter_parts = []
        current_len = 0

        def try_add(part):
            nonlocal current_len
            # Each additional part is joined by ' or ' (4 chars)
            needed = len(part) + (4 if filter_parts else 0)
            if current_len + needed <= FILTER_MAX_LEN:
                filter_parts.append(part)
                current_len += needed
                return True
            return False

        # --- Priority 1: Local UDP ports (game's ephemeral real-time data ports) ---
        def get_ranges(ports_set, cap=8):
            valid_ports = sorted([p for p in ports_set if p >= 1024], reverse=True)[:cap * 3]
            valid_ports = sorted(valid_ports)
            if not valid_ports:
                return []
            ranges = []
            start = valid_ports[0]
            prev = valid_ports[0]
            for p in valid_ports[1:]:
                if p - prev <= 15:
                    prev = p
                else:
                    ranges.append((start, prev))
                    start = p
                    prev = p
            ranges.append((start, prev))
            return ranges[:cap]

        udp_ranges = get_ranges(local_udp_ports, cap=6)
        tcp_ranges = get_ranges(local_ports, cap=6)

        for start_p, end_p in udp_ranges:
            if start_p == end_p:
                part = f"(udp.SrcPort == {start_p} or udp.DstPort == {start_p})"
            else:
                part = f"((udp.SrcPort >= {start_p} and udp.SrcPort <= {end_p}) or (udp.DstPort >= {start_p} and udp.DstPort <= {end_p}))"
            if not try_add(part):
                break

        # --- Priority 2: Local TCP ports ---
        for start_p, end_p in tcp_ranges:
            if start_p == end_p:
                part = f"(tcp.SrcPort == {start_p} or tcp.DstPort == {start_p})"
            else:
                part = f"((tcp.SrcPort >= {start_p} and tcp.SrcPort <= {end_p}) or (tcp.DstPort >= {start_p} and tcp.DstPort <= {end_p}))"
            if not try_add(part):
                break

        # --- Priority 2.5: Loopback Proxy Ports (Crucial for game accelerators / Clash) ---
        for p in proxy_ports[:5]:
            part = f"((tcp.SrcPort == {p} or tcp.DstPort == {p} or udp.SrcPort == {p} or udp.DstPort == {p}) and (ip.SrcAddr == 127.0.0.1 or ip.DstAddr == 127.0.0.1 or ipv6.SrcAddr == ::1 or ipv6.DstAddr == ::1))"
            if not try_add(part):
                break

        # --- Priority 3: Exact IP matching ---
        # NOTE: Subnet range expressions (>= / <=) are intentionally NOT used here.
        # With Clash TUN mode, a too-broad range filter captures TUN-routed packets
        # and WinDivert re-injection bypasses Clash routing, breaking the proxy entirely.
        ipv4_ips = [ip for ip in remote_ips if ":" not in ip]
        ipv6_ips = [ip for ip in remote_ips if ":" in ip]
        for ip in ipv4_ips:
            if not try_add(f"(ip.SrcAddr == {ip} or ip.DstAddr == {ip})"):
                break
        for ip in ipv6_ips:
            if not try_add(f"(ipv6.SrcAddr == {ip} or ipv6.DstAddr == {ip})"):
                break

        # --- Priority 4: Remote ports (stable well-known game ports like 28000) ---
        for p in sorted(remote_ports)[:5]:
            if not try_add(f"(tcp.SrcPort == {p} or tcp.DstPort == {p} or udp.SrcPort == {p} or udp.DstPort == {p})"):
                break

        # --- Priority 5: Global UDP Catch-all ---
        # Proxies (WFP/TUN) hide their UDP data tunnels or encapsulate in TCP. 
        # We must intercept all outbound/inbound TCP & UDP to guarantee proxy traffic is caught for Pulse/Block modes.
        try_add("((tcp or udp) and tcp.DstPort != 53 and udp.DstPort != 53 and ip.DstAddr != 127.0.0.1 and ipv6.DstAddr != ::1)")

        if not filter_parts:
            return
 
        filter_str = " or ".join(filter_parts)
        logger.info(f"Trying to open WinDivert with filter string (length {len(filter_str)}): {filter_str}")
        
        # Open WinDivert synchronously to check for errors/WinError 87
        try:
            wd = pydivert.WinDivert(filter_str, priority=1000)
        except Exception:
            wd = pydivert.WinDivert(filter_str)
        wd.open()
        
        cls._wd_stop_event = threading.Event()
        cls._wd_thread = WinDivertThrottlerThread(wd, cls._wd_stop_event)
        cls._wd_thread.start()

    @classmethod
    def _restart_windivert_with_fallback(cls):
        try:
            cls._restart_windivert()
            # If WinDivert successfully started, stop pulsing and disable firewall blocking rule groups to prevent interference
            if cls._pulsing_stop_event is not None:
                cls._pulsing_stop_event.set()
                if cls._pulsing_thread is not None:
                    cls._pulsing_thread.join(timeout=0.5)
                cls._pulsing_thread = None
                cls._pulsing_stop_event = None
                
            pythoncom.CoInitializeEx(0)
            try:
                policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", False)
                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", False)
            except Exception:
                pass
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logger.error(f"Failed to start WinDivert: {e}. Falling back to firewall/pulsing.")
            if cls._is_throttling:
                if cls._current_limit_type == "qos":
                    if cls._pulsing_thread is None:
                        cls._pulsing_stop_event = threading.Event()
                        cls._pulsing_thread = QosPulsingThread(cls._current_upload_rate_value, cls._current_download_rate_value, cls._current_unit, cls._pulsing_stop_event)
                        cls._pulsing_thread.start()
                else:
                    # Card Blocker fallback: enable firewall blocking rule groups
                    pythoncom.CoInitializeEx(0)
                    try:
                        policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                        policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", True)
                        policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", True)
                    except Exception:
                        pass
                    finally:
                        pythoncom.CoUninitialize()


    ACCELERATOR_NAMES = {
        "uu.exe", "uugame.exe", "uu_game_assistant.exe", 
        "xunyou.exe", "xunyousub.exe", "xunyou3.exe",
        "leishen.exe", "leishenelong.exe", "lsaccess.exe",
        "qingchuan.exe", "bilibililink.exe", "tgp_daemon.exe"
    }

    _worker = ThrottlerBackgroundWorker()
    _worker.start()

    @classmethod
    def join_worker(cls):
        cls._worker.join_queue()

    @classmethod
    def setup_qos_nla_bypass(cls) -> bool:
        # Compatibility no-op
        return False

    @classmethod
    def ensure_pacer_enabled(cls):
        pass

    @classmethod
    def is_pacer_enabled_any(cls) -> bool:
        return True

    @classmethod
    def _get_ps_session(cls):
        with cls._ps_lock:
            if cls._ps_process is None or cls._ps_process.poll() is not None:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                cls._ps_process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startupinfo,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    text=True
                )
            return cls._ps_process

    @classmethod
    def _run_ps_cmd_instant(cls, cmd_str: str):
        try:
            ps = cls._get_ps_session()
            ps.stdin.write(cmd_str + "\n")
            ps.stdin.flush()
        except Exception as e:
            logger.error(f"NetworkThrottlerService: Failed to write to PS process: {str(e)}")
            with cls._ps_lock:
                cls._ps_process = None

    @classmethod
    def _enable_loopback_compatibility(cls):
        path = r"SYSTEM\CurrentControlSet\Services\BFE\Parameters\Policy"
        try:
            import winreg
            need_restart = False
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key:
                    val, _ = winreg.QueryValueEx(key, "LoopbackBackwardsCompatibility")
                    if val != 1:
                        need_restart = True
            except FileNotFoundError:
                need_restart = True

            if need_restart:
                logger.info("Enabling LoopbackBackwardsCompatibility in registry...")
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "LoopbackBackwardsCompatibility", 0, winreg.REG_DWORD, 1)

                logger.info("Restarting Base Filtering Engine (BFE) service to apply loopback compatibility...")
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Restart-Service BFE -Force"],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                logger.info("BFE service restarted successfully.")
        except Exception as e:
            logger.error(f"Failed to enable loopback compatibility: {e}")

    @classmethod
    def _ensure_firewall_service_started(cls):
        try:
            status = win32serviceutil.QueryServiceStatus("MpsSvc")[1]
            if status == win32service.SERVICE_RUNNING:
                cls._original_service_status = "RUNNING"
                return True
            
            cls._original_service_status = "STOPPED"
            cls._original_service_start_type = get_service_start_type("MpsSvc")
            
            logger.info(f"MpsSvc is not running. Original start type: {cls._original_service_start_type}. Activating service...")
            if cls._original_service_start_type == 4:
                set_service_start_type("MpsSvc", 3) # Set to Manual
                
            win32serviceutil.StartService("MpsSvc")
            for _ in range(30):
                if win32serviceutil.QueryServiceStatus("MpsSvc")[1] == win32service.SERVICE_RUNNING:
                    logger.info("MpsSvc successfully started.")
                    return True
                time.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to ensure firewall service started: {e}")
        return False

    @classmethod
    def _restore_firewall_service(cls):
        try:
            if cls._original_service_status == "STOPPED":
                logger.info("Restoring MpsSvc to STOPPED status...")
                try:
                    win32serviceutil.StopService("MpsSvc")
                    for _ in range(30):
                        if win32serviceutil.QueryServiceStatus("MpsSvc")[1] == win32service.SERVICE_STOPPED:
                            break
                        time.sleep(0.1)
                except Exception:
                    pass
                
                if cls._original_service_start_type == 4:
                    logger.info("Restoring MpsSvc start type to DISABLED...")
                    set_service_start_type("MpsSvc", 4)
        except Exception as e:
            logger.error(f"Failed to restore firewall service: {e}")
        finally:
            cls._original_service_status = None
            cls._original_service_start_type = None

    @classmethod
    def _scan_accelerators(cls):
        acc_paths = set()
        acc_pids = []
        for p in psutil.process_iter(['name', 'exe', 'pid']):
            try:
                name = p.info['name']
                if name and name.lower() in cls.ACCELERATOR_NAMES:
                    exe = p.info['exe']
                    if exe and os.path.exists(exe):
                        acc_paths.add(exe)
                        acc_pids.append(p.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return list(acc_paths), acc_pids


    @classmethod
    def _dynamic_port_scanner_loop(cls):
        import time
        while not (cls._dynamic_scanner_stop_event and cls._dynamic_scanner_stop_event.is_set()):
            if cls._cache_target_key:
                pid, name = cls._cache_target_key
                try:
                    cls.cache_target_info(pid, name, force_rescan=True)
                except Exception as e:
                    logger.debug(f"Background port scanner error: {e}")
            if cls._dynamic_scanner_stop_event:
                cls._dynamic_scanner_stop_event.wait(5.0)
            else:
                time.sleep(5.0)

    @classmethod
    def _start_dynamic_scanner(cls):
        if cls._dynamic_scanner_thread is None or not cls._dynamic_scanner_thread.is_alive():
            cls._dynamic_scanner_stop_event = threading.Event()
            cls._dynamic_scanner_thread = threading.Thread(target=cls._dynamic_port_scanner_loop, daemon=True)
            cls._dynamic_scanner_thread.start()
            logger.info("Started dynamic UDP port background scanner.")

    @classmethod
    def _stop_dynamic_scanner(cls):
        if cls._dynamic_scanner_thread and cls._dynamic_scanner_stop_event:
            cls._dynamic_scanner_stop_event.set()
            cls._dynamic_scanner_thread = None
            logger.info("Stopped dynamic UDP port background scanner.")

    @classmethod
    def cache_target_info(cls, target_pid: int, target_name: str, force_rescan: bool = False):
        if not target_pid and not target_name:
            return

        with cls._cache_lock:
            resolved_pid = target_pid
            
            if not resolved_pid or not psutil.pid_exists(resolved_pid):
                running_cached_pids = []
                cached_paths = list(cls._cache_paths)
                if cached_paths:
                    for p in psutil.process_iter(['exe', 'pid']):
                        try:
                            exe = p.info['exe']
                            if exe and exe in cached_paths:
                                running_cached_pids.append(p.info['pid'])
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                if running_cached_pids:
                    resolved_pid = running_cached_pids[0]

            if not resolved_pid or not psutil.pid_exists(resolved_pid):
                if target_name:
                    target_lower = target_name.lower()
                    target_base = target_lower[:-4] if target_lower.endswith('.exe') else target_lower
                    for p in psutil.process_iter(['name', 'pid']):
                        try:
                            p_name = p.info['name']
                            if p_name:
                                p_lower = p_name.lower()
                                p_base = p_lower[:-4] if p_lower.endswith('.exe') else p_lower
                                if target_base in p_base or p_base in target_base:
                                    resolved_pid = p.info['pid']
                                    break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

            if not resolved_pid:
                return

            target_key = (resolved_pid, target_name)
            if force_rescan or cls._cache_target_key != target_key or not cls._cache_paths:
                paths = set()
                pids = [resolved_pid]

                from core_commander.utils.process import get_process_path_by_pid

                try:
                    p = psutil.Process(resolved_pid)
                    exe_path = get_process_path_by_pid(resolved_pid)
                    if exe_path:
                        paths.add(exe_path)
                        # Grab all processes running inside the game directory (such as NeacClient.exe, CCMini.exe, etc.)
                        # but exclude crash handlers and system utilities to avoid blocking their network traffic.
                        exe_dir = os.path.dirname(exe_path.lower())
                        for p_item in psutil.process_iter(['pid', 'exe', 'name']):
                            try:
                                p_exe = p_item.info['exe']
                                p_name = (p_item.info.get('name') or "").lower()
                                if p_exe:
                                    p_exe_lower = p_exe.lower()
                                    if (p_exe_lower.startswith(exe_dir) or exe_dir in p_exe_lower):
                                        if p_name not in EXCLUDED_GAME_DIR_EXES:
                                            paths.add(p_exe)
                                            pids.append(p_item.info['pid'])
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                continue
                    for child in p.children(recursive=True):
                        try:
                            c_path = get_process_path_by_pid(child.pid)
                            if c_path:
                                paths.add(c_path)
                            pids.append(child.pid)
                        except Exception:
                            pass
                except Exception:
                    pass

                if target_name:
                    target_lower = target_name.lower()
                    target_base = target_lower[:-4] if target_lower.endswith('.exe') else target_lower
                    for p in psutil.process_iter(['name', 'pid']):
                        try:
                            p_name = p.info['name']
                            if p_name:
                                p_lower = p_name.lower()
                                p_base = p_lower[:-4] if p_lower.endswith('.exe') else p_lower
                                if target_base in p_base or p_base in target_base:
                                    pid = p.info['pid']
                                    exe = get_process_path_by_pid(pid)
                                    if exe:
                                        paths.add(exe)
                                        pids.append(pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

                if cls._cache_target_key != target_key:
                    cls._cache_remote_ports.clear()
                    cls._cache_local_ports.clear()
                    cls._cache_local_udp_ports.clear()
                    cls._cache_proxy_ports.clear()
                    cls._cache_remote_ips.clear()
                if cls._cache_paths != paths:
                    cls._rules_installed = False
                    changed = True
                cls._cache_target_key = target_key
                cls._cache_paths = paths
                cls._cache_pids = list(set(pids))

            # Refresh active UDP/TCP ports of game
            pids = cls._cache_pids
            remote_ports = set()
            local_ports = set()
            local_udp_ports = set()
            proxy_ports = set()
            remote_ips = set()
            try:
                conns = []
                for pid in pids:
                    try:
                        p = psutil.Process(pid)
                        for conn in p.connections(kind='inet'):
                            class ConnWrapper:
                                def __init__(self, c, pid_val):
                                    self._c = c
                                    self.pid = pid_val
                                def __getattr__(self, name):
                                    return getattr(self._c, name)
                            conns.append(ConnWrapper(conn, pid))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                    except Exception:
                        continue
                if not conns:
                    conns = psutil.net_connections(kind='inet')

                for conn in conns:
                    if conn.pid in pids:
                        if conn.laddr:
                            # Filter out loopback connections to avoid polluting port list
                            is_loopback = False
                            if conn.raddr:
                                r_ip = conn.raddr[0]
                                if r_ip in ('127.0.0.1', '::1', 'localhost'):
                                    is_loopback = True
                            if not is_loopback:
                                port_val = conn.laddr[1]
                                if port_val >= 1024:
                                    if conn.type == socket.SOCK_DGRAM:
                                        local_udp_ports.add(port_val)
                                    else:
                                        local_ports.add(port_val)
                        if conn.raddr:
                            r_ip, r_port = conn.raddr
                            if not is_local_or_lan_ip(r_ip):
                                # Only add to remote_ips if not a known DNS resolver and not a system port.
                                # DNS servers (8.8.8.8, 1.1.1.1, etc.) connecting via DoT (853) or DoH (443)
                                # must never be treated as game server IPs  ?they would break all DNS.
                                if r_port not in SYSTEM_RESERVED_PORTS and r_ip not in KNOWN_DNS_IPS:
                                    remote_ips.add(r_ip)
                                # Only track remote port if it is not a system/infrastructure port
                                if r_port not in SYSTEM_RESERVED_PORTS:
                                    remote_ports.add(r_port)
                            else:
                                # Connection goes to loopback. Detect if the listening port belongs to external process (like Clash proxy)
                                is_game_internal = False
                                for conn_l in psutil.net_connections(kind='tcp'):
                                    if conn_l.laddr and conn_l.laddr[1] == r_port and conn_l.status == 'LISTEN':
                                        if conn_l.pid in pids:
                                            is_game_internal = True
                                            break
                                if not is_game_internal:
                                    proxy_ports.add(r_port)
            except Exception:
                pass

            # Detect active high-port TCP/UDP connections for proxy processes to extract remote game IPs
            try:
                proxy_pids = []
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        name = proc.info['name']
                        if name:
                            lname = name.lower()
                            if lname in PROXY_PROCESS_NAMES or lname in cls.ACCELERATOR_NAMES:
                                proxy_pids.append(proc.info['pid'])
                    except Exception:
                        continue
                
                for conn in psutil.net_connections(kind='inet'):
                    if conn.pid in proxy_pids:
                        if conn.raddr:
                            r_ip, r_port = conn.raddr
                            if not is_local_or_lan_ip(r_ip):
                                if r_port not in SYSTEM_RESERVED_PORTS and r_ip not in KNOWN_DNS_IPS:
                                    remote_ips.add(r_ip)
                        if conn.laddr and conn.type == socket.SOCK_DGRAM:
                            port_val = conn.laddr[1]
                            if port_val >= 1024:
                                local_udp_ports.add(port_val)
            except Exception:
                pass

            changed = False
            # Remote ports: only track the fixed well-known game ports (28000, 20300, etc.)
            # to avoid filter bloat. Keep a cap of 10.
            new_remote_ports = remote_ports - cls._cache_remote_ports
            if new_remote_ports:
                cls._cache_remote_ports.update(new_remote_ports)
                if len(cls._cache_remote_ports) > 10:
                    # Keep only the most frequently useful ones (non-ephemeral, < 30000)
                    stable = {p for p in cls._cache_remote_ports if p < 30000}
                    ephemeral = sorted([p for p in cls._cache_remote_ports if p >= 30000], reverse=True)
                    cls._cache_remote_ports = stable | set(ephemeral[:max(0, 10 - len(stable))])
                changed = True
            # Local ports: accumulate to prevent constant WinDivert restarts, cap at 30.
            if not local_ports.issubset(cls._cache_local_ports):
                cls._cache_local_ports.update(local_ports)
                if len(cls._cache_local_ports) > 30:
                    others = cls._cache_local_ports - local_ports
                    cls._cache_local_ports = local_ports | set(list(others)[:max(0, 30 - len(local_ports))])
                changed = True
            
            if not hasattr(cls, '_cache_local_udp_ports'):
                cls._cache_local_udp_ports = set()
            if not local_udp_ports.issubset(cls._cache_local_udp_ports):
                cls._cache_local_udp_ports.update(local_udp_ports)
                if len(cls._cache_local_udp_ports) > 30:
                    others = cls._cache_local_udp_ports - local_udp_ports
                    cls._cache_local_udp_ports = local_udp_ports | set(list(others)[:max(0, 30 - len(local_udp_ports))])
                changed = True
                
            if proxy_ports and not proxy_ports.issubset(cls._cache_proxy_ports):
                cls._cache_proxy_ports.update(proxy_ports)
                changed = True
                
            # Remote IPs: accumulate up to 30 to prevent constant filter rebuilds
            if not remote_ips.issubset(cls._cache_remote_ips):
                cls._cache_remote_ips.update(remote_ips)
                if len(cls._cache_remote_ips) > 30:
                    others = cls._cache_remote_ips - remote_ips
                    cls._cache_remote_ips = remote_ips | set(list(others)[:max(0, 30 - len(remote_ips))])
                changed = True

            if changed:
                if cls._is_throttling:
                    logger.info("Active game targets/IPs changed, but throttling is active. Deferring WinDivert update.")
                    cls._deferred_wd_restart = True
                else:
                    logger.info("Active game targets/IPs changed. Dynamically updating WinDivert...")
                    cls._restart_windivert()
                
                # If we are actively throttling, we also update the firewall rules and kill proxy connections
                if cls._current_limit_type is not None:
                    is_blocker = (cls._current_limit_type == "firewall")
                    def _conditional_create_rules():
                        with cls._cache_lock:
                            has_cache = bool(cls._cache_local_ports or cls._cache_local_udp_ports or cls._cache_remote_ips)
                        should_enable = is_blocker and cls._is_throttling
                        if not getattr(cls, '_rules_installed', False) or not has_cache:
                            direction = getattr(cls, '_current_direction', 'both')
                            cls._create_rules_internal(enabled=should_enable, direction=direction)
                        else:
                            # Just enable the groups
                            import pythoncom, win32com.client
                            pythoncom.CoInitializeEx(0)
                            try:
                                policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", should_enable)
                                policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", should_enable)
                            except Exception:
                                pass
                            finally:
                                pythoncom.CoUninitialize()

                    cls._worker.submit(_conditional_create_rules)
                    
                    if is_blocker and getattr(cls, '_is_throttling', False):
                        cls._worker.submit(cls._kill_proxy_game_connections, list(cls._cache_remote_ports), list(cls._cache_remote_ips))
    @classmethod
    def pre_create_rules(cls, target_pid: int, target_name: str):
        # Force a synchronous warm up of the cache and rules immediately on game launch detection
        cls._pre_create_rules_internal(target_pid, target_name)

    @classmethod
    def _pre_create_rules_internal(cls, target_pid: int, target_name: str):
        try:
            logger.info("Pre-warming throttler target info and installing rules...")
            cls.cache_target_info(target_pid, target_name, force_rescan=True)
            direction = getattr(cls, '_current_direction', 'both')
            cls._create_rules_internal(enabled=False, direction=direction)
            # Pre-warming WinDivert removed to prevent breaking game login.
            pass
        except Exception as e:
            logger.error(f"Failed to pre-create rules: {e}")

    @classmethod
    def _create_rules_internal(cls, enabled: bool = False, direction: str = "both"):
        """
        Creates isolated TCP and UDP outbound block rules.
        - TCP rules group: CoreCommander_Throttling_Group_TCP
        - UDP rules group: CoreCommander_Throttling_Group_UDP
        """
        with cls._cache_lock:
            paths = list(cls._cache_paths)
            local_ports = list(cls._cache_local_ports)
            local_udp_ports = list(getattr(cls, "_cache_local_udp_ports", []))
            remote_ports = list(cls._cache_remote_ports)
            proxy_ports = list(cls._cache_proxy_ports)
            remote_ips = list(cls._cache_remote_ips)

        # Scan running accelerators
        acc_paths, _ = cls._scan_accelerators()
        all_paths = list(set(paths + acc_paths))

        pythoncom.CoInitializeEx(0)
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            
            # Clean existing rules in both groups
            rules_to_remove = []
            for r in policy.Rules:
                if (r.Name.startswith("CoreCommander_Throttling_") or 
                    r.Grouping in ("CoreCommander_Throttling_Group", "CoreCommander_Throttling_Group_TCP", "CoreCommander_Throttling_Group_UDP")):
                    rules_to_remove.append(r.Name)
            for name in rules_to_remove:
                try:
                    policy.Rules.Remove(name)
                except Exception:
                    pass

            # 1. Path block rules (divided into TCP and UDP rules)
            directions_to_apply = [1, 2] if direction == "both" else ([1] if direction == "inbound" else [2])
            
            for i, path in enumerate(all_paths):
                for d_idx, dir_val in enumerate(directions_to_apply):
                    # Path TCP
                    rule_name_tcp = f"CoreCommander_Throttling_Path_TCP_{i}_{d_idx}"
                    rule_tcp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_tcp.Name = rule_name_tcp
                    rule_tcp.Description = f"CoreCommander Throttling Path TCP Rule {i} Dir {dir_val}"
                    rule_tcp.ApplicationName = path
                    rule_tcp.Protocol = 6  # TCP
                    rule_tcp.Action = 0  # Block
                    rule_tcp.Direction = dir_val
                    rule_tcp.Profiles = 2147483647
                    rule_tcp.Enabled = enabled
                    rule_tcp.Grouping = "CoreCommander_Throttling_Group_TCP"
                    policy.Rules.Add(rule_tcp)
    
                    # Path UDP
                    rule_name_udp = f"CoreCommander_Throttling_Path_UDP_{i}_{d_idx}"
                    rule_udp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_udp.Name = rule_name_udp
                    rule_udp.Description = f"CoreCommander Throttling Path UDP Rule {i} Dir {dir_val}"
                    rule_udp.ApplicationName = path
                    rule_udp.Protocol = 17  # UDP
                    rule_udp.Action = 0  # Block
                    rule_udp.Direction = dir_val
                    rule_udp.Profiles = 2147483647
                    rule_udp.Enabled = enabled
                    rule_udp.Grouping = "CoreCommander_Throttling_Group_UDP"
                    policy.Rules.Add(rule_udp)

            # 2. Local Port block rules
            all_l_tcp = sorted([p for p in local_ports if p >= 1024])
            all_l_udp = sorted([p for p in local_udp_ports if p >= 1024])

            if all_l_tcp:
                local_ports_str = ",".join(str(p) for p in all_l_tcp)
                for d_idx, dir_val in enumerate(directions_to_apply):
                    # TCP
                    rule_name_l_tcp = f"CoreCommander_Throttling_L_TCP_{d_idx}"
                    rule_l_tcp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_l_tcp.Name = rule_name_l_tcp
                    rule_l_tcp.Description = "CoreCommander Throttling Local Port TCP"
                    rule_l_tcp.Protocol = 6  # TCP
                    rule_l_tcp.LocalPorts = local_ports_str
                    rule_l_tcp.Action = 0  # Block
                    rule_l_tcp.Direction = dir_val
                    rule_l_tcp.Profiles = 2147483647
                    rule_l_tcp.Enabled = enabled
                    rule_l_tcp.Grouping = "CoreCommander_Throttling_Group_TCP"
                    policy.Rules.Add(rule_l_tcp)

            if all_l_udp:
                local_udp_ports_str = ",".join(str(p) for p in all_l_udp)
                for d_idx, dir_val in enumerate(directions_to_apply):
                    # UDP
                    rule_name_l_udp = f"CoreCommander_Throttling_L_UDP_{d_idx}"
                    rule_l_udp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_l_udp.Name = rule_name_l_udp
                    rule_l_udp.Description = "CoreCommander Throttling Local Port UDP"
                    rule_l_udp.Protocol = 17  # UDP
                    rule_l_udp.LocalPorts = local_udp_ports_str
                    rule_l_udp.Action = 0  # Block
                    rule_l_udp.Direction = dir_val
                    rule_l_udp.Profiles = 2147483647
                    rule_l_udp.Enabled = enabled
                    rule_l_udp.Grouping = "CoreCommander_Throttling_Group_UDP"
                    policy.Rules.Add(rule_l_udp)

            # 3. Remote Port block rules (for accelerators/proxies bypass)
            ports_to_block = ["20000-30000"]
            for p in remote_ports:
                if not (20000 <= p <= 30000):
                    ports_to_block.append(str(p))
            remote_ports_str = ",".join(ports_to_block)

            for d_idx, dir_val in enumerate(directions_to_apply):
                # TCP
                rule_name_r_tcp = f"CoreCommander_Throttling_R_TCP_{d_idx}"
                rule_r_tcp = win32com.client.Dispatch("HNetCfg.FWRule")
                rule_r_tcp.Name = rule_name_r_tcp
                rule_r_tcp.Description = "CoreCommander Throttling Remote Port TCP"
                rule_r_tcp.Protocol = 6  # TCP
                rule_r_tcp.RemotePorts = remote_ports_str
                rule_r_tcp.Action = 0  # Block
                rule_r_tcp.Direction = dir_val
                rule_r_tcp.Profiles = 2147483647
                rule_r_tcp.Enabled = enabled
                rule_r_tcp.Grouping = "CoreCommander_Throttling_Group_TCP"
                policy.Rules.Add(rule_r_tcp)
    
                # UDP
                rule_name_r_udp = f"CoreCommander_Throttling_R_UDP_{d_idx}"
                rule_r_udp = win32com.client.Dispatch("HNetCfg.FWRule")
                rule_r_udp.Name = rule_name_r_udp
                rule_r_udp.Description = "CoreCommander Throttling Remote Port UDP"
                rule_r_udp.Protocol = 17  # UDP
                rule_r_udp.RemotePorts = remote_ports_str
                rule_r_udp.Action = 0  # Block
                rule_r_udp.Direction = dir_val
                rule_r_udp.Profiles = 2147483647
                rule_r_udp.Enabled = enabled
                rule_r_udp.Grouping = "CoreCommander_Throttling_Group_UDP"
                policy.Rules.Add(rule_r_udp)

            # 4. Loopback Proxy block rules for the game paths to prevent Clash bypasses
            if proxy_ports:
                proxy_ports_str = ",".join(str(p) for p in sorted(proxy_ports))
                for i, path in enumerate(all_paths):
                    for d_idx, dir_val in enumerate(directions_to_apply):
                        # TCP
                        rule_name_loopback = f"CoreCommander_Throttling_Loopback_TCP_{i}_{d_idx}"
                        rule_loopback = win32com.client.Dispatch("HNetCfg.FWRule")
                        rule_loopback.Name = rule_name_loopback
                        rule_loopback.Description = f"CoreCommander Throttling Loopback TCP Rule {i} Dir {dir_val}"
                        rule_loopback.ApplicationName = path
                        rule_loopback.Protocol = 6  # TCP
                        rule_loopback.RemoteAddresses = "127.0.0.1"
                        rule_loopback.RemotePorts = proxy_ports_str
                        rule_loopback.Action = 0  # Block
                        rule_loopback.Direction = dir_val
                        rule_loopback.Profiles = 2147483647
                        rule_loopback.Enabled = enabled
                        rule_loopback.Grouping = "CoreCommander_Throttling_Group_TCP"
                        policy.Rules.Add(rule_loopback)
                    
            # Unconditional Loopback UDP block for game paths (catches LSP/local proxies perfectly)
            for i, path in enumerate(all_paths):
                for d_idx, dir_val in enumerate(directions_to_apply):
                    rule_name_loopback_udp = f"CoreCommander_Throttling_Loopback_UDP_{i}_{d_idx}"
                    rule_loopback_udp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_loopback_udp.Name = rule_name_loopback_udp
                    rule_loopback_udp.Description = f"CoreCommander Throttling Loopback UDP Rule {i} Dir {dir_val}"
                    rule_loopback_udp.ApplicationName = path
                    rule_loopback_udp.Protocol = 17  # UDP
                    rule_loopback_udp.RemoteAddresses = "127.0.0.1"
                    rule_loopback_udp.Action = 0  # Block
                    rule_loopback_udp.Direction = dir_val
                    rule_loopback_udp.Profiles = 2147483647
                    rule_loopback_udp.Enabled = enabled
                    rule_loopback_udp.Grouping = "CoreCommander_Throttling_Group_UDP"
                    policy.Rules.Add(rule_loopback_udp)

            # 5. Remote IP block rules for Clash TUN and global bypasses
            if remote_ips:
                remote_ips_str = ",".join(sorted(remote_ips))
                
                for d_idx, dir_val in enumerate(directions_to_apply):
                    # Remote IP TCP
                    rule_name_ip_tcp = f"CoreCommander_Throttling_IP_TCP_{d_idx}"
                    rule_ip_tcp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_ip_tcp.Name = rule_name_ip_tcp
                    rule_ip_tcp.Description = "CoreCommander Throttling Remote IP TCP"
                    rule_ip_tcp.Protocol = 6  # TCP
                    rule_ip_tcp.RemoteAddresses = remote_ips_str
                    rule_ip_tcp.Action = 0  # Block
                    rule_ip_tcp.Direction = dir_val
                    rule_ip_tcp.Profiles = 2147483647
                    rule_ip_tcp.Enabled = enabled
                    rule_ip_tcp.Grouping = "CoreCommander_Throttling_Group_TCP"
                    policy.Rules.Add(rule_ip_tcp)
    
                    # Remote IP UDP
                    rule_name_ip_udp = f"CoreCommander_Throttling_IP_UDP_{d_idx}"
                    rule_ip_udp = win32com.client.Dispatch("HNetCfg.FWRule")
                    rule_ip_udp.Name = rule_name_ip_udp
                    rule_ip_udp.Description = "CoreCommander Throttling Remote IP UDP"
                    rule_ip_udp.Protocol = 17  # UDP
                    rule_ip_udp.RemoteAddresses = remote_ips_str
                    rule_ip_udp.Action = 0  # Block
                    rule_ip_udp.Direction = dir_val
                    rule_ip_udp.Profiles = 2147483647
                    rule_ip_udp.Enabled = enabled
                    rule_ip_udp.Grouping = "CoreCommander_Throttling_Group_UDP"
                    policy.Rules.Add(rule_ip_udp)

            cls._rules_installed = True
            logger.info("Consolidated split TCP/UDP firewall rules created successfully.")
        except Exception as e:
            logger.error(f"Failed to create firewall rules: {e}")
        finally:
            pythoncom.CoUninitialize()

    @classmethod
    def _detect_game_ports(cls):
        detected_ports = set()
        # Fallback to standard 20000-30000 game ports
        detected_ports.update(range(20000, 30001))

        # Detect active high-port TCP connections for proxy processes
        try:
            proxy_pids = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name = proc.info['name']
                    if name and name.lower() in PROXY_PROCESS_NAMES:
                        proxy_pids.append(proc.info['pid'])
                except Exception:
                    continue

            for conn in psutil.net_connections(kind='tcp'):
                if conn.pid in proxy_pids and conn.raddr:
                    r_ip, r_port = conn.raddr
                    # Exclude common non-game ports
                    if r_port not in (80, 443, 22, 53, 8080, 9090, 9097, 3389):
                        detected_ports.add(r_port)
        except Exception as e:
            logger.error(f"Error dynamically detecting game ports: {e}")

        return list(detected_ports)

    @classmethod
    def _kill_proxy_game_connections(cls, game_ports, remote_ips=None):
        try:
            proxy_pids = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    name = proc.info['name']
                    if name and name.lower() in PROXY_PROCESS_NAMES:
                        proxy_pids.append(proc.info['pid'])
                except Exception:
                    continue

            game_ips = set(remote_ips) if remote_ips else set()
            killed_count = 0
            for conn in psutil.net_connections(kind='tcp'):
                is_target = False
                if conn.raddr:
                    r_ip, r_port = conn.raddr
                    # Never kill connections to known DNS servers or on system-reserved ports.
                    # Killing DNS connections (DoT port 853, DoH port 443 to 8.8.8.8 etc.)
                    # severs system-wide DNS resolution and causes total loss of game network.
                    if r_ip in KNOWN_DNS_IPS or r_port in SYSTEM_RESERVED_PORTS:
                        continue
                    # Proxy connections to game ports/IPs
                    if conn.pid in proxy_pids:
                        if r_port in game_ports or r_ip in game_ips:
                            is_target = True
                    # Game connections directly to game ports/IPs
                    elif conn.pid in cls._cache_pids:
                        if r_port in game_ports or r_ip in game_ips:
                            is_target = True
                
                if is_target:
                    if close_tcp_connection(conn.laddr[0], conn.laddr[1], conn.raddr[0], conn.raddr[1]):
                        killed_count += 1
            if killed_count > 0:
                logger.info(f"Terminated {killed_count} active game-related connections.")
        except Exception as e:
            logger.error(f"Error terminating proxy game connections: {e}")

    @classmethod
    def apply_rate_limit(cls, target_pid: int, target_name: str, upload_rate: float, download_rate: float, unit: str, limit_type: str = "firewall", direction: str = "both") -> bool:
        cls._current_limit_type = limit_type
        cls._current_upload_rate_value = upload_rate
        cls._current_download_rate_value = download_rate
        cls._current_unit = unit
        cls._current_direction = direction
        cls._is_throttling = True
        
        # Trigger proxy connection termination dynamically using cached info
        with cls._cache_lock:
            cached_ports = list(cls._cache_remote_ports)
            cached_ips = list(cls._cache_remote_ips)
        if (cached_ports or cached_ips) and limit_type == "firewall":
            cls._worker.submit(cls._kill_proxy_game_connections, cached_ports, cached_ips)
            
        if not HAS_PYDIVERT:
            # Absolute fallback if PyDivert is literally missing from the python environment
            logger.info(f"Apply rate limit: {limit_type} mode without PyDivert. Using firewall rules fallback.")
            cls._worker.submit(cls._apply_rate_limit_fallback_internal, target_pid, target_name, upload_rate, download_rate, unit, limit_type, direction)
        else:
            # Use WinDivert for ALL modes (Firewall Blocker or QoS delay). 
            # Generic UDP filtering handles TUN/proxy traffic flawlessly without slow firewall rules.
            logger.info(f"Apply rate limit: {limit_type} mode. Opening WinDivert on-demand.")
            if cls._wd_thread is None or not cls._wd_thread.is_alive():
                try:
                    cls._restart_windivert()
                except Exception as e:
                    logger.error(f"Failed to start WinDivert synchronously: {e}. Falling back to firewall pulsing.")
                    cls._worker.submit(cls._apply_rate_limit_fallback_internal, target_pid, target_name, upload_rate, download_rate, unit, limit_type, direction)
            
        return True

    @classmethod
    def _apply_rate_limit_first_time_internal(cls, target_pid: int, target_name: str, upload_rate: float, download_rate: float, unit: str, limit_type: str, direction: str):
        try:
            cls.cache_target_info(target_pid, target_name, force_rescan=True)
            with cls._cache_lock:
                has_ports_or_ips = bool(cls._cache_local_ports or cls._cache_local_udp_ports or cls._cache_remote_ips)
            if HAS_PYDIVERT and has_ports_or_ips:
                try:
                    cls._restart_windivert()
                except Exception as e:
                    logger.error(f"Failed to start WinDivert on first time internal scan: {e}")
                    cls._apply_rate_limit_fallback_internal(target_pid, target_name, upload_rate, download_rate, unit, limit_type, direction)
            else:
                cls._apply_rate_limit_fallback_internal(target_pid, target_name, upload_rate, download_rate, unit, limit_type, direction)
        except Exception as e:
            logger.error(f"Error in first time rate limit application: {e}")

    @classmethod
    def _apply_rate_limit_fallback_internal(cls, target_pid: int, target_name: str, upload_rate: float, download_rate: float, unit: str, limit_type: str, direction: str = "both"):
        # Stop background pulsing thread if active
        if cls._pulsing_stop_event is not None:
            cls._pulsing_stop_event.set()
            if cls._pulsing_thread is not None:
                cls._pulsing_thread.join(timeout=0.5)
            cls._pulsing_thread = None
            cls._pulsing_stop_event = None

        # Quick pre-flight check: ensure firewall service is started (extremely fast if already running)
        cls._ensure_firewall_service_started()

        # Ensure loopback backwards compatibility is enabled (very quick registry query/set)
        cls._enable_loopback_compatibility()

        # Check if rules are installed or cache is completely empty. 
        with cls._cache_lock:
            has_cache = bool(cls._cache_local_ports or cls._cache_local_udp_ports or cls._cache_remote_ips)
            
        if not cls._rules_installed or not has_cache:
            logger.info("Throttler cache is empty or rules are uninstalled. Triggering immediate synchronous scan...")
            cls.cache_target_info(target_pid, target_name, force_rescan=True)
            cls._create_rules_internal(enabled=False, direction=direction)

        pythoncom.CoInitializeEx(0)
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            
            # Temporarily enable profiles if disabled
            cls._enabled_profiles = []
            profile_map = {1: "Domain", 2: "Private", 4: "Public"}
            disabled_profiles = []
            for val, name in profile_map.items():
                if not policy.FirewallEnabled(val):
                    disabled_profiles.append(val)
            
            if disabled_profiles:
                logger.info(f"Temporarily enabling firewall profiles via COM: {disabled_profiles}")
                for profile_val in disabled_profiles:
                    policy.SetFirewallEnabled(profile_val, True)
                cls._enabled_profiles = disabled_profiles
        except Exception as e:
            logger.error(f"Failed to query/enable profiles: {e}")
        finally:
            pythoncom.CoUninitialize()

        if limit_type == "qos":
            cls._pulsing_stop_event = threading.Event()
            cls._pulsing_thread = QosPulsingThread(upload_rate, download_rate, unit, cls._pulsing_stop_event)
            cls._pulsing_thread.start()
            logger.info(f"NetworkThrottlerService: Precision Pulsing QoS simulated rate limit enabled (TCP/UDP).")
        else:
            # Blocker mode - enable both TCP and UDP rule groups for absolute firewall blocking
            pythoncom.CoInitializeEx(0)
            try:
                with getattr(cls, '_firewall_lock', __import__('threading').RLock()):
                    if not getattr(cls, '_is_throttling', False):
                        return
                    policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", True)
                    policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", True)
                    logger.info("NetworkThrottlerService: Absolute Card Block firewall rules enabled.")
            except Exception as e:
                logger.error(f"Failed to enable blocking groups: {e}")
            finally:
                pythoncom.CoUninitialize()

    @classmethod
    def remove_rate_limit(cls) -> bool:
        cls._is_throttling = False
        
        if getattr(cls, "_deferred_wd_restart", False):
            logger.info("Applying deferred WinDivert restart after throttling finished.")
            cls._deferred_wd_restart = False
            cls._restart_windivert()
        cls._current_limit_type = None
        cls._current_upload_rate_value = 0.0
        cls._current_download_rate_value = 0.0
        
        # WinDivert worker is persistent now, just tell it to stop throttling
        if getattr(cls, "_wd_thread", None):
            cls._wd_thread.sender_thread.clear(send_remaining=True)
        import threading
        threading.Thread(target=cls._remove_rate_limit_fallback_internal, daemon=True).start()
        return True

    @classmethod
    def _remove_rate_limit_fallback_internal(cls):
        if cls._pulsing_stop_event is not None:
            cls._pulsing_stop_event.set()
            if cls._pulsing_thread is not None:
                cls._pulsing_thread.join(timeout=0.5)
            cls._pulsing_thread = None
            cls._pulsing_stop_event = None

        # Disable firewall rules instantly
        pythoncom.CoInitializeEx(0)
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_UDP", False)
            policy.EnableRuleGroup(2147483647, "CoreCommander_Throttling_Group_TCP", False)
            logger.info("NetworkThrottlerService: Throttler/Blocker rules disabled.")
        except Exception as e:
            logger.error(f"Failed to disable firewall rules: {e}")
        finally:
            pythoncom.CoUninitialize()

    @classmethod
    def force_delete_rules(cls) -> bool:
        cls._worker.submit(cls._force_delete_rules_internal)
        return True

    @classmethod
    def _force_delete_rules_internal(cls):
        cls._current_limit_type = None
        cls._current_upload_rate_value = 0.0
        cls._current_download_rate_value = 0.0

        if getattr(cls, '_wd_stop_event', None) is not None:
            cls._wd_stop_event.set()
            if getattr(cls, '_wd_thread', None) is not None:
                if cls._wd_thread.wd:
                    try:
                        cls._wd_thread.wd.close()
                    except Exception:
                        pass
                cls._wd_thread.join(timeout=1.0)
            cls._wd_stop_event = None
            cls._wd_thread = None        # Stop background threads
        if cls._pulsing_stop_event is not None:
            cls._pulsing_stop_event.set()
            if cls._pulsing_thread is not None:
                cls._pulsing_thread.join(timeout=0.5)
            cls._pulsing_thread = None
            cls._pulsing_stop_event = None

        pythoncom.CoInitializeEx(0)
        try:
            policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
            rules_to_remove = []
            for r in policy.Rules:
                if (r.Name.startswith("CoreCommander_Throttling_") or 
                    r.Grouping in ("CoreCommander_Throttling_Group", "CoreCommander_Throttling_Group_TCP", "CoreCommander_Throttling_Group_UDP")):
                    rules_to_remove.append(r.Name)
            for name in rules_to_remove:
                try:
                    policy.Rules.Remove(name)
                except Exception:
                    pass
            logger.info(f"NetworkThrottlerService: Completely deleted {len(rules_to_remove)} rules.")
        except Exception as e:
            logger.error(f"Failed to delete rules: {e}")
        finally:
            pythoncom.CoUninitialize()

        # Restore firewall profiles
        if cls._enabled_profiles:
            pythoncom.CoInitializeEx(0)
            try:
                policy = win32com.client.Dispatch("HNetCfg.FwPolicy2")
                logger.info(f"Restoring firewall profiles to disabled via COM: {cls._enabled_profiles}")
                for profile_val in cls._enabled_profiles:
                    try:
                        policy.SetFirewallEnabled(profile_val, False)
                    except Exception:
                        pass
                cls._enabled_profiles = []
            except Exception as e:
                logger.error(f"Failed to restore profiles: {e}")
            finally:
                pythoncom.CoUninitialize()

        cls._rules_installed = False
        cls._restore_firewall_service()