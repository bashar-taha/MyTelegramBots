import pytz
import logging
import sqlite3
from datetime import datetime
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# --- إعدادات التسجيل ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- تعريف مراحل المحادثة ---
LOCATION, NAME, PEOPLE, BOOKING_DATE, CONFIRM, TRANSFER_NUMBER = range(6)

# --- إعدادات المنطقة الزمنية ---
TIMEZONE = pytz.timezone('Asia/Damascus')

# --- تعريف سعة الأماكن ---
CAPACITY = {
    "bar": 30,
    "winter_pool": 50,
    "kids_pool": 40,
    "summer_pool": 60,
    "hall_side": 45
}

# --- إعدادات الدفع ---
MERCHANT_PHONE = "0990330431"
PRICE_PER_PERSON = 10000

# --- إعدادات قاعدة البيانات ---
DB_NAME = "bookings.db"
ADMINS_DB = "admins.db"


# --- تهيئة قواعد البيانات ---
def init_databases():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS bookings
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 payment_code TEXT UNIQUE,
                 name TEXT,
                 location TEXT,
                 people INTEGER,
                 amount INTEGER,
                 transfer_number TEXT,
                 status TEXT DEFAULT 'pending',
                 user_id INTEGER,
                 created_at TIMESTAMP,
                 booking_date TEXT)''')
    conn.commit()
    conn.close()

    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id TEXT PRIMARY KEY,
                 username TEXT,
                 full_name TEXT,
                 added_at TIMESTAMP)''')
    conn.commit()
    conn.close()


init_databases()


# --- الدوال المساعدة ---
def get_current_date():
    return datetime.now(TIMEZONE).strftime('%Y-%m-%d')


def get_current_time():
    return datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')


def is_admin(user_id):
    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    c.execute("SELECT * FROM admins WHERE user_id=?", (str(user_id),))
    result = c.fetchone()
    conn.close()
    return result is not None


def add_admin(user_id, username=None, full_name=None):
    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO admins (user_id, username, full_name, added_at) VALUES (?, ?, ?, ?)",
                  (str(user_id), username, full_name, datetime.now()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_admin_info(user_id):
    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    c.execute("SELECT username, full_name FROM admins WHERE user_id=?", (str(user_id),))
    result = c.fetchone()
    conn.close()
    return {
        "username": result[0] if result else None,
        "full_name": result[1] if result else None
    }


def remove_admin(user_id):
    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (str(user_id),))
    conn.commit()
    rows_affected = c.rowcount
    conn.close()
    return rows_affected > 0


def list_admins():
    conn = sqlite3.connect(ADMINS_DB)
    c = conn.cursor()
    c.execute("SELECT * FROM admins")
    admins = c.fetchall()
    conn.close()
    return admins


async def notify_admins_new_booking(context: ContextTypes.DEFAULT_TYPE, booking_data):
    admins = list_admins()
    if not admins:
        return

    booking_details = (
        f"📣 حجز جديد يحتاج للموافقة:\n\n"
        f"🆔 كود الحجز: <code>{booking_data['payment_code']}</code>\n"
        f"👤 الاسم: {booking_data['name']}\n"
        f"📍 الموقع: {booking_data['location']}\n"
        f"👥 عدد الأشخاص: {booking_data['people']}\n"
        f"💰 المبلغ: {booking_data['amount']:,} ل.س\n"
        f"📅 تاريخ الحجز: {booking_data['booking_date']}\n"
        f"👤 معرف المستخدم: {booking_data['user_id']}\n"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ الموافقة على الحجز", callback_data=f"approve_{booking_data['payment_code']}")]
    ])

    for admin in admins:
        try:
            await context.bot.send_message(
                chat_id=admin[0],
                text=booking_details,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"فشل في إرسال إشعار للمسؤول {admin[0]}: {str(e)}")


