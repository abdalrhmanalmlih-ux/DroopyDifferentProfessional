import telebot
import json
import os
import threading
import time
import copy
from telebot import types
from datetime import date, datetime, timedelta

API_TOKEN = os.environ["BOT_TOKEN"]
bot = telebot.TeleBot(API_TOKEN)
DATA_FILE = "data.json"
user_states = {}
user_data = {}

SOURCE_TODAY = "🌅 من غلة اليوم"
SOURCE_OLD = "🏦 غلة قديمة"
SOURCE_GOODS = "📦 بضائع"
INV_YES = "🧾 يوجد رقم فاتورة"
INV_NO = "❌ بدون فاتورة"
PAY_YES = "✅ يوجد دفعة"
PAY_NO = "🚫 لا يوجد دفعة"
CUR_SYP = "💴 ليرة سورية"
CUR_USD = "💵 دولار"


# ===== Data =====
def load_data():
    if not os.path.exists(DATA_FILE):
        initial = {
            "customers": {},
            "suppliers": {},
            "expenses": {},
            "sales": [],
            "users": [],
            "archives": [],
            "settings": {"notify_enabled": True, "notify_hour": 8},
        }
        save_data(initial)
        return initial
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in ("users", "archives"):
            if k not in data:
                data[k] = []
        if "settings" not in data:
            data["settings"] = {"notify_enabled": True, "notify_hour": 8}
        if isinstance(data.get("sales"), dict):
            data["sales"] = []
        for p in data["sales"]:
            if "days" not in p:
                p["days"] = {}
        for section in ("suppliers", "expenses"):
            for entity in data[section].values():
                if "log" not in entity:
                    entity["log"] = []
        for cdata in data["customers"].values():
            if "log" not in cdata:
                cdata["log"] = []
            if "balance" not in cdata:
                cdata["balance"] = 0
        return data
    except:
        return {
            "customers": {},
            "suppliers": {},
            "expenses": {},
            "sales": [],
            "users": [],
            "archives": [],
            "settings": {"notify_enabled": True, "notify_hour": 8},
        }


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def register_user(chat_id):
    data = load_data()
    if chat_id not in data["users"]:
        data["users"].append(chat_id)
        save_data(data)


# ===== State Helpers =====
def set_state(uid, s):
    user_states[uid] = s


def get_state(uid):
    return user_states.get(uid, "main")


def set_udata(uid, k, v):
    if uid not in user_data:
        user_data[uid] = {}
    user_data[uid][k] = v


def get_udata(uid, k):
    return user_data.get(uid, {}).get(k)


def clear_udata(uid):
    user_data[uid] = {}


# ===== Date Helpers =====
def format_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y")
    except:
        return str(d)


def days_in_range(start_str, end_str):
    start = datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_str, "%Y-%m-%d").date()
    result, cur = [], start
    while cur <= end:
        result.append(str(cur))
        cur += timedelta(days=1)
    return result


def period_end(p):
    return p["end"] if p.get("end") else str(date.today())


def period_label(p):
    end_str = format_date(period_end(p))
    status = " (جارية)" if not p.get("end") else ""
    return f"🗓 {format_date(p['start'])} ← {end_str}{status}"


def cur_label(c):
    return "ل.س" if c == "syp" else "$"


def cur_key(text):
    return "syp" if "ليرة" in text or "SYP" in text else "usd"


# ===== Daily sales for a specific date =====
def get_day_sales_detail(data, iso):
    """جمع كل المعاملات ليوم محدد من غلة اليوم فقط + غلة الصندوق"""
    # غلة الصندوق لهذا اليوم
    box_syp = 0
    if data["sales"]:
        p = data["sales"][-1]
        day_val = p["days"].get(iso)
        if day_val:
            box_syp = day_val.get("syp", 0)

    items = []
    total = box_syp
    if box_syp > 0:
        items.append(f"💵 غلة الصندوق: {box_syp:,} ل.س")

    # دفعات التجار في هذا اليوم من غلة اليوم فقط
    for sname, sdata in data["suppliers"].items():
        for t in sdata.get("log", []):
            if t.get("date") == iso and t.get("type") == "دفع" and t.get("source") == SOURCE_TODAY:
                amt = t["amount"]
                cur = cur_label(t.get("currency", "syp"))
                items.append(f"🏪 دفع لـ{sname}: {amt:,} {cur}")
                if t.get("currency", "syp") == "syp":
                    total += amt
            # دفعة مدمجة مع شراء من غلة اليوم
            if t.get("date") == iso and t.get("type") == "شراء" and t.get("paid_source") == SOURCE_TODAY:
                paid = t.get("paid_amount", 0)
                if paid > 0:
                    cur = cur_label(t.get("paid_currency", "syp"))
                    items.append(f"🏪 دفعة شراء لـ{sname}: {paid:,} {cur}")
                    if t.get("paid_currency", "syp") == "syp":
                        total += paid

    # سحوبات الزبائن في هذا اليوم من غلة اليوم فقط
    for cname, cdata in data["customers"].items():
        for t in cdata.get("log", []):
            if t.get("date") == iso and t.get("type") == "سحب" and t.get("source") == SOURCE_TODAY:
                amt = t["amount"]
                items.append(f"👤 سحب {cname}: {amt:,} ل.س")
                total += amt

    # المصاريف في هذا اليوم من غلة اليوم فقط
    for ename, edata in data["expenses"].items():
        for t in edata.get("log", []):
            if t.get("date") == iso and t.get("source") == SOURCE_TODAY:
                amt = t["amount"]
                items.append(f"💸 {ename}: {amt:,} ل.س")
                total += amt

    return items, total


