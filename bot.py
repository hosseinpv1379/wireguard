import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import mysql.connector
from datetime import datetime, timedelta
import config

# Database connection
def get_db_connection():
    return mysql.connector.connect(
        host=config.DB_HOST,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME
    )

class ServiceBot:
    def __init__(self):
        self.db = get_db_connection()
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        # Check if user exists in database
        cursor = self.db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE telegram_id = %s", (user.id,))
        existing_user = cursor.fetchone()
        
        if not existing_user:
            # Register new user
            cursor.execute("""
                INSERT INTO users (telegram_id, username, first_name, last_name)
                VALUES (%s, %s, %s, %s)
            """, (user.id, user.username, user.first_name, user.last_name))
            self.db.commit()
        
        welcome_text = f"سلام {user.first_name} عزیز!\n"
        welcome_text += "به ربات فروش سرویس خوش آمدید.\n"
        welcome_text += "برای مشاهده لیست سرویس‌ها روی دکمه زیر کلیک کنید."
        
        keyboard = [
            [InlineKeyboardButton("📦 مشاهده سرویس‌ها", callback_data='show_services')],
            [InlineKeyboardButton("💰 موجودی من", callback_data='my_balance')],
            [InlineKeyboardButton("🔐 سرویس‌های من", callback_data='my_services')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        
    async def show_services(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        cursor = self.db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM service_plans WHERE is_active = TRUE")
        services = cursor.fetchall()
        
        text = "📦 لیست سرویس‌های موجود:\n\n"
        keyboard = []
        
        for service in services:
            text += f"🔸 {service['name']}\n"
            text += f"مدت: {service['duration_days']} روز\n"
            text += f"قیمت: {service['price']} تومان\n\n"
            keyboard.append([InlineKeyboardButton(
                f"خرید {service['name']}", 
                callback_data=f"buy_service_{service['id']}"
            )])
            
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)

    async def buy_service(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        service_id = int(query.data.split('_')[2])
        user_id = query.from_user.id
        
        cursor = self.db.cursor(dictionary=True)
        
        # Get service details
        cursor.execute("SELECT * FROM service_plans WHERE id = %s", (service_id,))
        service = cursor.fetchone()
        
        # Get user balance
        cursor.execute("SELECT balance FROM users WHERE telegram_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if user['balance'] < service['price']:
            await query.answer("موجودی شما کافی نیست! لطفا حساب خود را شارژ کنید.", show_alert=True)
            return
        
        # Process purchase
        try:
            # Start transaction
            cursor.execute("START TRANSACTION")
            
            # Deduct balance
            cursor.execute("""
                UPDATE users 
                SET balance = balance - %s 
                WHERE telegram_id = %s
            """, (service['price'], user_id))
            
            # Create subscription
            start_date = datetime.now()
            end_date = start_date + timedelta(days=service['duration_days'])
            
            cursor.execute("""
                INSERT INTO subscriptions (user_id, plan_id, start_date, end_date)
                SELECT id, %s, %s, %s FROM users WHERE telegram_id = %s
            """, (service_id, start_date, end_date, user_id))
            
            # Record transaction
            cursor.execute("""
                INSERT INTO transactions (user_id, amount, type, status, description)
                SELECT id, %s, 'purchase', 'completed', %s FROM users WHERE telegram_id = %s
            """, (service['price'], f"خرید سرویس {service['name']}", user_id))
            
            # Commit transaction
            self.db.commit()
            
            await query.answer("سرویس با موفقیت خریداری شد!", show_alert=True)
            
        except Exception as e:
            self.db.rollback()
            logging.error(f"Error in purchase: {str(e)}")
            await query.answer("خطا در خرید سرویس. لطفا دوباره تلاش کنید.", show_alert=True)

def main():
    # Initialize bot with your token
    bot = Application.builder().token(config.BOT_TOKEN).build()
    
    service_bot = ServiceBot()
    
    # Add handlers
    bot.add_handler(CommandHandler("start", service_bot.start_command))
    bot.add_handler(CallbackQueryHandler(service_bot.show_services, pattern='^show_services$'))
    bot.add_handler(CallbackQueryHandler(service_bot.buy_service, pattern='^buy_service_'))
    
    # Start the bot
    bot.run_polling()

if __name__ == '__main__':
    main()
