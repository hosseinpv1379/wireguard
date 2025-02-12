from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import subprocess

app = FastAPI()

# تنظیمات وایرگارد
WG_ENDPOINT = "202.78.171.138:51820"  # آدرس و پورت سرور شما
SERVER_PUBLIC_KEY = "PdUemN7y+NaYOte2vWl46bvnDjTEvaYhKPqEI56Ziyg="  # کلید عمومی سرور

class PeerCreate(BaseModel):
    allowed_ip: Optional[str] = None

@app.post("/peers")
async def create_peer(peer: PeerCreate = None):
    """ایجاد پیر جدید"""
    try:
        # Generate keys
        private_key = subprocess.run(["wg", "genkey"], 
                                   capture_output=True, 
                                   text=True, 
                                   check=True).stdout.strip()
        
        public_key = subprocess.run(["wg", "pubkey"], 
                                  input=private_key, 
                                  capture_output=True, 
                                  text=True, 
                                  check=True).stdout.strip()
        
        # تخصیص IP (فعلا ثابت برای تست)
        allowed_ip = peer.allowed_ip or "10.66.66.2"
        
        # Add peer
        subprocess.run([
            "wg", 
            "set", "wg0",
            "peer", public_key,
            "allowed-ips", f"{allowed_ip}/32"
        ], check=True)
        
        # ساخت کانفیگ کامل
        config = f"""[Interface]
PrivateKey = {private_key}
Address = {allowed_ip}/32
DNS = 8.8.8.8

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {WG_ENDPOINT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"""
        
        return {
            "public_key": public_key,
            "private_key": private_key,
            "allowed_ip": allowed_ip,
            "config": config
        }
        
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}

@app.get("/peers")
async def list_peers():
    """لیست تمام پیرها"""
    try:
        output = subprocess.run(["wg", "show", "wg0", "dump"], 
                              capture_output=True, 
                              text=True, 
                              check=True).stdout.strip()
        
        peers = []
        for line in output.splitlines():
            parts = line.split('\t')
            if len(parts) >= 4:
                peers.append({
                    "public_key": parts[0],
                    "endpoint": parts[2],
                    "allowed_ips": parts[3],
                    "latest_handshake": "active" if parts[4] != "0" else "inactive",
                    "transfer": f"↓{parts[5]} ↑{parts[6]}"
                })
        
        return peers
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}

@app.delete("/peers/{public_key}")
async def remove_peer(public_key: str):
    """حذف یک پیر"""
    try:
        subprocess.run([
            "wg",
            "set", "wg0",
            "peer", public_key,
            "remove"
        ], check=True)
        return {"status": "removed"}
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}