# ===== Sales Summary All Periods =====
def format_all_sales_summary(data):
    """ملخص كامل لجميع فترات المبيعات مع الجرد"""
    lines = ["📊 ملخص المبيعات الكامل", "═" * 28]

    all_periods = []

    # الفترات المؤرشفة
    for arch in data.get("archives", []):
        for p in arch.get("sales", []):
            all_periods.append(("arch", p, arch.get("label", "")))

    # الفترات الحالية
    for p in data.get("sales", []):
        all_periods.append(("current", p, ""))

    if not all_periods:
        return "لا توجد بيانات مبيعات."

    grand_total = 0
    for kind, p, arch_label in all_periods:
        end = period_end(p)
        box_syp = sum(v.get("syp", 0) for v in p.get("days", {}).values())
        label = f"{'📦 ' + arch_label if kind == 'arch' else '🔄 الدورة الحالية'}"
        lines.append(f"\n{label}")
        lines.append(f"📅 {format_date(p['start'])} ← {format_date(end)}")
        lines.append(f"  💵 الصندوق: {box_syp:,} ل.س")

        # إجمالي المعاملات في هذه الفترة
        cust_total = sum(
            t["amount"]
            for c in data["customers"].values()
            for t in c.get("log", [])
            if t.get("type") == "سحب" and p["start"] <= t.get("date", "") <= end
        )
        sup_total = sum(
            t["amount"]
            for s in data["suppliers"].values()
            for t in s.get("log", [])
            if t.get("type") == "دفع" and t.get("currency", "syp") == "syp"
            and p["start"] <= t.get("date", "") <= end
        )
        exp_total = sum(
            t["amount"]
            for e in data["expenses"].values()
            for t in e.get("log", [])
            if p["start"] <= t.get("date", "") <= end
        )
        period_total = box_syp + cust_total + sup_total + exp_total
        if cust_total:
            lines.append(f"  👤 زبائن: {cust_total:,} ل.س")
        if sup_total:
            lines.append(f"  🏪 تجار: {sup_total:,} ل.س")
        if exp_total:
            lines.append(f"  💸 مصاريف: {exp_total:,} ل.س")
        lines.append(f"  ▶ الإجمالي: {period_total:,} ل.س")
        grand_total += period_total

    lines += ["═" * 28, f"📦 المجموع الكلي: {grand_total:,} ل.س"]
    return "\n".join(lines)


# ===== Sales Register - current period days =====
def get_current_period(data):
    if not data["sales"]:
        return None
    p = data["sales"][-1]
    if p.get("end"):
        return None
    return p


def build_sales_register_kb(data):
    """بناء لوحة مفاتيح السجل: اليوم الحالي في الأعلى ثم باقي الأيام"""
    p = get_current_period(data)
    if not p:
        return None, None
    today = str(date.today())
    end = period_end(p)
    days = days_in_range(p["start"], end)
    # اليوم الحالي في الأعلى
    days_sorted = sorted(days, reverse=True)

    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for d in days_sorted:
        # حساب مجموع اليوم
        _, day_total = get_day_sales_detail(data, d)
        today_marker = " 📍" if d == today else ""
        btn = f"📆 {format_date(d)} | {day_total:,}ل.س{today_marker}"
        m.add(btn)
    m.add("🔙 رجوع")
    return m, days_sorted


def format_day_detail(data, iso):
    items, total = get_day_sales_detail(data, iso)
    lines = [f"📆 {format_date(iso)}", "─" * 25]
    if items:
        lines += items
    else:
        lines.append("لا توجد معاملات مسجلة.")
    lines += ["─" * 25, f"📊 مجموع المبيعات: {total:,} ل.س"]
    return "\n".join(lines)


# ===== Supplier helpers =====
def supplier_balance_line(sdata):
    """سطر الذمة الصافية: مشتريات - دفعات"""
    log = sdata.get("log", [])
    pur_syp = sum(t.get("amount", 0) for t in log if t.get("type") == "شراء" and t.get("currency", "syp") == "syp")
    pur_usd = sum(t.get("amount", 0) for t in log if t.get("type") == "شراء" and t.get("currency", "syp") == "usd")
    paid_syp = sum(t.get("amount", 0) for t in log if t.get("type") == "دفع" and t.get("currency", "syp") == "syp")
    paid_usd = sum(t.get("amount", 0) for t in log if t.get("type") == "دفع" and t.get("currency", "syp") == "usd")
    paid_syp += sum(t.get("paid_amount", 0) for t in log if t.get("type") == "شراء" and t.get("paid_currency", "syp") == "syp")
    paid_usd += sum(t.get("paid_amount", 0) for t in log if t.get("type") == "شراء" and t.get("paid_currency", "syp") == "usd")
    parts = []
    if pur_syp or paid_syp:
        net = pur_syp - paid_syp
        parts.append(f"💰 الذمة: {net:,} ل.س")
    if pur_usd or paid_usd:
        net = pur_usd - paid_usd
        parts.append(f"💰 الذمة: {net:,} $")
    return " | ".join(parts) if parts else "💰 لا توجد ذمم"


def format_supplier_report(name, sdata):
    log = sorted(sdata.get("log", []), key=lambda x: x.get("date", ""))
    lines = [f"📋 كشف حساب التاجر: {name}", "─" * 25]
    if not log:
        lines.append("لا توجد معاملات مسجلة بعد.")
    else:
        for t in log:
            d = format_date(t.get("date", ""))
            t_type = t.get("type", "")
            amt = t.get("amount", 0)
            cur = cur_label(t.get("currency", "syp"))
            inv = t.get("invoice_no", "")
            inv_txt = f" | فاتورة: {inv}" if inv else ""

            if t_type == "شراء":
                paid_amt = t.get("paid_amount", 0)
                paid_src = t.get("paid_source", "")
                paid_cur = cur_label(t.get("paid_currency", "syp"))
                remaining = amt - paid_amt
                lines.append(f"📅 {d} | 🛒 شراء{inv_txt}")
                lines.append(f"    قيمة الفاتورة: {amt:,} {cur}")
                if paid_amt > 0:
                    lines.append(f"    مدفوع: {paid_amt:,} {paid_cur} ({paid_src})")
                    if remaining > 0:
                        lines.append(f"    متبقي: {remaining:,} {cur}")
                else:
                    lines.append(f"    لم يُدفع بعد")
            elif t_type == "دفع":
                src = t.get("source", "")
                src_txt = f" | {src}" if src else ""
                lines.append(f"📅 {d} | 💳 دفع: {amt:,} {cur}{src_txt}{inv_txt}")
    lines.append("─" * 25)
    # المشتريات بالليرة فقط للمقارنة (نفس العملة)
    total_purchased_syp = sum(t.get("amount", 0) for t in log if t.get("type") == "شراء" and t.get("currency", "syp") == "syp")
    total_purchased_usd = sum(t.get("amount", 0) for t in log if t.get("type") == "شراء" and t.get("currency", "syp") == "usd")
    # الدفعات المستقلة
    total_paid_syp = sum(t.get("amount", 0) for t in log if t.get("type") == "دفع" and t.get("currency", "syp") == "syp")
    total_paid_usd = sum(t.get("amount", 0) for t in log if t.get("type") == "دفع" and t.get("currency", "syp") == "usd")
    # دفعات مدمجة مع الشراء
    total_paid_syp += sum(t.get("paid_amount", 0) for t in log if t.get("type") == "شراء" and t.get("paid_currency", "syp") == "syp")
    total_paid_usd += sum(t.get("paid_amount", 0) for t in log if t.get("type") == "شراء" and t.get("paid_currency", "syp") == "usd")

    net_syp = total_purchased_syp - total_paid_syp
    net_usd = total_purchased_usd - total_paid_usd

    if total_purchased_syp or total_paid_syp:
        lines.append(f"🛒 مشتريات ل.س: {total_purchased_syp:,} | مدفوع: {total_paid_syp:,} | 💰 الذمة: {net_syp:,} ل.س")
    if total_purchased_usd or total_paid_usd:
        lines.append(f"🛒 مشتريات $: {total_purchased_usd:,} | مدفوع: {total_paid_usd:,} | 💰 الذمة: {net_usd:,} $")
    return "\n".join(lines)