async def notify_user_approval(context: ContextTypes.DEFAULT_TYPE, payment_code):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, name, booking_date FROM bookings WHERE payment_code=?", (payment_code,))
    booking = c.fetchone()
    conn.close()

    if booking:
        user_id, name, booking_date = booking
        message = (
            f"🎉 تمت الموافقة على حجزك!\n\n"
            f"👤 الاسم: {name}\n"
            f"🆔 كود الحجز: <code>{payment_code}</code>\n"
            f"📅 تاريخ الحجز: {booking_date}\n\n"
            "شكراً لثقتك بنا! نتمنى لك وقتاً ممتعاً."
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message
            )
        except Exception as e:
            logger.error(f"فشل في إرسال إشعار للمستخدم {user_id}: {str(e)}")


def save_booking(data, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO bookings 
                    (payment_code, name, location, people, amount, transfer_number, user_id, created_at, booking_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (data['payment_code'], data['name'], data['location'],
                   data['people'], data['amount'], data['transfer_number'],
                   data['user_id'], datetime.now(), data['booking_date']))
        conn.commit()

        # إرسال إشعار للمسؤولين
        context.application.create_task(notify_admins_new_booking(context, data))

        return True
    except sqlite3.IntegrityError:
        logger.error("كود الدفع موجود مسبقاً")
        return False
    finally:
        conn.close()


def approve_booking(payment_code):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE bookings SET status='approved' WHERE payment_code=?", (payment_code,))
    conn.commit()
    rows_affected = c.rowcount
    conn.close()
    return rows_affected > 0


def reject_booking(payment_code, reason=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE bookings SET status=?, reject_reason=? WHERE payment_code=?",
              (f'rejected: {reason}' if reason else 'rejected', reason, payment_code))
    conn.commit()
    rows_affected = c.rowcount
    conn.close()
    return rows_affected > 0


def get_pending_bookings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM bookings WHERE status='pending'")
    bookings = c.fetchall()
    conn.close()
    return bookings


def get_approved_bookings():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM bookings WHERE status='approved' ORDER BY created_at DESC")
    bookings = c.fetchall()
    conn.close()
    return bookings


# --- معالجات الأوامر ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    admin_info = get_admin_info(user.id) if is_admin(user.id) else None

    welcome_msg = f"""
✨ مرحباً بك في واحة الشام ✨
📅 التاريخ الحالي: {get_current_date()}

"""

    if admin_info:
        welcome_msg += f"👑 أنت مسؤول: {admin_info.get('full_name', user.full_name)}\n"
        if admin_info.get('username'):
            welcome_msg += f"📌 اليوزرنيم: @{admin_info['username']}\n"

    welcome_msg += """
📍 خياراتنا المتاحة:
- مسابح نظيفة بمواصفات عالية
- مطعم رئيسي يقدم وجبات متنوعة

اختر من القائمة:
"""

    keyboard_layout = [
        ["🏊 المسابح", "🍽️ المطاعم"],
        ["🎉 العروض الخاصة", "📞 اتصل بنا"],
        ["📅 حجز طاولة", "🆔 معرفي"]
    ]

    if is_admin(user.id):
        keyboard_layout.append(["📋 الحجوزات الموافق عليها"])

    keyboard = ReplyKeyboardMarkup(
        keyboard_layout,
        resize_keyboard=True,
        input_field_placeholder="اختر من القائمة..."
    )

    await update.message.reply_text(welcome_msg, reply_markup=keyboard)
    return ConversationHandler.END


async def show_approved_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية الدخول لهذه الصفحة")
        return

    approved_bookings = get_approved_bookings()
    if not approved_bookings:
        await update.message.reply_text("لا توجد حجوزات موافق عليها حتى الآن.")
        return

    total_bookings = len(approved_bookings)
    total_people = sum(booking[4] for booking in approved_bookings)
    total_amount = sum(booking[5] for booking in approved_bookings)

    await update.message.reply_text(
        f"📊 إحصائيات الحجوزات الموافق عليها:\n"
        f"• عدد الحجوزات: {total_bookings}\n"
        f"• إجمالي عدد الأشخاص: {total_people}\n"
        f"• إجمالي المبالغ: {total_amount:,} ل.س\n\n"
        "تفاصيل الحجوزات:"
    )

    for booking in approved_bookings:
        booking_details = (
            f"🆔 كود الحجز: <code>{booking[1]}</code>\n"
            f"👤 الاسم: {booking[2]}\n"
            f"📍 الموقع: {booking[3]}\n"
            f"👥 عدد الأشخاص: {booking[4]}\n"
            f"💰 المبلغ: {booking[5]:,} ل.س\n"
            f"🔢 رقم التحويل: {booking[6]}\n"
            f"📅 تاريخ الحجز: {booking[10]}\n"
            f"👤 معرف المستخدم: {booking[8]}\n"
        )

        await update.message.reply_text(booking_details)


async def show_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    bot = await context.bot.get_me()

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bookings WHERE user_id=?", (user.id,))
    total_bookings = c.fetchone()[0]
    c.execute("SELECT status FROM bookings WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user.id,))
    last_booking = c.fetchone()
    conn.close()

    last_booking_status = ""
    if last_booking:
        status = last_booking[0]
        last_booking_status = "\n📅 آخر حجز: " + (
            "⏳ قيد الانتظار" if status == 'pending' else
            "✅ تمت الموافقة" if 'approved' in status else
            f"❌ مرفوض ({status.split(':')[-1]})" if ':' in status else "❌ مرفوض"
        )

    admin_info = ""
    if is_admin(user.id):
        admin_info = "\n👑 أنت مسؤول في هذا البوت\n"

    chat_type = "خاص" if chat.type == "private" else "مجموعة" if chat.type == "group" else "قناة"

    response = (
        f"🆔 معلومات المعرفات:\n\n"
        f"👤 <b>معلوماتك:</b>\n"
        f"- المعرف: <code>{user.id}</code>\n"
        f"- الاسم: {user.full_name}\n"
        f"- اليوزر: @{user.username if user.username else 'غير متوفر'}\n"
        f"- عدد الحجوزات: {total_bookings}"
        f"{last_booking_status}"
        f"{admin_info}\n"
        f"💬 <b>معلومات الدردشة:</b>\n"
        f"- المعرف: <code>{chat.id}</code>\n"
        f"- النوع: {chat_type}\n"
        f"- العنوان: {chat.title if hasattr(chat, 'title') else 'دردشة خاصة'}\n\n"
        f"🤖 <b>معلومات البوت:</b>\n"
        f"- المعرف: <code>{bot.id}</code>\n"
        f"- اليوزر: @{bot.username}\n"
        f"- الاسم: {bot.full_name}\n\n"
        f"📅 التاريخ الحالي: {get_current_date()}"
    )

    await update.message.reply_html(response)


async def handle_myid_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_ids(update, context)


async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("بار", callback_data='bar')],
        [InlineKeyboardButton("جانب المسبح الشتوي", callback_data='winter_pool')],
        [InlineKeyboardButton("جانب مسبح الأطفال", callback_data='kids_pool')],
        [InlineKeyboardButton("جانب المسبح الصيفي", callback_data='summer_pool')],
        [InlineKeyboardButton("مقابل الصالة", callback_data='hall_side')]
    ])

    await update.message.reply_text(
        "📍 اختر موقع الطاولة:",
        reply_markup=keyboard
    )
    return LOCATION


