#!/usr/bin/env python3
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
import os
from typing import Dict, List, Optional, Tuple
import logging
from pathlib import Path

class WireGuardQuotaManager:
    def __init__(self, interface: str = "wg0", db_path: str = "/etc/wireguard/quotas.db"):
        self.interface = interface
        self.db_path = db_path
        self.setup_database()
        self.setup_logging()

    def setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filename='/var/log/wireguard-quota.log'
        )
        self.logger = logging.getLogger('WireGuardQuota')

    def setup_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create tables if they don't exist
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT UNIQUE,
                name TEXT UNIQUE,
                data_quota BIGINT,          -- Bytes
                time_quota INTEGER,         -- Days
                data_used BIGINT DEFAULT 0,
                start_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                bytes_received BIGINT,
                bytes_sent BIGINT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients (id)
            );
        ''')
        conn.commit()
        conn.close()

    def add_client(self, name: str, public_key: str, data_quota: Optional[int] = None,
                  time_quota: Optional[int] = None) -> bool:
        """
        Add a new client with quotas
        
        Args:
            name: Client name
            public_key: WireGuard public key
            data_quota: Data quota in bytes (None for unlimited)
            time_quota: Time quota in days (None for unlimited)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            start_date = datetime.now().isoformat()
            end_date = None
            if time_quota:
                end_date = (datetime.now() + timedelta(days=time_quota)).isoformat()

            cursor.execute('''
                INSERT INTO clients (name, public_key, data_quota, time_quota, 
                                   start_date, end_date)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, public_key, data_quota, time_quota, start_date, end_date))
            
            conn.commit()
            conn.close()
            self.logger.info(f"Added client {name} with quotas: data={data_quota}B, time={time_quota}days")
            return True
        except Exception as e:
            self.logger.error(f"Error adding client: {e}")
            return False

    def update_quota(self, client_name: str, data_quota: Optional[int] = None,
                    time_quota: Optional[int] = None) -> bool:
        """Update client quotas"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            updates = []
            values = []
            if data_quota is not None:
                updates.append("data_quota = ?")
                values.append(data_quota)
            if time_quota is not None:
                updates.append("time_quota = ?")
                updates.append("end_date = ?")
                values.extend([time_quota, 
                             (datetime.now() + timedelta(days=time_quota)).isoformat()])
            
            if not updates:
                return False
                
            values.append(client_name)
            cursor.execute(f'''
                UPDATE clients 
                SET {", ".join(updates)}
                WHERE name = ?
            ''', values)
            
            conn.commit()
            conn.close()
            self.logger.info(f"Updated quotas for {client_name}")
            return True
        except Exception as e:
            self.logger.error(f"Error updating quota: {e}")
            return False

    def get_client_usage(self, client_name: str) -> Dict:
        """Get client's current usage statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT data_quota, time_quota, data_used, start_date, end_date, is_active
                FROM clients
                WHERE name = ?
            ''', (client_name,))
            
            result = cursor.fetchone()
            if not result:
                return {}
                
            return {
                'data_quota': result[0],
                'time_quota': result[1],
                'data_used': result[2],
                'start_date': result[3],
                'end_date': result[4],
                'is_active': bool(result[5]),
                'data_remaining': result[0] - result[2] if result[0] else None,
                'days_remaining': (datetime.fromisoformat(result[4]) - datetime.now()).days 
                                if result[4] else None
            }
        except Exception as e:
            self.logger.error(f"Error getting usage: {e}")
            return {}

    def update_usage(self):
        """Update usage statistics for all clients"""
        try:
            # Get WireGuard statistics
            wg_output = subprocess.check_output(['wg', 'show', self.interface, 'transfer']).decode()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for line in wg_output.strip().split('\n'):
                public_key, received, sent = line.split('\t')
                received = int(received)
                sent = int(sent)
                total_bytes = received + sent
                
                # Update client usage
                cursor.execute('''
                    UPDATE clients 
                    SET data_used = ?
                    WHERE public_key = ?
                ''', (total_bytes, public_key))
                
                # Log usage
                cursor.execute('''
                    INSERT INTO usage_logs (client_id, bytes_received, bytes_sent)
                    SELECT id, ?, ?
                    FROM clients
                    WHERE public_key = ?
                ''', (received, sent, public_key))
            
            conn.commit()
            
            # Check quotas and deactivate if exceeded
            self._check_quotas(cursor)
            
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error updating usage: {e}")

    def _check_quotas(self, cursor):
        """Check and enforce quotas"""
        now = datetime.now()
        
        # Find clients exceeding quotas
        cursor.execute('''
            SELECT id, name, data_quota, data_used, end_date
            FROM clients
            WHERE is_active = 1
            AND (
                (data_quota IS NOT NULL AND data_used >= data_quota)
                OR
                (end_date IS NOT NULL AND end_date <= ?)
            )
        ''', (now.isoformat(),))
        
        for client in cursor.fetchall():
            client_id, name = client[0], client[1]
            
            # Deactivate client
            cursor.execute('''
                UPDATE clients
                SET is_active = 0
                WHERE id = ?
            ''', (client_id,))
            
            # Remove from WireGuard configuration
            self._deactivate_client_wg(name)
            
            self.logger.info(f"Deactivated client {name} - quota exceeded")

    def _deactivate_client_wg(self, client_name: str):
        """Remove client from WireGuard configuration"""
        try:
            config_file = f"/etc/wireguard/{self.interface}.conf"
            with open(config_file, 'r') as f:
                lines = f.readlines()
            
            # Find and remove client section
            new_lines = []
            skip = False
            for line in lines:
                if f"### Client {client_name}" in line:
                    skip = True
                    continue
                if skip and line.strip() == "":
                    skip = False
                    continue
                if not skip:
                    new_lines.append(line)
            
            # Write updated config
            with open(config_file, 'w') as f:
                f.writelines(new_lines)
            
            # Apply changes
            subprocess.run(['wg', 'syncconf', self.interface, config_file])
        except Exception as e:
            self.logger.error(f"Error deactivating client in WireGuard: {e}")

    def get_all_clients(self) -> List[Dict]:
        """Get list of all clients and their usage"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT name, data_quota, time_quota, data_used, 
                       start_date, end_date, is_active
                FROM clients
            ''')
            
            clients = []
            for row in cursor.fetchall():
                clients.append({
                    'name': row[0],
                    'data_quota': row[1],
                    'time_quota': row[2],
                    'data_used': row[3],
                    'start_date': row[4],
                    'end_date': row[5],
                    'is_active': bool(row[6])
                })
            
            conn.close()
            return clients
        except Exception as e:
            self.logger.error(f"Error getting clients: {e}")
            return []

def format_bytes(bytes: int) -> str:
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes < 1024:
            return f"{bytes:.2f}{unit}"
        bytes /= 1024
    return f"{bytes:.2f}PB"

# Example usage:
if __name__ == "__main__":
    quota_manager = WireGuardQuotaManager()
    
    # Add a client with 10GB data quota and 30 days time quota
    quota_manager.add_client(
        name="test_client",
        public_key="client_public_key",
        data_quota=10 * 1024 * 1024 * 1024,  # 10GB
        time_quota=30  # 30 days
    )
    
    # Update usage statistics
    quota_manager.update_usage()
    
    # Get client usage
    usage = quota_manager.get_client_usage("test_client")
    if usage:
        print(f"Data used: {format_bytes(usage['data_used'])}")
        print(f"Data remaining: {format_bytes(usage['data_remaining'])}")
        print(f"Days remaining: {usage['days_remaining']}")