# ===== Customer helpers =====
def format_customer_report(name, cdata):
    log = sorted(cdata.get("log", []), key=lambda x: x.get("date", ""))
    lines = [f"📋 كشف حساب الزبون: {name}", "─" * 22]
    if not log:
        lines.append("لا توجد معاملات مسجلة بعد.")
    else:
        for t in log:
            t_type = t.get("type", "")
            amt = t["amount"]
            t_date = format_date(t.get("date", ""))
            if t_type == "سحب":
                src = t.get("source", "")
                src_txt = f" | {src}" if src else ""
                lines.append(f"📅 {t_date} | ⬆️ سحب: {amt:,} ل.س{src_txt}")
            elif t_type == "دفع":
                lines.append(f"📅 {t_date} | ⬇️ دفع: {amt:,} ل.س")
    lines += ["─" * 22, f"💰 الرصيد الحالي: {cdata.get('balance', 0):,} ل.س"]
    return "\n".join(lines)


# ===== Statistics =====
def format_statistics(data):
    box_syp = sum(v.get("syp", 0) for p in data["sales"] for v in p.get("days", {}).values())
    exp_syp = sum(t.get("amount", 0) for e in data["expenses"].values() for t in e.get("log", []))
    sup_paid = sum(
        t.get("amount", 0) for s in data["suppliers"].values()
        for t in s.get("log", []) if t.get("type") == "دفع"
    ) + sum(
        t.get("paid_amount", 0) for s in data["suppliers"].values()
        for t in s.get("log", []) if t.get("type") == "شراء"
    )
    cust_bal = sum(c.get("balance", 0) for c in data["customers"].values())
    return "\n".join([
        "📈 الإحصائيات\n" + "─" * 22,
        f"\n💵 غلة الصندوق الكلية: {box_syp:,} ل.س",
        f"💸 المصاريف الكلية: {exp_syp:,} ل.س",
        f"🏪 مدفوع للتجار: {sup_paid:,} ل.س",
        f"👤 أرصدة الزبائن: {cust_bal:,} ل.س",
    ])


# ===== Archive / Reset =====
def do_archive_and_reset(data, reset_expenses, reset_sales):
    today = str(date.today())
    archive = {
        "date": today,
        "label": f"جرد {format_date(today)}",
        "customers": copy.deepcopy(data["customers"]),
        "suppliers": copy.deepcopy(data["suppliers"]),
        "expenses": copy.deepcopy(data["expenses"]),
        "sales": copy.deepcopy(data["sales"]),
    }
    data["archives"].append(archive)
    if reset_expenses:
        for e in data["expenses"].values():
            e["log"] = []
    if reset_sales:
        if data["sales"] and not data["sales"][-1].get("end"):
            data["sales"][-1]["end"] = today
        data["sales"].append({"start": today, "end": None, "days": {}})
    save_data(data)
    return archive["label"]


def format_archive_summary(arch):
    lines = [f"📦 {arch['label']} — {format_date(arch['date'])}", "─" * 22]
    box_syp = sum(v.get("syp", 0) for p in arch.get("sales", []) for v in p.get("days", {}).values())
    exp_syp = sum(t.get("amount", 0) for e in arch.get("expenses", {}).values() for t in e.get("log", []))
    lines += [
        f"💵 غلة الصندوق: {box_syp:,} ل.س",
        f"💸 المصاريف: {exp_syp:,} ل.س",
        f"👤 عدد الزبائن: {len(arch.get('customers', {}))}",
        f"🏪 عدد التجار: {len(arch.get('suppliers', {}))}",
    ]
    return "\n".join(lines)


# ===== Keyboards =====
def main_keyboard():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("💰 زبائن", "🏪 تجار", "💸 المصاريف", "📊 المبيعات",
          "📈 إحصائيات", "📤 تصدير", "🗃 جرد", "⚙️ الإعدادات")
    return m


def back_keyboard():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("🔙 رجوع")
    return m


def customers_menu_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("➕ إضافة زبون", "👥 عرض الزبائن", "🔙 رجوع")
    return m


def customer_action_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("📥 سحب", "📤 دفع", "👁 عرض الرصيد", "📋 كشف الحساب", "🗑 حذف", "🔙 رجوع")
    return m


def suppliers_menu_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("➕ إضافة تاجر", "👥 عرض التجار", "🔙 رجوع")
    return m


def supplier_action_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("🛒 شراء بضائع", "💳 دفع للتاجر", "👁 عرض الرصيد", "📋 كشف الحساب", "🗑 حذف", "🔙 رجوع")
    return m


def expenses_menu_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("➕ إضافة مصروف", "📋 عرض المصاريف", "🔙 رجوع")
    return m


def expense_action_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("📋 كشف الحساب", "🗑 حذف", "🔙 رجوع")
    return m


def source_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(SOURCE_TODAY, SOURCE_OLD, SOURCE_GOODS, "🔙 رجوع")
    return m


def invoice_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(INV_YES, INV_NO, "🔙 رجوع")
    return m


def payment_exists_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(PAY_YES, PAY_NO, "🔙 رجوع")
    return m


def currency_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(CUR_SYP, CUR_USD, "🔙 رجوع")
    return m


def sales_menu_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("📋 سجل", "📊 ملخص المبيعات", "🔙 رجوع")
    return m


def day_action_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add("✏️ تسجيل/تعديل الغلة", "🔙 رجوع")
    return m


def settings_kb(data):
    s = data.get("settings", {})
    enabled = s.get("notify_enabled", True)
    hour = s.get("notify_hour", 8)
    toggle = "🔕 إيقاف التذكير" if enabled else "🔔 تفعيل التذكير"
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add(toggle, f"⏰ تغيير الوقت (الحالي: {hour}:00)", "🔙 رجوع")
    return m


def inventory_menu_kb():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    m.add("📦 تصفير المصاريف فقط", "📊 تصفير المبيعات فقط",
          "🔄 تصفير الكل (مصاريف + مبيعات)", "📚 السجلات القديمة", "🔙 رجوع")
    return m