async def select_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['location'] = query.data
    await query.edit_message_text("📝 الرجاء إدخال اسمك الكامل للحجز:")
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("👥 كم عدد الأشخاص؟ (الرجاء إدخال رقم فقط)")
    return PEOPLE


async def get_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        people = int(update.message.text)
        if people <= 0:
            raise ValueError

        context.user_data['people'] = people
        await update.message.reply_text("📅 الرجاء إدخال تاريخ الحجز (YYYY-MM-DD):")
        return BOOKING_DATE

    except ValueError:
        await update.message.reply_text("⚠️ الرجاء إدخال رقم صحيح موجب")
        return PEOPLE


async def get_booking_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        booking_date = update.message.text.strip()
        datetime.strptime(booking_date, '%Y-%m-%d')
        context.user_data['booking_date'] = booking_date

        location = context.user_data['location']
        remaining = CAPACITY[location] - sum(1 for b in get_pending_bookings() if b[3] == location)

        if context.user_data['people'] > remaining:
            await update.message.reply_text(f"⚠️ العدد يتجاوز السعة المتبقية ({remaining}). الرجاء إدخال عدد أقل")
            return PEOPLE

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد الحجز", callback_data='confirm')],
            [InlineKeyboardButton("❌ إلغاء", callback_data='cancel')]
        ])

        await update.message.reply_text(
            f"📋 تفاصيل الحجز:\n\n📍 الموقع: {location.replace('_', ' ')}\n"
            f"👤 الاسم: {context.user_data['name']}\n"
            f"👥 عدد الأشخاص: {context.user_data['people']}\n"
            f"📅 تاريخ الحجز: {booking_date}\n"
            f"💰 السعر الإجمالي: {context.user_data['people'] * PRICE_PER_PERSON:,} ل.س\n"
            f"🪑 السعة المتبقية: {remaining - context.user_data['people']}\n\n"
            "هل تريد تأكيد الحجز؟",
            reply_markup=keyboard
        )
        return CONFIRM

    except ValueError:
        await update.message.reply_text("⚠️ صيغة التاريخ غير صحيحة. الرجاء استخدام الصيغة YYYY-MM-DD")
        return BOOKING_DATE


