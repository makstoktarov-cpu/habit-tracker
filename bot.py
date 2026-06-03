import asyncio
import json
import os
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp, BotCommand
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-domain.com")  # URL where miniapp is hosted
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Almaty")


# ── Data helpers ──────────────────────────────────────────────────────────────

def get_user_file(user_id: int) -> Path:
    return DATA_DIR / f"{user_id}.json"

def load_user(user_id: int) -> dict:
    f = get_user_file(user_id)
    if f.exists():
        return json.loads(f.read_text())
    return {"habits": [], "reminders": {}, "chat_id": user_id}

def save_user(user_id: int, data: dict):
    get_user_file(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2))

def today_str() -> str:
    return date.today().isoformat()

def get_all_users() -> list[int]:
    return [int(f.stem) for f in DATA_DIR.glob("*.json")]


# ── /start ─────────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    data = load_user(user_id)
    data["chat_id"] = message.chat.id
    save_user(user_id, data)

    kb = InlineKeyboardBuilder()
    kb.button(
        text="📊 Открыть трекер привычек",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/miniapp/")
    )
    kb.button(text="⏰ Настроить уведомления", callback_data="setup_reminders")
    kb.button(text="📅 Скачать календарь (.ics)", callback_data="export_ics")
    kb.button(text="📈 Статистика дня", callback_data="daily_stats")
    kb.adjust(1)

    await message.answer(
        "👋 Привет! Я твой трекер привычек.\n\n"
        "• Открой трекер — выбери привычки и отмечай каждый день\n"
        "• Настрой уведомления — бот напомнит в нужное время\n"
        "• Скачай .ics — добавь привычки в календарь телефона\n\n"
        "Начнём?",
        reply_markup=kb.as_markup()
    )

    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(
            text="Трекер",
            web_app=WebAppInfo(url=f"{WEBAPP_URL}/miniapp/")
        )
    )


# ── /stats ─────────────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
@dp.callback_query(F.data == "daily_stats")
async def daily_stats(update: types.Message | types.CallbackQuery):
    msg = update if isinstance(update, types.Message) else update.message
    user_id = update.from_user.id
    data = load_user(user_id)
    habits = data.get("habits", [])

    if not habits:
        text = "У тебя пока нет привычек. Открой трекер и выбери!"
    else:
        today = today_str()
        done = [h for h in habits if h.get("done", {}).get(today)]
        total = len(habits)
        pct = round(len(done) / total * 100) if total else 0

        lines = [f"📊 *Сегодня, {date.today().strftime('%d.%m.%Y')}*\n"]
        lines.append(f"Выполнено: {len(done)} / {total} ({pct}%)\n")

        # streak calc
        best_streak = 0
        for h in habits:
            s = 0
            for i in range(90):
                d = (date.today() - timedelta(days=i)).isoformat()
                if h.get("done", {}).get(d):
                    s += 1
                else:
                    break
            best_streak = max(best_streak, s)

        lines.append(f"🔥 Лучший стрик: {best_streak} дн.\n")

        if done:
            lines.append("\n✅ Выполнено:")
            for h in done:
                lines.append(f"  • {h['name']}")
        not_done = [h for h in habits if not h.get("done", {}).get(today)]
        if not_done:
            lines.append("\n⏳ Осталось:")
            for h in not_done[:5]:
                lines.append(f"  • {h['name']}")
            if len(not_done) > 5:
                lines.append(f"  ... и ещё {len(not_done)-5}")

        text = "\n".join(lines)

    kb = InlineKeyboardBuilder()
    kb.button(
        text="📊 Открыть трекер",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/miniapp/")
    )
    kb.adjust(1)

    if isinstance(update, types.CallbackQuery):
        await update.answer()
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb.as_markup())
    else:
        await msg.answer(text, parse_mode="Markdown", reply_markup=kb.as_markup())