def confirm_kb(action):
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add(f"✅ تأكيد {action}", "❌ إلغاء")
    return m


# ===== Shared helpers =====
def send(cid, text, kb):
    bot.send_message(cid, text, reply_markup=kb)


def go_main(message):
    send(message.chat.id, "القائمة الرئيسية:", main_keyboard())


def show_list(message, uid, items, state, prompt, empty_msg, back_kb):
    if not items:
        send(message.chat.id, empty_msg, back_kb)
        return
    m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for name in items:
        m.add(name)
    m.add("🔙 رجوع")
    set_state(uid, state)
    send(message.chat.id, prompt, m)


def log_transaction(entity, entry):
    if "log" not in entity:
        entity["log"] = []
    entity["log"].append(entry)


# ===== Daily notification =====
def daily_notification_thread():
    last_day = None
    while True:
        try:
            data = load_data()
            s = data.get("settings", {})
            if s.get("notify_enabled", True):
                now = datetime.now()
                today = str(date.today())
                if now.hour == s.get("notify_hour", 8) and now.minute == 0 and today != last_day:
                    last_day = today
                    for cid in data.get("users", []):
                        try:
                            bot.send_message(
                                cid,
                                f"🌅 صباح الخير!\n📅 {format_date(today)}\nلا تنسَ إدخال مبيعات اليوم 📊",
                                reply_markup=main_keyboard(),
                            )
                        except:
                            pass
        except:
            pass
        time.sleep(60)


# ===== Export =====
def handle_export(message):
    data = load_data()
    lines = ["📊 التقرير الكامل\n" + "─" * 25]
    lines.append("\n👤 الزبائن:")
    if data["customers"]:
        total_bal = 0
        for n, c in data["customers"].items():
            bal = c.get("balance", 0)
            lines.append(f"  • {n}: رصيد {bal:,} ل.س")
            total_bal += bal
        lines.append(f"  ▶ إجمالي الأرصدة: {total_bal:,} ل.س")
    else:
        lines.append("  لا يوجد بيانات")
    lines.append("\n🏪 التجار:")
    if data["suppliers"]:
        for n, s in data["suppliers"].items():
            total_p = sum(t.get("amount", 0) for t in s.get("log", []) if t.get("type") == "دفع")
            total_p += sum(t.get("paid_amount", 0) for t in s.get("log", []) if t.get("type") == "شراء")
            lines.append(f"  • {n}: مدفوع {total_p:,} ل.س")
    else:
        lines.append("  لا يوجد بيانات")
    lines.append("\n💸 المصاريف:")
    if data["expenses"]:
        for n, e in data["expenses"].items():
            total_e = sum(t.get("amount", 0) for t in e.get("log", []))
            lines.append(f"  • {n}: {total_e:,} ل.س")
    else:
        lines.append("  لا يوجد بيانات")
    box_syp = sum(v.get("syp", 0) for p in data["sales"] for v in p.get("days", {}).values())
    lines.append(f"\n💵 غلة الصندوق الكلية: {box_syp:,} ل.س")
    send(message.chat.id, "\n".join(lines), main_keyboard())


# ===== Sales Register =====
def show_sales_register(message, uid):
    data = load_data()
    p = get_current_period(data)
    if not p:
        send(message.chat.id,
             "لا توجد فترة مبيعات جارية.\nاذهب إلى الجرد لبدء فترة جديدة.",
             sales_menu_kb())
        return
    kb, days_sorted = build_sales_register_kb(data)
    set_udata(uid, "register_days", days_sorted)
    set_state(uid, "sales_register")
    today = str(date.today())
    send(message.chat.id,
         f"📋 سجل المبيعات\n📅 من {format_date(p['start'])} | اليوم: {format_date(today)}",
         kb)