async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'confirm':
        context.user_data['amount'] = context.user_data['people'] * PRICE_PER_PERSON
        context.user_data['payment_code'] = f"SHAM{datetime.now().strftime('%Y%m%d%H%M%S')}"

        await query.edit_message_text(
            f"💰 طريقة الدفع:\n\n1. أرسل المبلغ {context.user_data['amount']:,} ل.س إلى الرقم: {MERCHANT_PHONE}\n"
            "2. أدخل رقم عملية التحويل بعد الدفع\n\n"
            "الرجاء إدخال رقم عملية التحويل بعد إتمام الدفع:"
        )
        return TRANSFER_NUMBER
    else:
        await query.edit_message_text("❌ تم إلغاء الحجز")
        return ConversationHandler.END


async def get_transfer_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transfer_number = update.message.text.strip()

    if not transfer_number.isdigit():
        await update.message.reply_text("⚠️ رقم التحويل غير صالح. الرجاء إدخال رقم التحويل الصحيح:")
        return TRANSFER_NUMBER

    context.user_data['transfer_number'] = transfer_number
    context.user_data['user_id'] = update.message.from_user.id

    if save_booking(context.user_data, context):
        await update.message.reply_text(
            "✅ تم تسجيل طلب الحجز بنجاح\n\n"
            "تفاصيل طلبك:\n"
            f"كود الحجز: {context.user_data['payment_code']}\n"
            f"رقم التحويل: {transfer_number}\n"
            f"📅 تاريخ الحجز: {context.user_data['booking_date']}\n\n"
            "سيتم مراجعة طلبك وإعلامك بالنتيجة قريباً.\n"
            "يمكنك استخدام الأمر /status للتحقق من حالة حجزك.\n\n"
            "شكراً لثقتك بنا!"
        )
    else:
        await update.message.reply_text(
            "⚠️ حدث خطأ في تسجيل الحجز. الرجاء المحاولة مرة أخرى."
        )
        return TRANSFER_NUMBER

    return ConversationHandler.END


