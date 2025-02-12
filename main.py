#!/usr/bin/env python3
import os
import sys
import subprocess
import re
import ipaddress
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import platform
from datetime import datetime

class Colors:
    RED = '\033[0;31m'
    ORANGE = '\033[0;33m'
    GREEN = '\033[0;32m'
    NC = '\033[0m'  # No Color

class WireGuardInstaller:
    def __init__(self):
        self.server_pub_ip = ""
        self.server_pub_nic = ""
        self.server_wg_nic = "wg0"
        self.server_wg_ipv4 = "10.66.66.1"
        self.server_wg_ipv6 = "fd42:42:42::1"
        self.server_port = self._generate_random_port()
        self.server_priv_key = ""
        self.server_pub_key = ""
        self.client_dns_1 = "1.1.1.1"
        self.client_dns_2 = "1.0.0.1"
        self.allowed_ips = "0.0.0.0/0,::/0"
        self.config_dir = "/etc/wireguard"

    def is_root(self) -> None:
        """Check if the script is run as root"""
        if os.geteuid() != 0:
            print("You need to run this script as root")
            sys.exit(1)

    def check_os(self) -> str:
        """Check if the OS is supported and return OS type"""
        os_type = ""
        if os.path.exists('/etc/os-release'):
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('ID='):
                        os_type = line.strip().split('=')[1].strip('"')
                        break

        if os_type in ['debian', 'ubuntu', 'fedora', 'centos', 'almalinux', 'rocky', 'arch', 'alpine']:
            return os_type
        else:
            print("Your operating system is not supported")
            sys.exit(1)

    def check_virtualization(self) -> None:
        """Check if the system is running in a supported virtualization environment"""
        def check_virt_type() -> str:
            try:
                virt = subprocess.check_output(['systemd-detect-virt']).decode().strip()
                return virt
            except:
                return ""

        virt_type = check_virt_type()
        if virt_type == "openvz":
            print("OpenVZ is not supported")
            sys.exit(1)
        elif virt_type == "lxc":
            print("LXC is not supported (yet)")
            sys.exit(1)

    def _generate_random_port(self) -> int:
        """Generate a random port between 49152 and 65535"""
        import random
        return random.randint(49152, 65535)

    def _detect_public_ip(self) -> str:
        """Detect the public IP address of the server"""
        try:
            ip = subprocess.check_output(
                "ip -4 addr | sed -ne 's|^.* inet \\([^/]*\\)/.* scope global.*$|\\1|p' | head -1",
                shell=True
            ).decode().strip()
            if not ip:
                ip = subprocess.check_output(
                    "ip -6 addr | sed -ne 's|^.* inet6 \\([^/]*\\)/.* scope global.*$|\\1|p' | head -1",
                    shell=True
                ).decode().strip()
            return ip
        except:
            return ""

    def _detect_public_interface(self) -> str:
        """Detect the public network interface"""
        try:
            return subprocess.check_output(
                "ip -4 route ls | grep default | grep -Po '(?<=dev )(\S+)'",
                shell=True
            ).decode().strip()
        except:
            return "eth0"

    def install_wireguard(self) -> None:
        """Install WireGuard and its dependencies"""
        os_type = self.check_os()
        
        # Install packages based on OS type
        if os_type in ['ubuntu', 'debian']:
            subprocess.run(['apt-get', 'update'])
            subprocess.run(['apt-get', 'install', '-y', 'wireguard', 'iptables', 'resolvconf', 'qrencode'])
        elif os_type == 'fedora':
            subprocess.run(['dnf', 'install', '-y', 'wireguard-tools', 'iptables', 'qrencode'])
        elif os_type in ['centos', 'almalinux', 'rocky']:
            subprocess.run(['yum', 'install', '-y', 'epel-release', 'elrepo-release'])
            subprocess.run(['yum', 'install', '-y', 'kmod-wireguard', 'wireguard-tools', 'iptables', 'qrencode'])
        elif os_type == 'arch':
            subprocess.run(['pacman', '-S', '--needed', '--noconfirm', 'wireguard-tools', 'qrencode'])

        # Create WireGuard directory
        os.makedirs(self.config_dir, exist_ok=True)
        os.chmod(self.config_dir, 0o700)

        # Generate keys
        self.server_priv_key = subprocess.check_output(['wg', 'genkey']).decode().strip()
        self.server_pub_key = subprocess.check_output(['wg', 'pubkey'], 
                                                    input=self.server_priv_key.encode()).decode().strip()

    def setup_server(self) -> None:
        """Configure the WireGuard server"""
        # Detect network settings
        self.server_pub_ip = self._detect_public_ip()
        self.server_pub_nic = self._detect_public_interface()

        # Create server config
        config = f"""[Interface]
Address = {self.server_wg_ipv4}/24,{self.server_wg_ipv6}/64
ListenPort = {self.server_port}
PrivateKey = {self.server_priv_key}

PostUp = iptables -I INPUT -p udp --dport {self.server_port} -j ACCEPT
PostUp = iptables -I FORWARD -i {self.server_pub_nic} -o {self.server_wg_nic} -j ACCEPT
PostUp = iptables -I FORWARD -i {self.server_wg_nic} -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o {self.server_pub_nic} -j MASQUERADE
PostDown = iptables -D INPUT -p udp --dport {self.server_port} -j ACCEPT
PostDown = iptables -D FORWARD -i {self.server_pub_nic} -o {self.server_wg_nic} -j ACCEPT
PostDown = iptables -D FORWARD -i {self.server_wg_nic} -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o {self.server_pub_nic} -j MASQUERADE
"""
        
        with open(f"{self.config_dir}/{self.server_wg_nic}.conf", 'w') as f:
            f.write(config)

        # Enable IP forwarding
        with open('/etc/sysctl.d/wg.conf', 'w') as f:
            f.write("net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1")
        
        subprocess.run(['sysctl', '--system'])

        # Start WireGuard
        subprocess.run(['systemctl', 'enable', f'wg-quick@{self.server_wg_nic}'])
        subprocess.run(['systemctl', 'start', f'wg-quick@{self.server_wg_nic}'])

    def add_client(self, client_name: str) -> Dict:
        """Add a new WireGuard client"""
        if not re.match("^[a-zA-Z0-9_-]+$", client_name) or len(client_name) > 15:
            print("Invalid client name")
            return {}

        # Generate client keys
        client_priv_key = subprocess.check_output(['wg', 'genkey']).decode().strip()
        client_pub_key = subprocess.check_output(['wg', 'pubkey'], 
                                               input=client_priv_key.encode()).decode().strip()
        psk = subprocess.check_output(['wg', 'genpsk']).decode().strip()

        # Determine client IP addresses
        existing_ips = self._get_existing_ips()
        client_ipv4 = self._get_next_ip(existing_ips, 4)
        client_ipv6 = self._get_next_ip(existing_ips, 6)

        # Create client config
        client_config = f"""[Interface]
PrivateKey = {client_priv_key}
Address = {client_ipv4}/32,{client_ipv6}/128
DNS = {self.client_dns_1},{self.client_dns_2}

[Peer]
PublicKey = {self.server_pub_key}
PresharedKey = {psk}
Endpoint = {self.server_pub_ip}:{self.server_port}
AllowedIPs = {self.allowed_ips}
"""

        # Save client config
        client_dir = f"{self.config_dir}/clients"
        os.makedirs(client_dir, exist_ok=True)
        config_file = f"{client_dir}/{client_name}.conf"
        with open(config_file, 'w') as f:
            f.write(client_config)

        # Add client to server config
        with open(f"{self.config_dir}/{self.server_wg_nic}.conf", 'a') as f:
            f.write(f"""
### Client {client_name}
[Peer]
PublicKey = {client_pub_key}
PresharedKey = {psk}
AllowedIPs = {client_ipv4}/32,{client_ipv6}/128
""")

        # Apply changes
        subprocess.run(['wg', 'syncconf', self.server_wg_nic, 
                       f"{self.config_dir}/{self.server_wg_nic}.conf"])

        # Generate QR code
        try:
            subprocess.run(['qrencode', '-t', 'ansiutf8', '-l', 'L'], 
                         input=client_config.encode())
        except:
            pass

        return {
            'name': client_name,
            'config_file': config_file,
            'public_key': client_pub_key,
            'ipv4': client_ipv4,
            'ipv6': client_ipv6
        }

    def _get_existing_ips(self) -> List[str]:
        """Get list of IPs already assigned to clients"""
        try:
            with open(f"{self.config_dir}/{self.server_wg_nic}.conf") as f:
                content = f.read()
            return re.findall(r'AllowedIPs = ([\d\./,]+)', content)
        except:
            return []

    def _get_next_ip(self, existing_ips: List[str], ip_version: int) -> str:
        """Get next available IP address"""
        if ip_version == 4:
            base_ip = self.server_wg_ipv4.rsplit('.', 1)[0]
            for i in range(2, 255):
                candidate = f"{base_ip}.{i}"
                if not any(candidate in ip for ip in existing_ips):
                    return candidate
        else:
            base_ip = self.server_wg_ipv6.rsplit(':', 1)[0]
            for i in range(2, 255):
                candidate = f"{base_ip}:{i}"
                if not any(candidate in ip for ip in existing_ips):
                    return candidate
        raise Exception("No available IPs")

    def list_clients(self) -> List[str]:
        """List all WireGuard clients"""
        try:
            with open(f"{self.config_dir}/{self.server_wg_nic}.conf") as f:
                content = f.read()
            return re.findall(r'### Client (.+)', content)
        except:
            return []

    def remove_client(self, client_name: str) -> bool:
        """Remove a WireGuard client"""
        config_file = f"{self.config_dir}/{self.server_wg_nic}.conf"
        try:
            with open(config_file) as f:
                lines = f.readlines()

            # Find and remove client section
            start_idx = -1
            end_idx = -1
            for i, line in enumerate(lines):
                if f"### Client {client_name}" in line:
                    start_idx = i
                elif start_idx != -1 and line.strip() == "":
                    end_idx = i
                    break

            if start_idx != -1 and end_idx != -1:
                lines = lines[:start_idx] + lines[end_idx + 1:]
                with open(config_file, 'w') as f:
                    f.writelines(lines)

                # Remove client config file
                client_config = f"{self.config_dir}/clients/{client_name}.conf"
                if os.path.exists(client_config):
                    os.remove(client_config)

                # Apply changes
                subprocess.run(['wg', 'syncconf', self.server_wg_nic, config_file])
                return True
        except Exception as e:
            print(f"Error removing client: {e}")
        return False

    def uninstall(self) -> None:
        """Uninstall WireGuard"""
        try:
            # Stop and disable WireGuard
            subprocess.run(['systemctl', 'stop', f'wg-quick@{self.server_wg_nic}'])
            subprocess.run(['systemctl', 'disable', f'wg-quick@{self.server_wg_nic}'])

            # Remove configuration
            if os.path.exists(self.config_dir):
                subprocess.run(['rm', '-rf', self.config_dir])
            if os.path.exists('/etc/sysctl.d/wg.conf'):
                os.remove('/etc/sysctl.d/wg.conf')

            # Remove packages based on OS
            os_type = self.check_os()
            if os_type in ['ubuntu', 'debian']:
                subprocess.run(['apt-get', 'remove', '-y', 'wireguard', 'wireguard-tools', 'qrencode'])
            elif os_type == 'fedora':
                subprocess.run(['dnf', 'remove', '-y', 'wireguard-tools', 'qrencode'])
            elif os_type in ['centos', 'almalinux', 'rocky']:
                subprocess.run(['yum', 'remove', '-y', 'wireguard-tools', 'kmod-wireguard', 'qrencode'])
            elif os_type == 'arch':
                subprocess.run(['pacman', '-Rs', '--noconfirm', 'wireguard-tools', 'qrencode'])

            print(f"{Colors.GREEN}WireGuard uninstalled successfully{Colors.NC}")
        except Exception as e:
            print(f"{Colors.RED}Error uninstalling WireGuard: {e}{Colors.NC}")