# ── Reminders setup ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "setup_reminders")
async def setup_reminders(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    data = load_user(user_id)
    habits = data.get("habits", [])

    if not habits:
        await call.message.answer(
            "Сначала выбери привычки в трекере, потом настроим уведомления."
        )
        return

    kb = InlineKeyboardBuilder()
    for h in habits:
        reminder = data.get("reminders", {}).get(h["key"], "")
        icon = "🔔" if reminder else "🔕"
        kb.button(
            text=f"{icon} {h['name']} {('— '+reminder) if reminder else ''}",
            callback_data=f"set_reminder:{h['key']}"
        )
    kb.button(text="✅ Готово", callback_data="reminders_done")
    kb.adjust(1)

    await call.message.answer(
        "⏰ *Настройка уведомлений*\n\n"
        "Нажми на привычку чтобы задать время напоминания (формат: ЧЧ:ММ, например 07:30).\n"
        "Время — Алматы (UTC+5).",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("set_reminder:"))
async def set_reminder_prompt(call: types.CallbackQuery):
    await call.answer()
    key = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    data = load_user(user_id)
    habit = next((h for h in data.get("habits", []) if h["key"] == key), None)
    if not habit:
        return

    # Store pending state
    data["_pending_reminder"] = key
    save_user(user_id, data)

    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Убрать напоминание", callback_data=f"clear_reminder:{key}")
    kb.button(text="← Назад", callback_data="setup_reminders")
    kb.adjust(1)

    await call.message.answer(
        f"⏰ *{habit['name']}*\n\n"
        f"Напиши время в формате *ЧЧ:ММ* (например `07:30`)\n"
        f"или выбери ниже:",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )


@dp.message(F.text.regexp(r"^\d{1,2}:\d{2}$"))
async def handle_time_input(message: types.Message):
    user_id = message.from_user.id
    data = load_user(user_id)
    pending_key = data.get("_pending_reminder")
    if not pending_key:
        return

    time_str = message.text.strip()
    try:
        h, m = map(int, time_str.split(":"))
        assert 0 <= h <= 23 and 0 <= m <= 59
        time_str = f"{h:02d}:{m:02d}"
    except Exception:
        await message.answer("Неверный формат. Напиши как 07:30")
        return

    if "reminders" not in data:
        data["reminders"] = {}
    data["reminders"][pending_key] = time_str
    del data["_pending_reminder"]
    save_user(user_id, data)

    habit = next((h for h in data.get("habits", []) if h["key"] == pending_key), None)
    name = habit["name"] if habit else pending_key

    # Reschedule
    schedule_user_reminders(user_id, data)

    await message.answer(
        f"✅ Напоминание для *{name}* установлено на *{time_str}* (Алматы)",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("clear_reminder:"))
async def clear_reminder(call: types.CallbackQuery):
    await call.answer()
    key = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    data = load_user(user_id)
    data.get("reminders", {}).pop(key, None)
    data.pop("_pending_reminder", None)
    save_user(user_id, data)
    schedule_user_reminders(user_id, data)
    await call.message.answer("🔕 Напоминание убрано")


@dp.callback_query(F.data == "reminders_done")
async def reminders_done(call: types.CallbackQuery):
    await call.answer("Готово!")
    await call.message.answer("✅ Уведомления настроены. Буду напоминать в указанное время!")


# ── Scheduler ──────────────────────────────────────────────────────────────────

def schedule_user_reminders(user_id: int, data: dict):
    # Remove old jobs for this user
    for job in scheduler.get_jobs():
        if job.id.startswith(f"reminder_{user_id}_"):
            job.remove()

    habits_map = {h["key"]: h for h in data.get("habits", [])}
    for key, time_str in data.get("reminders", {}).items():
        habit = habits_map.get(key)
        if not habit:
            continue
        try:
            h, m = map(int, time_str.split(":"))
        except Exception:
            continue

        job_id = f"reminder_{user_id}_{key.replace(':', '_').replace(' ', '_')}"
        scheduler.add_job(
            send_habit_reminder,
            CronTrigger(hour=h, minute=m, timezone="Asia/Almaty"),
            args=[user_id, data["chat_id"], habit["name"], key],
            id=job_id,
            replace_existing=True
        )
    logger.info(f"Scheduled {len(data.get('reminders', {}))} reminders for user {user_id}")


async def send_habit_reminder(user_id: int, chat_id: int, habit_name: str, habit_key: str):
    data = load_user(user_id)
    today = today_str()
    habit = next((h for h in data.get("habits", []) if h["key"] == habit_key), None)
    if not habit:
        return

    already_done = habit.get("done", {}).get(today, False)
    if already_done:
        return  # Already checked off today — no need to nag

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Выполнено!", callback_data=f"quick_done:{habit_key}")
    kb.button(
        text="📊 Открыть трекер",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/miniapp/")
    )
    kb.adjust(1)

    await bot.send_message(
        chat_id,
        f"⏰ Напоминание: *{habit_name}*\n\nНе забудь отметить сегодня!",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )


@dp.callback_query(F.data.startswith("quick_done:"))
async def quick_done(call: types.CallbackQuery):
    key = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    data = load_user(user_id)
    habit = next((h for h in data.get("habits", []) if h["key"] == key), None)
    if not habit:
        await call.answer("Привычка не найдена")
        return

    today = today_str()
    if "done" not in habit:
        habit["done"] = {}
    habit["done"][today] = True
    save_user(user_id, data)
    await call.answer(f"✅ {habit['name']} — отмечено!")
    await call.message.edit_text(
        f"✅ *{habit['name']}* выполнено сегодня!\n\nОтличная работа 💪",
        parse_mode="Markdown"
    )


# ── Calendar (.ics) export ─────────────────────────────────────────────────────

@dp.callback_query(F.data == "export_ics")
async def export_ics(call: types.CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    data = load_user(user_id)
    habits = data.get("habits", [])
    reminders = data.get("reminders", {})

    if not habits:
        await call.message.answer("Сначала выбери привычки в трекере.")
        return

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HabitBot//KM//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Трекер привычек",
        "X-WR-TIMEZONE:Asia/Almaty",
    ]

    today = date.today()
    uid_base = int(today.strftime("%Y%m%d"))

    for i, habit in enumerate(habits):
        time_str = reminders.get(habit["key"], "08:00")
        try:
            h, m = map(int, time_str.split(":"))
        except Exception:
            h, m = 8, 0

        # Create recurring daily event starting today
        dtstart = today.strftime(f"%Y%m%dT{h:02d}{m:02d}00")
        dtend_dt = today.replace()
        dtend = today.strftime(f"%Y%m%dT{h:02d}{m+15 if m+15 < 60 else m:02d}00")
        uid = f"{uid_base}{i:03d}@habitbot"

        goal_meta = ""
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;TZID=Asia/Almaty:{dtstart}",
            f"DTEND;TZID=Asia/Almaty:{dtend}",
            f"RRULE:FREQ=DAILY",
            f"SUMMARY:{habit['name']}",
            f"DESCRIPTION:Привычка из трекера КМ",
            f"BEGIN:VALARM",
            f"TRIGGER:-PT5M",
            f"ACTION:DISPLAY",
            f"DESCRIPTION:Напоминание: {habit['name']}",
            f"END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(lines)

    # Save and send as file
    ics_path = DATA_DIR / f"{user_id}_habits.ics"
    ics_path.write_text(ics_content, encoding="utf-8")

    await call.message.answer_document(
        types.FSInputFile(ics_path, filename="привычки.ics"),
        caption=(
            "📅 *Календарь привычек*\n\n"
            "Как добавить на телефон:\n"
            "• *iOS*: открой файл → «Добавить все» → готово\n"
            "• *Android*: открой в Google Календарь → импорт\n\n"
            "Все привычки добавятся как ежедневные события с напоминаниями!"
        ),
        parse_mode="Markdown"
    )


# ── Webapp data sync ───────────────────────────────────────────────────────────

@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    """Receives habit data from Mini App when user saves"""
    user_id = message.from_user.id
    try:
        payload = json.loads(message.web_app_data.data)
        action = payload.get("action")

        data = load_user(user_id)
        data["chat_id"] = message.chat.id

        if action == "sync_habits":
            data["habits"] = payload.get("habits", [])
            save_user(user_id, data)
            schedule_user_reminders(user_id, data)
            await message.answer("✅ Привычки синхронизированы!")

        elif action == "toggle_habit":
            key = payload.get("key")
            date_str = payload.get("date", today_str())
            habit = next((h for h in data.get("habits", []) if h["key"] == key), None)
            if habit:
                if "done" not in habit:
                    habit["done"] = {}
                habit["done"][date_str] = not habit["done"].get(date_str, False)
                save_user(user_id, data)

    except Exception as e:
        logger.error(f"Webapp data error: {e}")


# ── Evening summary (21:00 Almaty) ────────────────────────────────────────────

async def send_evening_summary():
    for user_id in get_all_users():
        try:
            data = load_user(user_id)
            habits = data.get("habits", [])
            if not habits:
                continue

            today = today_str()
            done = [h for h in habits if h.get("done", {}).get(today)]
            not_done = [h for h in habits if not h.get("done", {}).get(today)]
            pct = round(len(done) / len(habits) * 100)

            if pct == 100:
                emoji, msg = "🏆", "Идеальный день! Все привычки выполнены!"
            elif pct >= 70:
                emoji, msg = "💪", "Отличный день!"
            elif pct >= 40:
                emoji, msg = "📈", "Неплохо, но есть куда расти."
            else:
                emoji, msg = "💡", "Завтра новый шанс!"

            text = (
                f"{emoji} *Итоги дня* — {date.today().strftime('%d.%m')}\n\n"
                f"Выполнено: {len(done)}/{len(habits)} ({pct}%) — {msg}"
            )

            if not_done:
                text += "\n\n⏳ Не отмечено:\n"
                text += "\n".join(f"  • {h['name']}" for h in not_done[:5])

            kb = InlineKeyboardBuilder()
            kb.button(
                text="📊 Отметить сейчас",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}/miniapp/")
            )
            kb.adjust(1)

            await bot.send_message(
                data["chat_id"], text,
                parse_mode="Markdown",
                reply_markup=kb.as_markup()
            )
        except Exception as e:
            logger.error(f"Evening summary error for {user_id}: {e}")


# ── Bot commands menu ──────────────────────────────────────────────────────────

async def set_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="stats", description="Статистика сегодня"),
    ])


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    await set_commands()

    # Load existing reminders on startup
    for user_id in get_all_users():
        data = load_user(user_id)
        schedule_user_reminders(user_id, data)

    # Evening summary at 21:00 Almaty
    scheduler.add_job(
        send_evening_summary,
        CronTrigger(hour=21, minute=0, timezone="Asia/Almaty"),
        id="evening_summary",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