async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM bookings WHERE user_id=?", (user_id,))
    user_bookings = c.fetchall()
    conn.close()

    if not user_bookings:
        await update.message.reply_text("ليس لديك أي حجوزات مسجلة.")
        return

    for booking in user_bookings:
        status = "⏳ قيد الانتظار" if booking[7] == 'pending' else (
            "✅ تمت الموافقة" if 'approved' in booking[7] else
            f"❌ مرفوض: {booking[7].split(':')[-1]}" if ':' in booking[7] else "❌ مرفوض")

        msg = (
            f"🔄 حالة الحجز:\n\n🆔 كود الحجز: <code>{booking[1]}</code>\n"
            f"👤 الاسم: {booking[2]}\n📍 الموقع: {booking[3]}\n"
            f"💰 المبلغ: {booking[5]:,} ل.س\n🔢 رقم التحويل: {booking[6]}\n"
            f"📅 تاريخ الحجز: {booking[10]}\n"
            f"📌 الحالة: {status}\n\n"
        )

        if 'approved' in booking[7]:
            msg += "🎉 تمت الموافقة على حجزك! استمتع بوقتك!"

        await update.message.reply_text(msg)


async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية الدخول لهذه الصفحة")
        return

    pending = get_pending_bookings()
    if not pending:
        await update.message.reply_text("لا توجد حجوزات منتظرة للموافقة")
        return

    total_bookings = len(pending)
    total_people = sum(booking[4] for booking in pending)

    await update.message.reply_text(
        f"📊 إحصائيات الحجوزات:\n"
        f"• عدد الحجوزات المعلقة: {total_bookings}\n"
        f"• إجمالي عدد الأشخاص: {total_people}\n\n"
        "تفاصيل الحجوزات:"
    )

    for booking in pending:
        booking_details = (
            f"🆔 كود الحجز: <code>{booking[1]}</code>\n"
            f"👤 الاسم: {booking[2]}\n"
            f"📍 الموقع: {booking[3]}\n"
            f"👥 عدد الأشخاص: {booking[4]}\n"
            f"💰 المبلغ: {booking[5]:,} ل.س\n"
            f"🔢 رقم التحويل: {booking[6]}\n"
            f"📅 تاريخ الحجز: {booking[10]}\n"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ الموافقة على الحجز",
                callback_data=f"approve_{booking[1]}")
            ]
        ])

        await update.message.reply_text(
            booking_details,
            reply_markup=keyboard
        )


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if not is_admin(user.id):
        await query.edit_message_text("⚠️ ليس لديك صلاحية تنفيذ هذا الأمر")
        return

    payment_code = query.data.split('_')[1]
    if approve_booking(payment_code):
        await notify_user_approval(context, payment_code)
        await query.edit_message_text(
            f"✅ تمت الموافقة على الحجز {payment_code} بنجاح\n"
            "تم إرسال إشعار للمستخدم بالموافقة"
        )
    else:
        await query.edit_message_text(
            f"⚠️ لم يتم العثور على الحجز {payment_code}"
        )


async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية تنفيذ هذا الأمر")
        return

    parts = update.message.text.split('_')
    payment_code = parts[1]
    reason = ' '.join(parts[2:]) if len(parts) > 2 else None

    if reject_booking(payment_code, reason):
        msg = f"✅ تم رفض الحجز {payment_code}"
        if reason:
            msg += f"\n📝 السبب: {reason}"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text(f"⚠️ لم يتم العثور على الحجز {payment_code}")


async def promote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية تنفيذ هذا الأمر")
        return

    if not context.args:
        await update.message.reply_text("الاستخدام: /promote <user_id> <username> <full_name>")
        return

    user_id = context.args[0]
    username = context.args[1] if len(context.args) > 1 else None
    full_name = ' '.join(context.args[2:]) if len(context.args) > 2 else None

    if add_admin(user_id, username, full_name):
        await update.message.reply_text(
            f"✅ تمت ترقية المستخدم إلى مسؤول:\n"
            f"🆔 الآيدي: {user_id}\n"
            f"📌 اليوزرنيم: @{username if username else 'بدون'}\n"
            f"📛 الاسم: {full_name if full_name else 'غير محدد'}"
        )
    else:
        await update.message.reply_text("⚠️ فشل في ترقية المستخدم أو هو مسؤول بالفعل")


