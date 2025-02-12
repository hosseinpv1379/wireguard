#!/usr/bin/env python3
import sqlite3
import subprocess
from datetime import datetime, timedelta
import os
import logging

class WireGuardQuotaManager:
    def __init__(self, interface="wg0", db_path="/etc/wireguard/quotas.db"):
        self.interface = interface
        self.db_path = db_path
        self.setup_database()
        self.setup_logging()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filename='/var/log/wireguard-quota.log'
        )
        self.logger = logging.getLogger('WireGuardQuota')

    def setup_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT UNIQUE,
                name TEXT UNIQUE,
                data_quota BIGINT,
                time_quota INTEGER,
                data_used BIGINT DEFAULT 0,
                start_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1
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

    def add_client(self, name, public_key, data_quota=None, time_quota=None):
        """Add a new client with quotas"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if client exists
            cursor.execute('SELECT name FROM clients WHERE name = ?', (name,))
            if cursor.fetchone():
                conn.close()
                return False, f"Client {name} already exists"
                
            cursor.execute('SELECT public_key FROM clients WHERE public_key = ?', (public_key,))
            if cursor.fetchone():
                conn.close()
                return False, "Public key is already in use"
            
            start_date = datetime.now().isoformat()
            end_date = None
            if time_quota:
                end_date = (datetime.now() + timedelta(days=time_quota)).isoformat()

            cursor.execute('''
                INSERT INTO clients (
                    name, public_key, data_quota, time_quota, 
                    start_date, end_date
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (name, public_key, data_quota, time_quota, start_date, end_date))
            
            conn.commit()
            conn.close()
            self.logger.info(f"Added client {name}")
            return True, "Client added successfully"
            
        except Exception as e:
            self.logger.error(f"Error adding client: {e}")
            return False, str(e)

    def update_usage(self):
        try:
            wg_output = subprocess.check_output(['wg', 'show', self.interface, 'transfer']).decode()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for line in wg_output.strip().split('\n'):
                if not line:
                    continue
                public_key, received, sent = line.split('\t')
                received = int(received)
                sent = int(sent)
                total_bytes = received + sent
                
                cursor.execute('''
                    UPDATE clients 
                    SET data_used = ?
                    WHERE public_key = ?
                ''', (total_bytes, public_key))
                
                cursor.execute('''
                    INSERT INTO usage_logs (client_id, bytes_received, bytes_sent)
                    SELECT id, ?, ?
                    FROM clients
                    WHERE public_key = ?
                ''', (received, sent, public_key))
            
            conn.commit()
            self._check_quotas(cursor)
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Error updating usage: {e}")

    def _check_quotas(self, cursor):
        now = datetime.now()
        
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
            cursor.execute('UPDATE clients SET is_active = 0 WHERE id = ?', (client_id,))
            self._deactivate_client_wg(name)
            self.logger.info(f"Deactivated client {name} - quota exceeded")

    def _deactivate_client_wg(self, client_name):
        try:
            config_file = f"/etc/wireguard/{self.interface}.conf"
            with open(config_file, 'r') as f:
                lines = f.readlines()
            
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
            
            with open(config_file, 'w') as f:
                f.writelines(new_lines)
            
            subprocess.run(['wg', 'syncconf', self.interface, config_file])
        except Exception as e:
            self.logger.error(f"Error deactivating client in WireGuard: {e}")

    def get_client_usage(self, client_name):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT data_quota, time_quota, data_used, 
                       start_date, end_date, is_active
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
                'days_remaining': (
                    datetime.fromisoformat(result[4]) - datetime.now()
                ).days if result[4] else None
            }
        except Exception as e:
            self.logger.error(f"Error getting usage: {e}")
            return {}

    def get_all_clients(self):
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