def main():
    installer = WireGuardInstaller()
    
    # Check initial requirements
    installer.is_root()
    installer.check_virtualization()
    
    # Check if WireGuard is already installed
    if os.path.exists('/etc/wireguard/params'):
        while True:
            print("\nWireGuard Management Menu:")
            print("1) Add new client")
            print("2) List all clients")
            print("3) Remove client")
            print("4) Uninstall WireGuard")
            print("5) Exit")
            
            choice = input("\nSelect an option [1-5]: ").strip()
            
            if choice == '1':
                client_name = input("Enter client name: ").strip()
                client_info = installer.add_client(client_name)
                if client_info:
                    print(f"\n{Colors.GREEN}Client {client_name} added successfully{Colors.NC}")
                    print(f"Config file: {client_info['config_file']}")
            
            elif choice == '2':
                clients = installer.list_clients()
                if clients:
                    print("\nExisting clients:")
                    for i, client in enumerate(clients, 1):
                        print(f"{i}) {client}")
                else:
                    print("\nNo clients found")
            
            elif choice == '3':
                clients = installer.list_clients()
                if not clients:
                    print("\nNo clients found")
                    continue
                
                print("\nExisting clients:")
                for i, client in enumerate(clients, 1):
                    print(f"{i}) {client}")
                
                try:
                    idx = int(input("\nSelect client to remove [1-{}]: ".format(len(clients))))
                    if 1 <= idx <= len(clients):
                        client_name = clients[idx-1]
                        if installer.remove_client(client_name):
                            print(f"\n{Colors.GREEN}Client {client_name} removed successfully{Colors.NC}")
                        else:
                            print(f"\n{Colors.RED}Failed to remove client {client_name}{Colors.NC}")
                except ValueError:
                    print("\nInvalid selection")
            
            elif choice == '4':
                confirm = input("\nAre you sure you want to uninstall WireGuard? [y/N]: ").strip().lower()
                if confirm == 'y':
                    installer.uninstall()
                    break
            
            elif choice == '5':
                break
    
    else:
        # New installation
        print("Welcome to WireGuard installer!")
        print("\nInstalling WireGuard...")
        
        installer.install_wireguard()
        installer.setup_server()
        
        print(f"\n{Colors.GREEN}WireGuard installed successfully!{Colors.NC}")
        
        # Add first client
        client_name = input("\nEnter name for first client: ").strip()
        client_info = installer.add_client(client_name)
        if client_info:
            print(f"\n{Colors.GREEN}Client {client_name} added successfully{Colors.NC}")
            print(f"Config file: {client_info['config_file']}")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
