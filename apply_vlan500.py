# filename: apply_vlan500.py
import paramiko
from netmiko import ConnectHandler

DEVICE_INFO = {
    "R3": {"device_type": "cisco_ios", "host": "10.1.1.9", "username": "admin", "password": "1234"},
    "SW4": {"device_type": "cisco_ios", "host": "10.254.254.14", "username": "ansible", "password": "1234"},
    "SW5": {"device_type": "cisco_ios", "host": "10.254.254.15", "username": "ansible", "password": "1234"},
    "SW2": {"device_type": "cisco_ios_telnet", "host": "10.254.254.12", "username": "ansible", "password": "1234", "secret": "1234"},
    "SW6": {"device_type": "cisco_ios_telnet", "host": "10.254.254.16", "username": "ansible", "password": "1234", "secret": "1234"},
    "SW8": {"device_type": "cisco_ios_telnet", "host": "192.168.40.18", "username": "ansible", "password": "1234", "secret": "1234"}
}

CONFIGS = {
    "R3": [
        "ip dhcp excluded-address 10.50.50.1 10.50.50.10",
        "ip dhcp pool VLAN500_POOL",
        "network 10.50.50.0 255.255.255.0",
        "default-router 10.50.50.4",
        "dns-server 8.8.8.8",
        "exit",
        "ip route 10.50.50.0 255.255.255.0 10.1.1.14"
    ],
    "SW4": [
        "vlan 500", "name NetDevOps_VLAN", "exit",
        "interface Vlan500", "ip address 10.50.50.4 255.255.255.0", "ip helper-address 10.1.1.9", "no shutdown", "exit",
        "router ospf 1", "network 10.50.50.0 0.0.0.255 area 0", "network 10.0.1.0 0.0.0.255 area 0", "redistribute rip subnets", "exit",
        "router rip", "version 2", "network 10.0.0.0", "redistribute ospf 1 metric 2", "exit"
    ],
    "SW5": [
        "vlan 500", "name NetDevOps_VLAN", "exit",
        "interface Vlan500", "ip address 10.50.50.5 255.255.255.0", "ip helper-address 10.1.1.9", "no shutdown", "exit",
        "router ospf 1", "network 10.50.50.0 0.0.0.255 area 0", "network 10.0.1.0 0.0.0.255 area 0", "redistribute rip subnets", "exit",
        "router rip", "version 2", "network 10.0.0.0", "redistribute ospf 1 metric 2", "exit"
    ],
    "SW2": [
        "vlan 500", "name NetDevOps_VLAN", "exit",
        "interface Vlan500", "ip address 10.50.50.2 255.255.255.0", "no shutdown", "exit",
        "router rip", "version 2", "network 10.0.0.0", "exit"
    ],
    "SW6": [
        "vlan 500", "name NetDevOps_VLAN", "exit",
        "interface Vlan500", "ip address 10.50.50.6 255.255.255.0", "no shutdown", "exit",
        "router rip", "version 2", "network 10.0.0.0", "exit"
    ],
    "SW8": [
        "vlan 500", "name NetDevOps_VLAN", "exit",
        "interface Vlan500", "ip address 10.50.50.8 255.255.255.0", "no shutdown", "exit"
    ]
}

def main():
    print("=== SSoT CLI 직접 적용 시작 ===")
    for host, cmds in CONFIGS.items():
        print(f"\n[{host}] 장비 접속 중...")
        dev = DEVICE_INFO[host].copy()
        
        # R3 점프호스트 세팅
        if host in ["R3", "R4"]:
            jump_cmd = f"sshpass -p '1234' ssh -o StrictHostKeyChecking=no -p 2222 -W {dev['host']}:22 vboxuser@10.0.2.2"
            dev["sock"] = paramiko.ProxyCommand(jump_cmd)
        
        try:
            conn = ConnectHandler(**dev)
            
            # Telnet의 경우 enable 모드 진입 확인
            if dev["device_type"] == "cisco_ios_telnet":
                conn.enable()
                
            print(f"[{host}] 설정 전송 중...")
            out = conn.send_config_set(cmds)
            
            print(f"[{host}] 설정 저장 중...")
            conn.save_config()
            
            conn.disconnect()
            print(f"[{host}] ✅ 완료!\n{out}")
        except Exception as e:
            print(f"[{host}] ❌ 실패! 원인: {e}")

if __name__ == "__main__":
    main()
