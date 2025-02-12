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
        """تنظیم لاگ‌ها"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filename='/var/log/wireguard-quota.log'
        )
        self.logger = logging.getLogger('WireGuardQuota')

    def setup_database(self):
        """ایجاد دیتابیس و جداول مورد نیاز"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.executescript('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_key TEXT UNIQUE,         -- کلید عمومی کلاینت
                name TEXT UNIQUE,              -- نام کلاینت
                data_quota BIGINT,             -- حجم کل (بایت)
                time_quota INTEGER,            -- زمان کل (روز)
                data_used BIGINT DEFAULT 0,    -- حجم مصرف شده
                start_date TEXT,               -- تاریخ شروع
                end_date TEXT,                 -- تاریخ پایان
                is_active INTEGER DEFAULT 1     -- وضعیت فعال/غیرفعال
            );

            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,              -- شناسه کلاینت
                bytes_received BIGINT,          -- بایت‌های دریافتی
                bytes_sent BIGINT,              -- بایت‌های ارسالی
                timestamp TEXT,                 -- زمان ثبت
                FOREIGN KEY (client_id) REFERENCES clients (id)
            );
        ''')
        conn.commit()
        conn.close()

    def add_client(self, name, public_key, data_quota=None, time_quota=None):
        """
        افزودن کلاینت جدید
        
        Args:
            name: نام کلاینت
            public_key: کلید عمومی
            data_quota: حجم به بایت (None برای نامحدود)
            time_quota: زمان به روز (None برای نامحدود)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
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
            self.logger.info(f"کلاینت {name} اضافه شد")
            return True
        except Exception as e:
            self.logger.error(f"خطا در افزودن کلاینت: {e}")
            return False

    def update_usage(self):
        """به‌روزرسانی آمار مصرف همه کلاینت‌ها"""
        try:
            # دریافت آمار از WireGuard
            wg_output = subprocess.check_output(
                ['wg', 'show', self.interface, 'transfer']
            ).decode()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for line in wg_output.strip().split('\n'):
                public_key, received, sent = line.split('\t')
                received = int(received)
                sent = int(sent)
                total_bytes = received + sent
                
                # به‌روزرسانی مصرف کلاینت
                cursor.execute('''
                    UPDATE clients 
                    SET data_used = ?
                    WHERE public_key = ?
                ''', (total_bytes, public_key))
                
                # ثبت در لاگ
                cursor.execute('''
                    INSERT INTO usage_logs (client_id, bytes_received, bytes_sent)
                    SELECT id, ?, ?
                    FROM clients
                    WHERE public_key = ?
                ''', (received, sent, public_key))
            
            conn.commit()
            
            # بررسی محدودیت‌ها
            self._check_quotas(cursor)
            
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"خطا در به‌روزرسانی آمار: {e}")

    def _check_quotas(self, cursor):
        """بررسی و اعمال محدودیت‌های حجم و زمان"""
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
            
            # غیرفعال کردن کلاینت
            cursor.execute('''
                UPDATE clients
                SET is_active = 0
                WHERE id = ?
            ''', (client_id,))
            
            # حذف از پیکربندی WireGuard
            self._deactivate_client_wg(name)
            
            self.logger.info(f"کلاینت {name} به دلیل اتمام حجم/زمان غیرفعال شد")

    def _deactivate_client_wg(self, client_name):
        """غیرفعال کردن کلاینت در WireGuard"""
        config_file = f"/etc/wireguard/{self.interface}.conf"
        try:
            with open(config_file, 'r') as f:
                lines = f.readlines()
            
            # یافتن و حذف بخش مربوط به کلاینت
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
            
            # ذخیره تغییرات
            with open(config_file, 'w') as f:
                f.writelines(new_lines)
            
            # اعمال تغییرات
            subprocess.run(['wg', 'syncconf', self.interface, config_file])
        except Exception as e:
            self.logger.error(f"خطا در غیرفعال‌سازی کلاینت: {e}")

    def get_client_usage(self, client_name):
        """دریافت آمار مصرف یک کلاینت"""
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
            self.logger.error(f"خطا در دریافت آمار: {e}")
            return {}

    def get_all_clients(self):
        """دریافت لیست تمام کلاینت‌ها"""
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
            self.logger.error(f"خطا در دریافت لیست کلاینت‌ها: {e}")
            return []
