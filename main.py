# filename: main.py
import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess
from netmiko import ConnectHandler
import paramiko
import json
import re
import socket
import threading

app = FastAPI()
active_websockets = set()
DEVICE_PORT_STATES = {}
TOPOLOGY_CACHE = {"nodes": [], "edges": [], "log": "서버 초기화 중... 잠시 후 다시 시도하세요.", "vlans": [], "subnets": []}

class PingReq(BaseModel):
    source: str
    target: str

class PortReq(BaseModel):
    target_device: str
    target_port: str

class CliReq(BaseModel):
    target_device: str
    command: str

DEVICE_INFO = {
    "R3": {"device_type": "cisco_ios", "host": "10.1.1.9", "username": "admin", "password": "1234"},
    "R4": {"device_type": "cisco_ios", "host": "10.1.1.10", "username": "admin", "password": "1234"},
    "SW4": {"device_type": "cisco_ios", "host": "10.254.254.14", "username": "ansible", "password": "1234"},
    "SW5": {"device_type": "cisco_ios", "host": "10.254.254.15", "username": "ansible", "password": "1234"},
    "SW2": {"device_type": "cisco_ios_telnet", "host": "10.254.254.12", "username": "ansible", "password": "1234"},
    "SW6": {"device_type": "cisco_ios_telnet", "host": "10.254.254.16", "username": "ansible", "password": "1234"},
    "SW8": {"device_type": "cisco_ios_telnet", "host": "10.254.254.18", "username": "ansible", "password": "1234"}
}

def get_connection(hostname):
    dev = DEVICE_INFO[hostname].copy()
    if hostname in ["R3", "R4"]:
        jump_cmd = f"sshpass -p '1234' ssh -o StrictHostKeyChecking=no -p 2222 -W {dev['host']}:22 vboxuser@10.0.2.2"
        dev["sock"] = paramiko.ProxyCommand(jump_cmd)
    return ConnectHandler(**dev)

def fetch_port_state(hostname):
    conn = get_connection(hostname)
    current_states = {}
    
    if hostname in ["R3", "R4"]:
        out = conn.send_command("show ip interface brief")
        for line in out.splitlines():
            if "Interface" in line: continue
            parts = line.split()
            if len(parts) >= 2:
                port = parts[0]
                status = "up" if "up" in line.lower() else "down (차단/단절)"
                current_states[port] = status
    else:
        out = conn.send_command("show interface status")
        for line in out.splitlines():
            parts = line.split()
            if not parts or "Port" in line: continue
            port = parts[0]
            if "disabled" in line: status = "수동차단 (disabled)"
            elif "notconnect" in line: status = "물리적 끊김 (notconnect)"
            elif "err-disabled" in line: status = "보안/오류 차단 (err-disabled)"
            elif " BLK " in line: status = "루프 방지 (STP blocking)"
            elif "connected" in line: status = "정상 연결됨 (connected)"
            else: continue
            current_states[port] = status
            
    conn.disconnect()
    return current_states

def normalize_name(name):
    name = re.sub(r'(?i)switch-?(\d+)', r'SW\1', name)
    name = re.sub(r'(?i)router-?(\d+)', r'R\1', name)
    return name.upper()