# ===== Back handler =====
def handle_back(message, uid, state):
    cid = message.chat.id
    data = load_data()

    if state in ("customers_menu", "customers_add_name"):
        set_state(uid, "main"); go_main(message)
    elif state == "customers_view":
        set_state(uid, "customers_menu"); send(cid, "قسم الزبائن:", customers_menu_kb())
    elif state == "customers_action":
        show_list(message, uid, data["customers"], "customers_view",
                  "اختر زبوناً:", "لا يوجد زبائن.", customers_menu_kb())
    elif state == "customers_source":
        set_state(uid, "customers_action")
        name = get_udata(uid, "selected")
        c = data["customers"].get(name, {})
        send(cid, f"👤 {name}\n💰 الرصيد: {c.get('balance',0):,} ل.س", customer_action_kb())
    elif state == "customers_amount":
        set_state(uid, "customers_source"); send(cid, "اختر مصدر السحب:", source_kb())
    elif state == "customers_pay_amount":
        set_state(uid, "customers_action")
        name = get_udata(uid, "selected")
        c = data["customers"].get(name, {})
        send(cid, f"👤 {name}\n💰 الرصيد: {c.get('balance',0):,} ل.س", customer_action_kb())

    elif state in ("suppliers_menu", "suppliers_add_name"):
        set_state(uid, "main"); go_main(message)
    elif state == "suppliers_view":
        set_state(uid, "suppliers_menu"); send(cid, "قسم التجار:", suppliers_menu_kb())
    elif state == "suppliers_action":
        show_list(message, uid, data["suppliers"], "suppliers_view",
                  "اختر تاجراً:", "لا يوجد تجار.", suppliers_menu_kb())

    # شراء بضائع back chain
    elif state == "sup_buy_inv":
        set_state(uid, "suppliers_action")
        name = get_udata(uid, "selected")
        send(cid, f"🏪 {name}", supplier_action_kb())
    elif state == "sup_buy_inv_num":
        set_state(uid, "sup_buy_inv"); send(cid, "هل يوجد رقم فاتورة؟", invoice_kb())
    elif state == "sup_buy_currency":
        inv = get_udata(uid, "sup_inv_no")
        if inv:
            set_state(uid, "sup_buy_inv_num"); send(cid, "أدخل رقم الفاتورة:", back_keyboard())
        else:
            set_state(uid, "sup_buy_inv"); send(cid, "هل يوجد رقم فاتورة؟", invoice_kb())
    elif state == "sup_buy_amount":
        set_state(uid, "sup_buy_currency"); send(cid, "اختر نوع العملة:", currency_kb())
    elif state == "sup_buy_payment":
        set_state(uid, "sup_buy_amount"); send(cid, "أدخل قيمة الفاتورة:", back_keyboard())
    elif state == "sup_buy_pay_source":
        set_state(uid, "sup_buy_payment"); send(cid, "هل توجد دفعة الآن؟", payment_exists_kb())
    elif state == "sup_buy_pay_currency":
        set_state(uid, "sup_buy_pay_source"); send(cid, "اختر مصدر الدفعة:", source_kb())
    elif state == "sup_buy_pay_amount":
        set_state(uid, "sup_buy_pay_currency"); send(cid, "اختر عملة الدفعة:", currency_kb())

    # دفع للتاجر back chain
    elif state == "sup_pay_source":
        set_state(uid, "suppliers_action")
        name = get_udata(uid, "selected")
        send(cid, f"🏪 {name}", supplier_action_kb())
    elif state == "sup_pay_currency":
        set_state(uid, "sup_pay_source"); send(cid, "اختر مصدر الدفع:", source_kb())
    elif state == "sup_pay_inv":
        set_state(uid, "sup_pay_currency"); send(cid, "اختر نوع العملة:", currency_kb())
    elif state == "sup_pay_inv_num":
        set_state(uid, "sup_pay_inv"); send(cid, "هل يوجد رقم فاتورة؟", invoice_kb())
    elif state == "sup_pay_amount":
        inv = get_udata(uid, "sup_inv_no")
        if inv:
            set_state(uid, "sup_pay_inv_num"); send(cid, "أدخل رقم الفاتورة:", back_keyboard())
        else:
            set_state(uid, "sup_pay_inv"); send(cid, "هل يوجد رقم فاتورة؟", invoice_kb())

    elif state in ("expenses_menu", "expenses_add_name"):
        set_state(uid, "main"); go_main(message)
    elif state == "expenses_add_source":
        set_state(uid, "expenses_add_name"); send(cid, "اكتب اسم/وصف المصروف:", back_keyboard())
    elif state == "expenses_add_amount":
        set_state(uid, "expenses_add_source"); send(cid, "اختر مصدر الدفع:", source_kb())
    elif state == "expenses_view":
        set_state(uid, "expenses_menu"); send(cid, "قسم المصاريف:", expenses_menu_kb())
    elif state == "expenses_action":
        show_list(message, uid, data["expenses"], "expenses_view",
                  "اختر مصروفاً:", "لا يوجد مصاريف.", expenses_menu_kb())

    elif state in ("sales_menu", "sales_register"):
        set_state(uid, "main"); go_main(message)
    elif state == "sales_day_detail":
        show_sales_register(message, uid)
    elif state == "sales_day_edit":
        iso = get_udata(uid, "selected_day")
        set_state(uid, "sales_day_detail")
        send(cid, format_day_detail(data, iso), day_action_kb())

    elif state == "inventory_menu":
        set_state(uid, "main"); go_main(message)
    elif state == "inventory_confirm":
        set_state(uid, "inventory_menu"); send(cid, "قسم الجرد:", inventory_menu_kb())
    elif state == "archives_list":
        set_state(uid, "inventory_menu"); send(cid, "قسم الجرد:", inventory_menu_kb())
    elif state == "settings_menu":
        set_state(uid, "main"); go_main(message)
    elif state == "settings_set_hour":
        set_state(uid, "settings_menu")
        data2 = load_data(); send(cid, "الإعدادات:", settings_kb(data2))
    else:
        set_state(uid, "main"); go_main(message)


