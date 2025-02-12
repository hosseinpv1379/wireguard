from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
import subprocess
import os
import json
from typing import List, Optional

app = FastAPI()

# Security
API_KEY = os.getenv("WG_API_KEY", "your-secure-api-key")
api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key

# Models
class PeerCreate(BaseModel):
    allowed_ip: Optional[str] = None  # اگر خالی باشد، سیستم خودش IP میده

class PeerStatus(BaseModel):
    public_key: str
    allowed_ips: str
    latest_handshake: str
    transfer_rx: str
    transfer_tx: str
    is_active: bool

# Helper Functions
def run_wg_command(command: List[str]) -> str:
    try:
        result = subprocess.run(["wg"] + command, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"WireGuard command failed: {e.stderr}")

def generate_ip_address() -> str:
    # این تابع باید IP خالی پیدا کنه
    # فعلا یک نمونه ساده:
    used_ips = get_used_ips()
    for i in range(2, 255):
        ip = f"10.66.66.{i}"
        if ip not in used_ips:
            return ip
    raise HTTPException(status_code=507, detail="No IP addresses available")

def get_used_ips() -> List[str]:
    peers = run_wg_command(["show", "wg0", "dump"])
    used_ips = []
    for line in peers.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) >= 4:
            used_ips.extend(parts[3].split(','))
    return used_ips

# API Endpoints
@app.get("/status")
async def get_status():
    """وضعیت فعلی وایرگارد"""
    status = run_wg_command(["show"])
    return {"status": status}

@app.get("/peers")
async def list_peers() -> List[PeerStatus]:
    """لیست تمام پیرهای فعال"""
    output = run_wg_command(["show", "wg0", "dump"])
    peers = []
    for line in output.splitlines()[1:]:  # Skip header line
        parts = line.split('\t')
        if len(parts) >= 7:
            peers.append(PeerStatus(
                public_key=parts[0],
                allowed_ips=parts[3],
                latest_handshake=parts[4],
                transfer_rx=parts[5],
                transfer_tx=parts[6],
                is_active=bool(parts[4])  # اگر handshake داشته باشه یعنی فعاله
            ))
    return peers

@app.post("/peers", dependencies=[Depends(verify_api_key)])
async def create_peer(peer: PeerCreate):
    """ایجاد پیر جدید"""
    # Generate keys
    private_key = run_wg_command(["genkey"])
    public_key = subprocess.run(["wg", "pubkey"], 
                              input=private_key, 
                              capture_output=True, 
                              text=True, 
                              check=True).stdout.strip()
    
    # Get or generate IP
    allowed_ip = peer.allowed_ip or generate_ip_address()
    
    # Add peer
    run_wg_command([
        "set", "wg0",
        "peer", public_key,
        "allowed-ips", f"{allowed_ip}/32"
    ])
    
    return {
        "public_key": public_key,
        "private_key": private_key,
        "allowed_ip": allowed_ip
    }

@app.delete("/peers/{public_key}", dependencies=[Depends(verify_api_key)])
async def remove_peer(public_key: str):
    """حذف یک پیر"""
    run_wg_command([
        "set", "wg0",
        "peer", public_key,
        "remove"
    ])
    return {"status": "removed"}

@app.post("/peers/{public_key}/disable", dependencies=[Depends(verify_api_key)])
async def disable_peer(public_key: str):
    """غیرفعال کردن یک پیر"""
    # Find peer's allowed IPs
    peers = run_wg_command(["show", "wg0", "dump"])
    allowed_ips = None
    for line in peers.splitlines()[1:]:
        parts = line.split('\t')
        if parts[0] == public_key:
            allowed_ips = parts[3]
            break
    
    if allowed_ips:
        # Remove and re-add peer with no allowed IPs
        run_wg_command([
            "set", "wg0",
            "peer", public_key,
            "remove"
        ])
        run_wg_command([
            "set", "wg0",
            "peer", public_key,
            "allowed-ips", ""  # خالی یعنی هیچ ترافیکی نمیتونه رد و بدل کنه
        ])
        return {"status": "disabled"}
    else:
        raise HTTPException(status_code=404, detail="Peer not found")

@app.post("/peers/{public_key}/enable", dependencies=[Depends(verify_api_key)])
async def enable_peer(public_key: str):
    """فعال کردن مجدد یک پیر"""
    # Find peer's original allowed IPs from config
    # این قسمت باید با روش مناسب پیاده سازی شود
    # فعلا فرض میکنیم IP رو از قبل داریم
    allowed_ip = "10.66.66.x"  # باید از جای مناسب خونده شود
    
    run_wg_command([
        "set", "wg0",
        "peer", public_key,
        "allowed-ips", f"{allowed_ip}/32"
    ])
    return {"status": "enabled"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