def perform_discovery():
    discovered_edges = []
    discovered_nodes = {}
    edge_tracker = set()
    full_status_log = ""
    all_vlans = set()
    all_subnets = set()
    
    for hostname in DEVICE_INFO.keys():
        try:
            connection = get_connection(hostname)
            
            ip_out = connection.send_command("show ip interface brief")
            full_status_log += f"\n[{hostname} 포트 상태]\n{ip_out}\n"
            
            ip_map = {"routed": []}
            for line in ip_out.splitlines():
                if "up" in line.lower() and "unassigned" not in line.lower():
                    parts = line.split()
                    if len(parts) >= 2:
                        intf = parts[0]
                        ip = parts[1]
                        if "Vlan" in intf:
                            vlan_id = intf.replace("Vlan", "")
                            ip_map[vlan_id] = ip
                            all_vlans.add(vlan_id)
                        elif "Loopback" not in intf:
                            ip_parts = ip.split(".")
                            if len(ip_parts) == 4:
                                subnet = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.x"
                                if subnet not in ip_map:
                                    ip_map[subnet] = []
                                ip_map[subnet].append(ip)
                                all_subnets.add(subnet)
                            ip_map["routed"].append(ip)

            vlan_map = {}
            if hostname not in ["R3", "R4"]:
                status_out = connection.send_command("show interface status")
                for line in status_out.splitlines():
                    match = re.search(r'(Fa\S+|Gi\S+|Te\S+).*?(connected|notconnect|disabled|err-disabled)\s+(\w+)', line)
                    if match:
                        port = match.group(1)
                        vlan_str = match.group(3)
                        vlan_map[port] = vlan_str
                        if vlan_str.isdigit():
                            all_vlans.add(vlan_str)
            
            discovered_nodes[hostname] = {
                "id": hostname,
                "ip_map": ip_map,
                "type": "router" if hostname in ["R3", "R4"] else "switch"
            }
            
            cdp_out = connection.send_command("show cdp neighbors detail")
            connection.disconnect()
            
            target_dev = None
            for line in cdp_out.splitlines():
                if line.startswith("Device ID:"):
                    raw_name = line.split("Device ID:")[1].strip().split(".")[0]
                    target_dev = normalize_name(raw_name)
                elif line.startswith("Interface:"):
                    parts = line.split(",")
                    if len(parts) >= 2:
                        l_str = parts[0].replace("Interface:", "").strip()
                        r_str = parts[1].replace("Port ID (outgoing port):", "").strip()
                        local_port = l_str.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa")
                        remote_port = r_str.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa")
                        
                        if target_dev and local_port and remote_port:
                            if target_dev not in discovered_nodes:
                                discovered_nodes[target_dev] = {"id": target_dev, "ip_map": {"routed":[]}, "type": "unknown"}
                            
                            link_pair = tuple(sorted([hostname, target_dev]))
                            if link_pair not in edge_tracker:
                                edge_tracker.add(link_pair)
                                
                                link_vlan = "routed" if hostname in ["R3", "R4"] else vlan_map.get(local_port, "routed")
                                
                                discovered_edges.append({
                                    "from": hostname, "to": target_dev,
                                    "from_port": local_port, "to_port": remote_port,
                                    "vlan": link_vlan,
                                    "label": f"{hostname}({local_port})\n ↕ \n{target_dev}({remote_port})"
                                })
                            target_dev = None
        except Exception as e:
            print(f"Error on {hostname} discovery: {e}")
            
    nodes_list = list(discovered_nodes.values())
    vlans_list = sorted(list(all_vlans), key=lambda x: int(x) if x.isdigit() else 999)
    subnets_list = sorted(list(all_subnets))
    return {"nodes": nodes_list, "edges": discovered_edges, "log": full_status_log, "vlans": vlans_list, "subnets": subnets_list}

async def init_system_cache():
    print("[System] 서버 시작: 포트 상태 캐싱 중...")
    for hostname in DEVICE_INFO.keys():
        try:
            loop = asyncio.get_running_loop()
            states = await loop.run_in_executor(None, fetch_port_state, hostname)
            DEVICE_PORT_STATES[hostname] = states
            print(f"[{hostname}] 포트 캐싱 완료.")
        except Exception as e:
            print(f"[{hostname}] 포트 캐싱 실패: {e}")
            
    print("[System] 서버 시작: 토폴로지 캐싱 중...")
    try:
        loop = asyncio.get_running_loop()
        global TOPOLOGY_CACHE
        TOPOLOGY_CACHE = await loop.run_in_executor(None, perform_discovery)
        print("[System] 토폴로지 캐싱 완료. 모든 준비 끝.")
    except Exception as e:
        print(f"[System] 토폴로지 캐싱 실패: {e}")

