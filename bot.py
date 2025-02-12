#!/usr/bin/env python3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters
)
import logging
import json
from datetime import datetime
from wireguard_quota import WireGuardQuotaManager
import qrcode
from io import BytesIO

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
SELECTING_ACTION = 0
ADDING_CLIENT = 1
SETTING_QUOTA = 2
CONFIRMING = 3

class WireGuardBot:
    def __init__(self, token: str, admin_ids: list):
        """
        Initialize bot with token and admin user IDs
        
        Args:
            token: Telegram bot token
            admin_ids: List of Telegram user IDs that can access admin functions
        """
        self.token = token
        self.admin_ids = admin_ids
        self.quota_manager = WireGuardQuotaManager()
        
        # Store temporary user data
        self.user_data = {}

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start command handler"""
        user_id = update.effective_user.id
        
        if user_id not in self.admin_ids:
            await update.message.reply_text("شما دسترسی به این ربات را ندارید.")
            return ConversationHandler.END
            
        keyboard = [
            [
                InlineKeyboardButton("👤 اضافه کردن کاربر", callback_data='add_client'),
                InlineKeyboardButton("📊 مشاهده کاربران", callback_data='list_clients')
            ],
            [
                InlineKeyboardButton("⚙️ تنظیمات", callback_data='settings'),
                InlineKeyboardButton("❌ خروج", callback_data='exit')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            'به ربات مدیریت WireGuard خوش آمدید!\n'
            'لطفا یک گزینه را انتخاب کنید:',
            reply_markup=reply_markup
        )
        
        return SELECTING_ACTION

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle button presses"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'exit':
            await query.edit_message_text('خدانگهدار!')
            return ConversationHandler.END
            
        elif query.data == 'add_client':
            await query.edit_message_text(
                'لطفا نام کاربر جدید را وارد کنید:'
            )
            return ADDING_CLIENT
            
        elif query.data == 'list_clients':
            clients = self.quota_manager.get_all_clients()
            if not clients:
                await query.edit_message_text(
                    'هیچ کاربری وجود ندارد.',
                    reply_markup=self._get_main_menu_keyboard()
                )
                return SELECTING_ACTION
                
            message = "📊 لیست کاربران:\n\n"
            for client in clients:
                status = "✅ فعال" if client['is_active'] else "❌ غیرفعال"
                data_used = self._format_bytes(client['data_used'])
                data_quota = self._format_bytes(client['data_quota']) if client['data_quota'] else "نامحدود"
                
                message += f"👤 {client['name']}\n"
                message += f"وضعیت: {status}\n"
                message += f"حجم مصرفی: {data_used}\n"
                message += f"حجم کل: {data_quota}\n"
                if client['end_date']:
                    remaining = datetime.fromisoformat(client['end_date']) - datetime.now()
                    message += f"زمان باقیمانده: {remaining.days} روز\n"
                message += "〰️〰️〰️〰️〰️〰️\n"
            
            await query.edit_message_text(
                message,
                reply_markup=self._get_main_menu_keyboard()
            )
            return SELECTING_ACTION

    async def add_client_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle client name input"""
        name = update.message.text
        self.user_data[update.effective_user.id] = {'name': name}
        
        keyboard = [
            [
                InlineKeyboardButton("1GB", callback_data='quota_1'),
                InlineKeyboardButton("5GB", callback_data='quota_5'),
                InlineKeyboardButton("10GB", callback_data='quota_10')
            ],
            [
                InlineKeyboardButton("50GB", callback_data='quota_50'),
                InlineKeyboardButton("100GB", callback_data='quota_100'),
                InlineKeyboardButton("نامحدود", callback_data='quota_unlimited')
            ],
            [
                InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_main')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            'لطفا حجم مورد نظر را انتخاب کنید:',
            reply_markup=reply_markup
        )
        return SETTING_QUOTA

    async def set_quota(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle quota selection"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'back_to_main':
            await self._show_main_menu(query)
            return SELECTING_ACTION
            
        user_data = self.user_data.get(update.effective_user.id, {})
        quota = query.data.split('_')[1]
        
        if quota == 'unlimited':
            user_data['quota'] = None
        else:
            user_data['quota'] = int(quota) * 1024 * 1024 * 1024  # Convert GB to bytes
            
        self.user_data[update.effective_user.id] = user_data
        
        keyboard = [
            [
                InlineKeyboardButton("30 روز", callback_data='time_30'),
                InlineKeyboardButton("60 روز", callback_data='time_60'),
                InlineKeyboardButton("90 روز", callback_data='time_90')
            ],
            [
                InlineKeyboardButton("180 روز", callback_data='time_180'),
                InlineKeyboardButton("365 روز", callback_data='time_365'),
                InlineKeyboardButton("نامحدود", callback_data='time_unlimited')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            'لطفا مدت زمان اشتراک را انتخاب کنید:',
            reply_markup=reply_markup
        )
        return CONFIRMING

    async def confirm_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Create the client and generate config"""
        query = update.callback_query
        await query.answer()
        
        user_data = self.user_data.get(update.effective_user.id, {})
        time_period = query.data.split('_')[1]
        
        if time_period == 'unlimited':
            user_data['time'] = None
        else:
            user_data['time'] = int(time_period)
            
        # Generate keys and add client
        keys = self._generate_wireguard_keys()
        success, message = self.quota_manager.add_client(
            name=user_data['name'],
            public_key=keys['public'],
            data_quota=user_data['quota'],
            time_quota=user_data['time']
        )
        
        if not success:
            await query.message.reply_text(f"❌ خطا: {message}")
            await self._show_main_menu(query)
            return SELECTING_ACTION
        
        # Generate config
        config = self._generate_client_config(
            user_data['name'],
            keys['private'],
            keys['public']
        )
        
        # Generate QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(config)
        qr.make(fit=True)
        
        # Save QR code to buffer
        buffer = BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buffer, 'PNG')
        buffer.seek(0)
        
        # Send config and QR code
        await query.message.reply_document(
            document=buffer,
            filename=f"{user_data['name']}_config.png",
            caption=f"🎉 کاربر {user_data['name']} با موفقیت ایجاد شد!\n\n"
                    f"تنظیمات کانفیگ به صورت QR code ارسال شد."
        )
        
        # Send text config
        await query.message.reply_text(
            f"```\n{config}\n```",
            parse_mode='MarkdownV2'
        )
        
        # Clean up and return to main menu
        del self.user_data[update.effective_user.id]
        await self._show_main_menu(query)
        return SELECTING_ACTION

    def _generate_wireguard_keys(self) -> dict:
        """Generate WireGuard key pair"""
        import subprocess
        
        private = subprocess.check_output(['wg', 'genkey']).decode().strip()
        public = subprocess.check_output(['wg', 'pubkey'], 
                                      input=private.encode()).decode().strip()
        
        return {
            'private': private,
            'public': public
        }

    def _generate_client_config(self, name: str, private_key: str, public_key: str) -> str:
        """Generate client configuration"""
        # Get next available IP
        next_ip = self._get_next_available_ip()
        
        # Load server config from config.json
        with open('config.json') as f:
            config = json.load(f)
        
        server_config = config['server_settings']
        
        config = f"""[Interface]
PrivateKey = {private_key}
Address = {next_ip}/24
DNS = {server_config['dns']}

[Peer]
PublicKey = {server_config['public_key']}
Endpoint = {config['server_endpoint']}
AllowedIPs = {server_config['allowed_ips']}
PersistentKeepalive = 25
"""
        return config

    def _get_next_available_ip(self) -> str:
        """Get next available IP from subnet"""
        with open('config.json') as f:
            config = json.load(f)
        
        base_ip = config['server_settings']['subnet'].split('.')[0:3]
        base_ip = '.'.join(base_ip)
        
        # Check existing IPs
        used_ips = []
        with open(f"/etc/wireguard/{self.interface}.conf", 'r') as f:
            content = f.read()
            used_ips = re.findall(r'Address = (\d+\.\d+\.\d+\.\d+)', content)
        
        # Find next available IP
        for i in range(2, 255):
            candidate_ip = f"{base_ip}.{i}"
            if candidate_ip not in used_ips:
                return candidate_ip
                
        raise Exception("No available IPs in subnet")

    def _get_server_public_key(self) -> str:
        """Get server's public key"""
        # این بخش باید با توجه به تنظیمات سرور شما تکمیل شود
        return "server_public_key_here"

    def _format_bytes(self, bytes_value: int) -> str:
        """Convert bytes to human readable format"""
        if bytes_value is None:
            return "نامحدود"
            
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_value < 1024:
                return f"{bytes_value:.2f} {unit}"
            bytes_value /= 1024
        return f"{bytes_value:.2f} TB"

    def _get_main_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Get main menu keyboard"""
        keyboard = [
            [
                InlineKeyboardButton("👤 اضافه کردن کاربر", callback_data='add_client'),
                InlineKeyboardButton("📊 مشاهده کاربران", callback_data='list_clients')
            ],
            [
                InlineKeyboardButton("⚙️ تنظیمات", callback_data='settings'),
                InlineKeyboardButton("❌ خروج", callback_data='exit')
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def _show_main_menu(self, query):
        """Show main menu"""
        await query.edit_message_text(
            'لطفا یک گزینه را انتخاب کنید:',
            reply_markup=self._get_main_menu_keyboard()
        )

    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.token).build()
        
        # Add conversation handler
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start)],
            states={
                SELECTING_ACTION: [
                    CallbackQueryHandler(self.button_handler)
                ],
                ADDING_CLIENT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_client_name)
                ],
                SETTING_QUOTA: [
                    CallbackQueryHandler(self.set_quota)
                ],
                CONFIRMING: [
                    CallbackQueryHandler(self.confirm_creation)
                ]
            },
            fallbacks=[],
        )
        
        application.add_handler(conv_handler)
        
        # Start the bot
        application.run_polling()

# Example usage:
if __name__ == '__main__':
    # Load config
    with open('config.json') as f:
        config = json.load(f)
    
    # Initialize and run bot
    bot = WireGuardBot(
        token=config['telegram_token'],
        admin_ids=config['admin_ids']
    )
    bot.run()
