from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import json
import ipaddress
import os

app = FastAPI(title="WireGuard Management API")

# WireGuard Configuration
WG_INTERFACE = "wg0"
WG_CONFIG_PATH = "/etc/wireguard/wg0.conf"
SERVER_ENDPOINT = "202.78.171.138:51820"  # Change this to your server's endpoint
SERVER_PUBLIC_KEY = "PdUemN7y+NaYOte2vWl46bvnDjTEvaYhKPqEI56Ziyg="  # Your server's public key

# Models
class PeerCreate(BaseModel):
    allowed_ip: Optional[str] = None

class PeerInfo(BaseModel):
    public_key: str
    endpoint: Optional[str]
    allowed_ips: str
    latest_handshake: str
    transfer_rx: str
    transfer_tx: str
    status: str

# Helper Functions
def run_wg_command(command: List[str]) -> str:
    """Execute WireGuard command and return output"""
    try:
        result = subprocess.run(
            ["wg"] + command,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise HTTPException(
            status_code=500,
            detail=f"WireGuard command failed: {e.stderr}"
        )

def get_next_available_ip() -> str:
    """Find next available IP from WireGuard peers"""
    try:
        # Get current peers
        output = run_wg_command(["show", WG_INTERFACE, "dump"])
        used_ips = set()
        
        # Collect used IPs
        for line in output.splitlines():
            parts = line.split('\t')
            if len(parts) >= 4 and parts[3]:
                ips = parts[3].split(',')
                for ip in ips:
                    if ip and '/32' in ip:
                        used_ips.add(ip.split('/')[0])
        
        # Find next available IP
        for i in range(2, 255):
            ip = f"10.66.66.{i}"
            if ip not in used_ips:
                return ip
                
        raise HTTPException(
            status_code=507,
            detail="No available IP addresses"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

# API Endpoints
@app.post("/peers", response_model=dict)
async def create_peer(peer: PeerCreate):
    """Create a new WireGuard peer"""
    try:
        # Generate private key
        private_key = run_wg_command(["genkey"])
        
        # Generate public key
        public_key = subprocess.run(
            ["wg", "pubkey"],
            input=private_key,
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()
        
        # Get or assign IP
        allowed_ip = peer.allowed_ip or get_next_available_ip()
        
        # Add peer to WireGuard
        run_wg_command([
            "set", WG_INTERFACE,
            "peer", public_key,
            "allowed-ips", f"{allowed_ip}/32"
        ])
        
        # Generate client config
        client_config = f"""[Interface]
PrivateKey = {private_key}
Address = {allowed_ip}/32
DNS = 8.8.8.8

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {SERVER_ENDPOINT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"""

        return {
            "public_key": public_key,
            "private_key": private_key,
            "allowed_ip": allowed_ip,
            "config": client_config
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/peers", response_model=List[PeerInfo])
async def list_peers():
    """List all WireGuard peers"""
    try:
        output = run_wg_command(["show", WG_INTERFACE, "dump"])
        peers = []
        
        for line in output.splitlines():
            parts = line.split('\t')
            if len(parts) >= 7:
                peer = PeerInfo(
                    public_key=parts[0],
                    endpoint=parts[2] if parts[2] != "(none)" else None,
                    allowed_ips=parts[3] if parts[3] != "(none)" else "",
                    latest_handshake=parts[4],
                    transfer_rx=parts[5],
                    transfer_tx=parts[6],
                    status="active" if parts[4] != "0" else "inactive"
                )
                peers.append(peer)
                
        return peers
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.delete("/peers/{public_key}")
async def remove_peer(public_key: str):
    """Remove a WireGuard peer"""
    try:
        run_wg_command([
            "set", WG_INTERFACE,
            "peer", public_key,
            "remove"
        ])
        return {"status": "success", "message": "Peer removed"}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.put("/peers/{public_key}/disable")
async def disable_peer(public_key: str):
    """Disable a peer by removing its allowed IPs"""
    try:
        run_wg_command([
            "set", WG_INTERFACE,
            "peer", public_key,
            "allowed-ips", ""
        ])
        return {"status": "success", "message": "Peer disabled"}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.put("/peers/{public_key}/enable")
async def enable_peer(public_key: str, allowed_ip: str):
    """Re-enable a peer by setting its allowed IPs"""
    try:
        run_wg_command([
            "set", WG_INTERFACE,
            "peer", public_key,
            "allowed-ips", f"{allowed_ip}/32"
        ])
        return {"status": "success", "message": "Peer enabled"}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

@app.get("/status")
async def get_status():
    """Get WireGuard interface status"""
    try:
        status = run_wg_command(["show", WG_INTERFACE])
        return {"status": status}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        # ssl_keyfile="key.pem",  # Uncomment for HTTPS
        # ssl_certfile="cert.pem"  # Uncomment for HTTPS
    )