async def demote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية تنفيذ هذا الأمر")
        return

    if not context.args:
        await update.message.reply_text("الاستخدام: /demote <user_id>")
        return

    user_id = context.args[0]
    if remove_admin(user_id):
        await update.message.reply_text(f"✅ تم إزالة صلاحيات المسؤول من {user_id}")
    else:
        await update.message.reply_text("⚠️ فشل في إزالة الصلاحيات أو المستخدم ليس مسؤولاً")


async def list_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not is_admin(user.id):
        await update.message.reply_text("⚠️ ليس لديك صلاحية تنفيذ هذا الأمر")
        return

    admins = list_admins()
    if not admins:
        await update.message.reply_text("لا يوجد مسؤولين حالياً")
        return

    message = "📋 قائمة المسؤولين:\n\n"
    for admin in admins:
        message += (
            f"🆔 الآيدي: {admin[0]}\n"
            f"📌 اليوزرنيم: @{admin[1] if admin[1] else 'بدون'}\n"
            f"📛 الاسم: {admin[2] if admin[2] else 'غير محدد'}\n"
            f"🕒 تاريخ الإضافة: {admin[3]}\n\n"
        )

    await update.message.reply_text(message)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "تم إلغاء العملية",
        reply_markup=ReplyKeyboardMarkup([
            ["🏊 المسابح", "🍽️ المطاعم"],
            ["🎉 العروض الخاصة", "📞 اتصل بنا"],
            ["📅 حجز طاولة", "🆔 معرفي"]
        ], resize_keyboard=True)
    )
    return ConversationHandler.END


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "عذراً، لا أفهم هذا الأمر.\n"
        "الرجاء استخدام الأزرار المتاحة أو كتابة /start للبدء من جديد.",
        reply_markup=ReplyKeyboardMarkup([
            ["🏊 المسابح", "🍽️ المطاعم"],
            ["🎉 العروض الخاصة", "📞 اتصل بنا"],
            ["📅 حجز طاولة", "🆔 معرفي"]
        ], resize_keyboard=True)
    )


def main():
    try:
        app = Application.builder().token("7992401524:AAEIKl5ECMeIl24bRlw3A3_2j_0Uvae0yLQ").build()

        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex(r'^(📅 حجز طاولة|حجز طاولة|حجز|booking|book)$'), start_booking)
            ],
            states={
                LOCATION: [CallbackQueryHandler(select_location)],
                NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
                PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_people)],
                BOOKING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_booking_date)],
                CONFIRM: [CallbackQueryHandler(confirm_booking)],
                TRANSFER_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_number)]
            },
            fallbacks=[
                CommandHandler('cancel', cancel),
                MessageHandler(filters.Regex(r'^(إلغاء|الغاء|تراجع)$'), cancel)
            ],
            allow_reentry=True,
            per_user=True
        )

        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("status", check_status))
        app.add_handler(CommandHandler("admin", admin_approve))
        app.add_handler(CallbackQueryHandler(handle_approve, pattern=r'^approve_'))
        app.add_handler(CommandHandler("reject", reject_command))
        app.add_handler(CommandHandler("promote", promote_admin))
        app.add_handler(CommandHandler("demote", demote_admin))
        app.add_handler(CommandHandler("admins", list_admins_cmd))
        app.add_handler(CommandHandler("myid", show_ids))
        app.add_handler(MessageHandler(filters.Regex(r'^🆔 معرفي$'), handle_myid_button))
        app.add_handler(MessageHandler(filters.Regex(r'^📋 الحجوزات الموافق عليها$'), show_approved_bookings))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_command))

        logger.info("🚀 بدء تشغيل البوت...")
        app.run_polling(drop_pending_updates=True)

    except Exception as e:
        logger.error(f"حدث خطأ: {str(e)}")
    finally:
        logger.info("إيقاف البوت")


if __name__ == "__main__":
    if not list_admins():
        add_admin(
            user_id="5901137890",
            username="Bashar8100",
            full_name="بشار"
        )
    main()