def analyze_trap(ip):
    global DEVICE_PORT_STATES
    hostname = None
    for name, info in DEVICE_INFO.items():
        if info["host"] == ip:
            hostname = name
            break
            
    if not hostname:
        return f"[SNMP] 미등록 장비({ip}) 감지됨"
        
    try:
        current_states = fetch_port_state(hostname)
        old_states = DEVICE_PORT_STATES.get(hostname, {})
        logs = []
        
        for port, status in current_states.items():
            old_status = old_states.get(port)
            if old_status != status:
                logs.append(f" - {port} : {old_status or '알수없음'} ➜ {status}")

        DEVICE_PORT_STATES[hostname] = current_states
        
        if logs:
            return f"[{hostname}] 포트 상태 변경 감지:\n" + "\n".join(logs)
        else:
            return f"[{hostname}] Trap 수신 (상태 변경 없음 - 일시적 플래핑 의심)"
            
    except Exception as e:
        return f"[{hostname}] 원격 분석 실패: {e}"

async def broadcast(msg):
    dead = set()
    for ws in active_websockets:
        try:
            await ws.send_text(msg)
        except:
            dead.add(ws)
    active_websockets.difference_update(dead)

async def handle_trap_async(ip):
    await broadcast(f"[System] {ip} 이벤트 감지. 장비 SSH 상태 비교 중...")
    loop = asyncio.get_running_loop()
    msg = await loop.run_in_executor(None, analyze_trap, ip)
    await broadcast(msg)

def udp_listener_thread(loop):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 1162))
    while True:
        try:
            data, addr = sock.recvfrom(2048)
            ip = addr[0]
            asyncio.run_coroutine_threadsafe(handle_trap_async(ip), loop)
        except Exception as e:
            print(f"UDP 수신 에러: {e}")

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_running_loop()
    asyncio.create_task(init_system_cache())
    t = threading.Thread(target=udp_listener_thread, args=(loop,), daemon=True)
    t.start()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        active_websockets.discard(websocket)

def run_ansible(playbook: str, extra_vars: dict = None):
    cmd = ["ansible-playbook", "-i", "inventory.yaml", playbook]
    if extra_vars:
        cmd.extend(["--extra-vars", json.dumps(extra_vars)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return {"status": "success", "message": "실행 완료", "output": result.stdout}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": "Ansible 에러", "output": e.stderr or e.stdout}

@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.get("/api/discover")
def discover_topology():
    return {"status": "success", **TOPOLOGY_CACHE}

@app.post("/api/discover/refresh")
def refresh_topology():
    global TOPOLOGY_CACHE
    try:
        TOPOLOGY_CACHE = perform_discovery()
        return {"status": "success", **TOPOLOGY_CACHE}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/ping")
def do_ping(req: PingReq):
    if req.source not in DEVICE_INFO:
        return {"status": "error", "message": "장비 정보 없음"}
    try:
        connection = get_connection(req.source)
        output = connection.send_command(f"ping {req.target} repeat 3")
        connection.disconnect()
        return {"status": "success", "message": f"Ping 결과:\n{output}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/cli")
def do_cli(req: CliReq):
    if req.target_device not in DEVICE_INFO:
        return {"status": "error", "message": "장비 정보 없음"}
    try:
        connection = get_connection(req.target_device)
        output = connection.send_command(req.command)
        connection.disconnect()
        return {"status": "success", "message": f"실행 완료", "output": output}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/shutdown")
def do_shutdown(req: PortReq):
    vars = {"target_device": req.target_device, "target_port": req.target_port}
    return run_ansible("port_shut.yaml", vars)

@app.post("/api/restore")
def do_restore(req: PortReq):
    vars = {"target_device": req.target_device, "target_port": req.target_port}
    return run_ansible("port_restore.yaml", vars)