# ===== Main handler =====
@bot.message_handler(func=lambda m: True)
def handle_message(message):
    uid = message.chat.id
    text = message.text.strip() if message.text else ""
    cid = message.chat.id
    register_user(cid)

    if text == "/start":
        set_state(uid, "main"); clear_udata(uid)
        send(cid, "أهلاً! القائمة الرئيسية:", main_keyboard())
        return

    state = get_state(uid)

    if text == "🔙 رجوع":
        handle_back(message, uid, state)
        return

    # ── MAIN MENU ──
    if state == "main":
        if text == "💰 زبائن":
            set_state(uid, "customers_menu"); send(cid, "قسم الزبائن:", customers_menu_kb())
        elif text == "🏪 تجار":
            set_state(uid, "suppliers_menu"); send(cid, "قسم التجار:", suppliers_menu_kb())
        elif text == "💸 المصاريف":
            set_state(uid, "expenses_menu"); send(cid, "قسم المصاريف:", expenses_menu_kb())
        elif text == "📊 المبيعات":
            set_state(uid, "sales_menu"); send(cid, "قسم المبيعات:", sales_menu_kb())
        elif text == "📈 إحصائيات":
            data = load_data(); send(cid, format_statistics(data), main_keyboard())
        elif text == "📤 تصدير":
            handle_export(message)
        elif text == "🗃 جرد":
            set_state(uid, "inventory_menu"); send(cid, "قسم الجرد:", inventory_menu_kb())
        elif text == "⚙️ الإعدادات":
            set_state(uid, "settings_menu"); data = load_data(); send(cid, "الإعدادات:", settings_kb(data))
        return

    # ── CUSTOMERS ──
    elif state == "customers_menu":
        if text == "➕ إضافة زبون":
            set_state(uid, "customers_add_name"); send(cid, "اكتب اسم الزبون الجديد:", back_keyboard())
        elif text == "👥 عرض الزبائن":
            data = load_data()
            show_list(message, uid, data["customers"], "customers_view",
                      "اختر زبوناً:", "لا يوجد زبائن.", customers_menu_kb())

    elif state == "customers_add_name":
        data = load_data()
        if text in data["customers"]:
            send(cid, f"⚠️ الزبون '{text}' موجود مسبقاً.", customers_menu_kb())
            set_state(uid, "customers_menu")
        else:
            data["customers"][text] = {"balance": 0, "log": []}
            save_data(data)
            send(cid, f"✅ تم إضافة الزبون: {text}", customers_menu_kb())
            set_state(uid, "customers_menu")

    elif state == "customers_view":
        data = load_data()
        if text in data["customers"]:
            set_udata(uid, "selected", text); set_state(uid, "customers_action")
            c = data["customers"][text]
            send(cid, f"👤 الزبون: {text}\n💰 الرصيد: {c.get('balance',0):,} ل.س", customer_action_kb())

    elif state == "customers_action":
        name = get_udata(uid, "selected")
        data = load_data()
        c = data["customers"].get(name, {})
        if text == "📥 سحب":
            set_udata(uid, "cust_op", "withdraw"); set_state(uid, "customers_source")
            send(cid, "اختر مصدر السحب:", source_kb())
        elif text == "📤 دفع":
            set_state(uid, "customers_pay_amount"); send(cid, "ادخل مبلغ الدفع (ل.س):", back_keyboard())
        elif text == "👁 عرض الرصيد":
            send(cid, f"👤 {name}\n💰 الرصيد: {c.get('balance',0):,} ل.س", customer_action_kb())
        elif text == "📋 كشف الحساب":
            send(cid, format_customer_report(name, c), customer_action_kb())
        elif text == "🗑 حذف":
            if name in data["customers"]: del data["customers"][name]; save_data(data)
            send(cid, f"✅ تم حذف الزبون: {name}", customers_menu_kb())
            set_state(uid, "customers_menu")

    elif state == "customers_source":
        if text in [SOURCE_TODAY, SOURCE_OLD, SOURCE_GOODS]:
            set_udata(uid, "source", text); set_state(uid, "customers_amount")
            send(cid, "ادخل مبلغ السحب (ل.س):", back_keyboard())

    elif state == "customers_amount":
        try:
            amount = int(float(text))
            if amount <= 0: raise ValueError
            name = get_udata(uid, "selected"); source = get_udata(uid, "source")
            data = load_data()
            data["customers"][name]["balance"] += amount
            log_transaction(data["customers"][name], {
                "date": str(date.today()), "type": "سحب", "amount": amount, "source": source
            })
            save_data(data)
            new_bal = data["customers"][name]["balance"]
            send(cid,
                 f"✅ تم تسجيل السحب\n👤 {name}\n💸 {amount:,} ل.س\n📂 {source}\n💰 الرصيد: {new_bal:,} ل.س",
                 customer_action_kb())
            set_state(uid, "customers_action")
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً أكبر من الصفر:", back_keyboard())

    elif state == "customers_pay_amount":
        try:
            amount = int(float(text))
            if amount <= 0: raise ValueError
            name = get_udata(uid, "selected")
            data = load_data()
            old_bal = data["customers"][name].get("balance", 0)
            if amount > old_bal:
                send(cid, f"⚠️ المبلغ ({amount:,}) أكبر من الرصيد ({old_bal:,}).\nادخل مبلغاً مناسباً:", back_keyboard())
                return
            data["customers"][name]["balance"] -= amount
            log_transaction(data["customers"][name], {
                "date": str(date.today()), "type": "دفع", "amount": amount
            })
            save_data(data)
            new_bal = data["customers"][name]["balance"]
            send(cid,
                 f"✅ تم تسجيل الدفع\n👤 {name}\n💸 {amount:,} ل.س\n💰 الرصيد المتبقي: {new_bal:,} ل.س",
                 customer_action_kb())
            set_state(uid, "customers_action")
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً أكبر من الصفر:", back_keyboard())

    # ── SUPPLIERS ──
    elif state == "suppliers_menu":
        if text == "➕ إضافة تاجر":
            set_state(uid, "suppliers_add_name"); send(cid, "اكتب اسم التاجر الجديد:", back_keyboard())
        elif text == "👥 عرض التجار":
            data = load_data()
            show_list(message, uid, data["suppliers"], "suppliers_view",
                      "اختر تاجراً:", "لا يوجد تجار.", suppliers_menu_kb())

    elif state == "suppliers_add_name":
        data = load_data()
        if text in data["suppliers"]:
            send(cid, f"⚠️ التاجر '{text}' موجود مسبقاً.", suppliers_menu_kb())
            set_state(uid, "suppliers_menu")
        else:
            data["suppliers"][text] = {"log": []}
            save_data(data)
            send(cid, f"✅ تم إضافة التاجر: {text}", suppliers_menu_kb())
            set_state(uid, "suppliers_menu")

    elif state == "suppliers_view":
        data = load_data()
        if text in data["suppliers"]:
            set_udata(uid, "selected", text); set_state(uid, "suppliers_action")
            sdata = data["suppliers"][text]
            send(cid, f"🏪 التاجر: {text}\n{supplier_balance_line(sdata)}", supplier_action_kb())

    elif state == "suppliers_action":
        name = get_udata(uid, "selected")
        data = load_data()
        sdata = data["suppliers"].get(name, {})

        if text == "🛒 شراء بضائع":
            # بدء تدفق الشراء
            set_udata(uid, "sup_inv_no", None)
            set_state(uid, "sup_buy_inv")
            send(cid, "هل يوجد رقم فاتورة؟", invoice_kb())

        elif text == "💳 دفع للتاجر":
            # بدء تدفق الدفع
            set_udata(uid, "sup_inv_no", None)
            set_state(uid, "sup_pay_source")
            send(cid, "اختر مصدر الدفع:", source_kb())

        elif text == "👁 عرض الرصيد":
            send(cid, f"🏪 {name}\n{supplier_balance_line(sdata)}", supplier_action_kb())

        elif text == "📋 كشف الحساب":
            send(cid, format_supplier_report(name, sdata), supplier_action_kb())

        elif text == "🗑 حذف":
            if name in data["suppliers"]: del data["suppliers"][name]; save_data(data)
            send(cid, f"✅ تم حذف التاجر: {name}", suppliers_menu_kb())
            set_state(uid, "suppliers_menu")

    # ── شراء بضائع flow ──
    elif state == "sup_buy_inv":
        if text == INV_YES:
            set_state(uid, "sup_buy_inv_num"); send(cid, "أدخل رقم الفاتورة:", back_keyboard())
        elif text == INV_NO:
            set_udata(uid, "sup_inv_no", None)
            set_state(uid, "sup_buy_currency"); send(cid, "اختر نوع العملة:", currency_kb())

    elif state == "sup_buy_inv_num":
        set_udata(uid, "sup_inv_no", text)
        set_state(uid, "sup_buy_currency"); send(cid, "اختر نوع العملة:", currency_kb())

    elif state == "sup_buy_currency":
        if text in [CUR_SYP, CUR_USD]:
            set_udata(uid, "sup_currency", cur_key(text))
            set_state(uid, "sup_buy_amount"); send(cid, "أدخل قيمة الفاتورة:", back_keyboard())

    elif state == "sup_buy_amount":
        try:
            amount = int(float(text))
            if amount <= 0: raise ValueError
            set_udata(uid, "sup_buy_total", amount)
            set_state(uid, "sup_buy_payment"); send(cid, "هل توجد دفعة الآن؟", payment_exists_kb())
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً:", back_keyboard())

    elif state == "sup_buy_payment":
        if text == PAY_YES:
            set_state(uid, "sup_buy_pay_source"); send(cid, "اختر مصدر الدفعة:", source_kb())
        elif text == PAY_NO:
            # حفظ الشراء بدون دفعة
            name = get_udata(uid, "selected")
            inv_no = get_udata(uid, "sup_inv_no")
            currency = get_udata(uid, "sup_currency")
            amount = get_udata(uid, "sup_buy_total")
            data = load_data()
            log_transaction(data["suppliers"][name], {
                "date": str(date.today()), "type": "شراء",
                "invoice_no": inv_no, "currency": currency,
                "amount": amount, "paid_amount": 0, "paid_source": None, "paid_currency": None
            })
            save_data(data)
            inv_txt = f" | فاتورة: {inv_no}" if inv_no else ""
            send(cid,
                 f"✅ تم تسجيل الشراء\n🏪 {name}\n🛒 القيمة: {amount:,} {cur_label(currency)}{inv_txt}\n🚫 بدون دفعة",
                 supplier_action_kb())
            set_state(uid, "suppliers_action")

    elif state == "sup_buy_pay_source":
        if text in [SOURCE_TODAY, SOURCE_OLD, SOURCE_GOODS]:
            set_udata(uid, "sup_pay_source_val", text)
            set_state(uid, "sup_buy_pay_currency"); send(cid, "اختر عملة الدفعة:", currency_kb())

    elif state == "sup_buy_pay_currency":
        if text in [CUR_SYP, CUR_USD]:
            set_udata(uid, "sup_pay_currency", cur_key(text))
            set_state(uid, "sup_buy_pay_amount"); send(cid, "أدخل مبلغ الدفعة:", back_keyboard())

    elif state == "sup_buy_pay_amount":
        try:
            paid_amt = int(float(text))
            if paid_amt < 0: raise ValueError
            name = get_udata(uid, "selected")
            inv_no = get_udata(uid, "sup_inv_no")
            currency = get_udata(uid, "sup_currency")
            amount = get_udata(uid, "sup_buy_total")
            pay_src = get_udata(uid, "sup_pay_source_val")
            pay_cur = get_udata(uid, "sup_pay_currency")
            data = load_data()
            log_transaction(data["suppliers"][name], {
                "date": str(date.today()), "type": "شراء",
                "invoice_no": inv_no, "currency": currency, "amount": amount,
                "paid_amount": paid_amt, "paid_source": pay_src, "paid_currency": pay_cur
            })
            save_data(data)
            inv_txt = f" | فاتورة: {inv_no}" if inv_no else ""
            remaining = amount - paid_amt
            msg = (f"✅ تم تسجيل الشراء\n🏪 {name}{inv_txt}\n"
                   f"🛒 القيمة: {amount:,} {cur_label(currency)}\n"
                   f"💳 مدفوع: {paid_amt:,} {cur_label(pay_cur)} ({pay_src})")
            if remaining > 0:
                msg += f"\n⚠️ المتبقي: {remaining:,} {cur_label(currency)}"
            send(cid, msg, supplier_action_kb())
            set_state(uid, "suppliers_action")
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً:", back_keyboard())

    # ── دفع للتاجر flow ──
    elif state == "sup_pay_source":
        if text in [SOURCE_TODAY, SOURCE_OLD, SOURCE_GOODS]:
            set_udata(uid, "sup_pay_source_val", text)
            set_state(uid, "sup_pay_currency"); send(cid, "اختر نوع العملة:", currency_kb())

    elif state == "sup_pay_currency":
        if text in [CUR_SYP, CUR_USD]:
            set_udata(uid, "sup_currency", cur_key(text))
            set_state(uid, "sup_pay_inv"); send(cid, "هل يوجد رقم فاتورة للدفعة؟", invoice_kb())

    elif state == "sup_pay_inv":
        if text == INV_YES:
            set_state(uid, "sup_pay_inv_num"); send(cid, "أدخل رقم الفاتورة:", back_keyboard())
        elif text == INV_NO:
            set_udata(uid, "sup_inv_no", None)
            set_state(uid, "sup_pay_amount"); send(cid, "أدخل مبلغ الدفعة:", back_keyboard())

    elif state == "sup_pay_inv_num":
        set_udata(uid, "sup_inv_no", text)
        set_state(uid, "sup_pay_amount"); send(cid, "أدخل مبلغ الدفعة:", back_keyboard())

    elif state == "sup_pay_amount":
        try:
            amount = int(float(text))
            if amount <= 0: raise ValueError
            name = get_udata(uid, "selected")
            source = get_udata(uid, "sup_pay_source_val")
            currency = get_udata(uid, "sup_currency")
            inv_no = get_udata(uid, "sup_inv_no")
            data = load_data()
            log_transaction(data["suppliers"][name], {
                "date": str(date.today()), "type": "دفع",
                "source": source, "currency": currency,
                "amount": amount, "invoice_no": inv_no
            })
            save_data(data)
            inv_txt = f" | فاتورة: {inv_no}" if inv_no else ""
            send(cid,
                 f"✅ تم تسجيل الدفعة\n🏪 {name}\n💳 {amount:,} {cur_label(currency)}\n📂 {source}{inv_txt}",
                 supplier_action_kb())
            set_state(uid, "suppliers_action")
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً:", back_keyboard())

    # ── EXPENSES ──
    elif state == "expenses_menu":
        if text == "➕ إضافة مصروف":
            set_state(uid, "expenses_add_name"); send(cid, "اكتب اسم/وصف المصروف:", back_keyboard())
        elif text == "📋 عرض المصاريف":
            data = load_data()
            show_list(message, uid, data["expenses"], "expenses_view",
                      "اختر مصروفاً:", "لا يوجد مصاريف.", expenses_menu_kb())

    elif state == "expenses_add_name":
        set_udata(uid, "expense_name", text)
        set_state(uid, "expenses_add_source")
        send(cid, "اختر مصدر الدفع:", source_kb())

    elif state == "expenses_add_source":
        if text in [SOURCE_TODAY, SOURCE_OLD, SOURCE_GOODS]:
            set_udata(uid, "expense_source", text)
            set_state(uid, "expenses_add_amount")
            send(cid, "أدخل قيمة المصروف (ل.س):", back_keyboard())

    elif state == "expenses_add_amount":
        try:
            amount = int(float(text))
            if amount <= 0: raise ValueError
            name = get_udata(uid, "expense_name")
            source = get_udata(uid, "expense_source")
            data = load_data()
            if name not in data["expenses"]:
                data["expenses"][name] = {"log": []}
            log_transaction(data["expenses"][name], {
                "date": str(date.today()), "type": "مصروف", "amount": amount, "source": source
            })
            save_data(data)
            send(cid, f"✅ تم تسجيل المصروف\n📝 {name}\n💸 {amount:,} ل.س\n📂 {source}", expenses_menu_kb())
            set_state(uid, "expenses_menu")
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً:", back_keyboard())

    elif state == "expenses_view":
        data = load_data()
        if text in data["expenses"]:
            set_udata(uid, "selected", text); set_state(uid, "expenses_action")
            edata = data["expenses"][text]
            total = sum(t.get("amount", 0) for t in edata.get("log", []))
            send(cid, f"💸 {text}\n💴 الإجمالي: {total:,} ل.س", expense_action_kb())

    elif state == "expenses_action":
        name = get_udata(uid, "selected")
        data = load_data()
        edata = data["expenses"].get(name, {})
        if text == "📋 كشف الحساب":
            log = sorted(edata.get("log", []), key=lambda x: x.get("date", ""))
            lines = [f"📋 كشف المصروف: {name}", "─" * 22]
            if not log:
                lines.append("لا توجد معاملات.")
            else:
                for t in log:
                    lines.append(f"📅 {format_date(t['date'])}: {t['amount']:,} ل.س")
            total = sum(t.get("amount", 0) for t in log)
            lines += ["─" * 22, f"💸 الإجمالي: {total:,} ل.س"]
            send(cid, "\n".join(lines), expense_action_kb())
        elif text == "🗑 حذف":
            if name in data["expenses"]: del data["expenses"][name]; save_data(data)
            send(cid, f"✅ تم حذف المصروف: {name}", expenses_menu_kb())
            set_state(uid, "expenses_menu")

    # ── SALES ──
    elif state == "sales_menu":
        if text == "📋 سجل":
            show_sales_register(message, uid)
        elif text == "📊 ملخص المبيعات":
            data = load_data()
            send(cid, format_all_sales_summary(data), sales_menu_kb())

    elif state == "sales_register":
        # المستخدم اختار يوماً من السجل
        data = load_data()
        days_sorted = get_udata(uid, "register_days") or []
        # البحث عن اليوم المختار من النص
        selected_iso = None
        for iso in days_sorted:
            if format_date(iso) in text:
                selected_iso = iso
                break
        if selected_iso:
            set_udata(uid, "selected_day", selected_iso)
            set_state(uid, "sales_day_detail")
            send(cid, format_day_detail(data, selected_iso), day_action_kb())

    elif state == "sales_day_detail":
        if text == "✏️ تسجيل/تعديل الغلة":
            iso = get_udata(uid, "selected_day")
            data = load_data()
            p = get_current_period(data)
            current_val = 0
            if p:
                current_val = p["days"].get(iso, {}).get("syp", 0)
            set_state(uid, "sales_day_edit")
            send(cid,
                 f"📆 {format_date(iso)}\n💵 الغلة الحالية: {current_val:,} ل.س\nأدخل قيمة الغلة الجديدة (ل.س):",
                 back_keyboard())

    elif state == "sales_day_edit":
        try:
            amount = int(float(text))
            if amount < 0: raise ValueError
            iso = get_udata(uid, "selected_day")
            data = load_data()
            p = get_current_period(data)
            if not p:
                send(cid, "⚠️ لا توجد فترة مبيعات جارية.", sales_menu_kb())
                set_state(uid, "sales_menu")
                return
            if iso not in p["days"]:
                p["days"][iso] = {"syp": 0}
            p["days"][iso]["syp"] = amount
            save_data(data)
            data2 = load_data()
            # عرض تفاصيل اليوم المحدّث
            detail = format_day_detail(data2, iso)
            set_state(uid, "sales_day_detail")
            send(cid, f"✅ تم تسجيل الغلة: {amount:,} ل.س\n\n{detail}", day_action_kb())
        except (ValueError, TypeError):
            send(cid, "⚠️ أدخل رقماً صحيحاً:", back_keyboard())

    # ── INVENTORY / ARCHIVE ──
    elif state == "inventory_menu":
        if text in ("📦 تصفير المصاريف فقط", "📊 تصفير المبيعات فقط", "🔄 تصفير الكل (مصاريف + مبيعات)"):
            set_udata(uid, "inv_action", text)
            set_state(uid, "inventory_confirm")
            send(cid, f"⚠️ هل أنت متأكد من:\n{text}\n\nسيتم حفظ نسخة أرشيفية.", confirm_kb(text))
        elif text == "📚 السجلات القديمة":
            data = load_data()
            archives = data.get("archives", [])
            if not archives:
                send(cid, "لا توجد سجلات محفوظة.", inventory_menu_kb())
            else:
                m = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
                for a in reversed(archives): m.add(a["label"])
                m.add("🔙 رجوع")
                set_state(uid, "archives_list"); send(cid, "اختر سجلاً:", m)

    elif state == "inventory_confirm":
        action = get_udata(uid, "inv_action")
        if text == f"✅ تأكيد {action}":
            data = load_data()
            reset_exp = "مصاريف" in action or "الكل" in action
            reset_sal = "مبيعات" in action or "الكل" in action
            label = do_archive_and_reset(data, reset_exp, reset_sal)
            set_state(uid, "main")
            send(cid, f"✅ تم الجرد بنجاح!\n📦 تم حفظ النسخة: {label}", main_keyboard())
        elif text == "❌ إلغاء":
            set_state(uid, "inventory_menu"); send(cid, "تم الإلغاء.", inventory_menu_kb())

    elif state == "archives_list":
        data = load_data()
        for arch in data.get("archives", []):
            if text == arch["label"]:
                send(cid, format_archive_summary(arch), inventory_menu_kb())
                set_state(uid, "inventory_menu"); return

    # ── SETTINGS ──
    elif state == "settings_menu":
        data = load_data()
        s = data.get("settings", {})
        enabled = s.get("notify_enabled", True)
        if text in ("🔕 إيقاف التذكير", "🔔 تفعيل التذكير"):
            s["notify_enabled"] = not enabled; data["settings"] = s; save_data(data)
            status = "مفعّل ✅" if s["notify_enabled"] else "موقوف 🔕"
            send(cid, f"التذكير: {status}", settings_kb(data))
        elif text.startswith("⏰ تغيير الوقت"):
            set_state(uid, "settings_set_hour"); send(cid, "أدخل الساعة (0-23):", back_keyboard())

    elif state == "settings_set_hour":
        try:
            hour = int(text)
            if not 0 <= hour <= 23: raise ValueError
            data = load_data(); data["settings"]["notify_hour"] = hour; save_data(data)
            set_state(uid, "settings_menu"); send(cid, f"✅ وقت التذكير: {hour}:00", settings_kb(data))
        except ValueError:
            send(cid, "⚠️ أدخل رقماً بين 0 و 23.", back_keyboard())


print("✅ البوت يعمل الآن...")
load_data()
threading.Thread(target=daily_notification_thread, daemon=True).start()
bot.infinity_polling()
