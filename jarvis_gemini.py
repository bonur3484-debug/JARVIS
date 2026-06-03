import os
import json
import asyncio
import logging
import base64
from datetime import datetime
import httpx

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

JARVIS_SYSTEM = """Ты JARVIS — персональный ИИ-ассистент, как в фильме «Железный человек».
Ты умный, вежливый, немного остроумный. Говоришь только на русском языке.
Ты помогаешь с любыми задачами: вопросы, анализ, написание текстов, советы, планирование.
Отвечай структурированно, используй эмодзи для наглядности."""

DATA_FILE = "jarvis_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_data(user_id):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"history": [], "reminders": [], "notes": [], "tasks": []}
        save_data(data)
    return data[uid]

def save_user_data(user_id, user_data):
    data = load_data()
    data[str(user_id)] = user_data
    save_data(data)

async def ask_gemini(history, image_b64=None, doc_text=None):
    contents = []
    # system as first user message
    contents.append({"role": "user", "parts": [{"text": JARVIS_SYSTEM}]})
    contents.append({"role": "model", "parts": [{"text": "Понял. Готов помочь!"}]})
    
    for msg in history[:-1]:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    
    # last message (may include image or doc)
    last = history[-1]["content"]
    parts = []
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    if doc_text:
        parts.append({"text": doc_text + "\n\n" + last})
    else:
        parts.append({"text": last})
    contents.append({"role": "user", "parts": parts})

    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": 2048}}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

def check_access(user_id):
    return ALLOWED_USER_ID == 0 or user_id == ALLOWED_USER_ID

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_access(update.effective_user.id): return
    await update.message.reply_text(
        "🤖 *JARVIS активирован!*\n\n"
        "Я ваш персональный ИИ-ассистент на базе Google Gemini.\n\n"
        "📋 Напишите /help для списка команд\n"
        "💬 Или просто напишите мне что угодно!",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_access(update.effective_user.id): return
    await update.message.reply_text(
        "🤖 *JARVIS — Команды*\n\n"
        "💬 *Чат:* просто пишите любой вопрос\n"
        "🖼 *Фото:* отправьте фото — опишу и проанализирую\n"
        "📄 *Документ:* отправьте файл — прочитаю и объясню\n\n"
        "✅ *Задачи:*\n"
        "/tasks — все задачи\n"
        "/addtask текст — добавить\n"
        "/donetask 1 — выполнить\n"
        "/deltask 1 — удалить\n\n"
        "📝 *Заметки:*\n"
        "/notes — все заметки\n"
        "/addnote текст — добавить\n"
        "/delnote 1 — удалить\n\n"
        "⏰ *Напоминания:*\n"
        "/reminders — все напоминания\n"
        "/remind 14:30 текст — поставить\n"
        "/delremind 1 — удалить\n\n"
        "📊 /summary — сводка дня\n"
        "🧹 /clear — очистить историю",
        parse_mode="Markdown"
    )

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    tasks = get_user_data(uid).get("tasks", [])
    if not tasks:
        await update.message.reply_text("📋 Задач нет.\nДобавьте: /addtask купить продукты")
        return
    lines = ["📋 *Ваши задачи:*\n"]
    for i, t in enumerate(tasks, 1):
        lines.append(f"{'✅' if t['done'] else '⬜'} {i}. {t['text']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_addtask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("❗ Напишите: /addtask позвонить клиенту")
        return
    ud = get_user_data(uid)
    ud["tasks"].append({"text": text, "done": False, "created": datetime.now().isoformat()})
    save_user_data(uid, ud)
    await update.message.reply_text(f"✅ Задача добавлена: *{text}*", parse_mode="Markdown")

async def cmd_donetask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    try:
        idx = int(context.args[0]) - 1
        ud["tasks"][idx]["done"] = True
        save_user_data(uid, ud)
        await update.message.reply_text(f"✅ Задача #{idx+1} выполнена!")
    except: await update.message.reply_text("❗ Неверный номер. Пример: /donetask 1")

async def cmd_deltask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    try:
        idx = int(context.args[0]) - 1
        removed = ud["tasks"].pop(idx)
        save_user_data(uid, ud)
        await update.message.reply_text(f"🗑 Удалено: {removed['text']}")
    except: await update.message.reply_text("❗ Неверный номер.")

async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    notes = get_user_data(uid).get("notes", [])
    if not notes:
        await update.message.reply_text("📝 Заметок нет.\nДобавьте: /addnote текст")
        return
    lines = ["📝 *Ваши заметки:*\n"]
    for i, n in enumerate(notes, 1):
        lines.append(f"{i}. {n['text']}\n   _{n['created'][:10]}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_addnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("❗ Напишите: /addnote важная идея")
        return
    ud = get_user_data(uid)
    ud["notes"].append({"text": text, "created": datetime.now().isoformat()})
    save_user_data(uid, ud)
    await update.message.reply_text(f"📝 Заметка сохранена: *{text}*", parse_mode="Markdown")

async def cmd_delnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    try:
        idx = int(context.args[0]) - 1
        removed = ud["notes"].pop(idx)
        save_user_data(uid, ud)
        await update.message.reply_text(f"🗑 Удалено: {removed['text']}")
    except: await update.message.reply_text("❗ Неверный номер.")

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    active = [r for r in get_user_data(uid).get("reminders", []) if not r.get("fired")]
    if not active:
        await update.message.reply_text("⏰ Напоминаний нет.\nДобавьте: /remind 14:30 встреча")
        return
    lines = ["⏰ *Напоминания:*\n"]
    for i, r in enumerate(active, 1):
        lines.append(f"{i}. 🕐 {r['time']} — {r['text']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    if len(context.args) < 2:
        await update.message.reply_text("❗ Формат: /remind 14:30 встреча с клиентом")
        return
    time_str = context.args[0]
    text = " ".join(context.args[1:])
    try:
        hour, minute = map(int, time_str.split(":"))
        ud = get_user_data(uid)
        ud["reminders"].append({"time": time_str, "hour": hour, "minute": minute,
                                  "text": text, "fired": False, "created": datetime.now().isoformat()})
        save_user_data(uid, ud)
        await update.message.reply_text(f"⏰ Напоминание: *{time_str}* — {text}", parse_mode="Markdown")
    except: await update.message.reply_text("❗ Неверный формат. Пример: /remind 09:00 позвонить")

async def cmd_delremind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    active = [r for r in ud["reminders"] if not r.get("fired")]
    try:
        idx = int(context.args[0]) - 1
        rem = active[idx]
        ud["reminders"].remove(rem)
        save_user_data(uid, ud)
        await update.message.reply_text(f"🗑 Удалено: {rem['text']}")
    except: await update.message.reply_text("❗ Неверный номер.")

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    tasks = ud.get("tasks", [])
    pending = [t for t in tasks if not t["done"]]
    done_t = [t for t in tasks if t["done"]]
    notes = ud.get("notes", [])
    reminders = [r for r in ud.get("reminders", []) if not r.get("fired")]
    now = datetime.now()
    text = (f"📊 *Сводка на {now.strftime('%d.%m.%Y %H:%M')}*\n\n"
            f"✅ Задачи: {len(done_t)} выполнено / {len(pending)} в ожидании\n"
            f"📝 Заметки: {len(notes)} шт.\n"
            f"⏰ Напоминаний: {len(reminders)} активных\n")
    if pending:
        text += "\n📋 *Задачи в ожидании:*\n" + "\n".join(f"  ⬜ {t['text']}" for t in pending[:5])
    if reminders:
        text += "\n\n⏰ *Напоминания:*\n" + "\n".join(f"  🕐 {r['time']} — {r['text']}" for r in reminders[:5])
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    ud = get_user_data(uid)
    ud["history"] = []
    save_user_data(uid, ud)
    await update.message.reply_text("🧹 История очищена!")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    await update.message.chat.send_action("typing")
    ud = get_user_data(uid)
    history = ud.get("history", [])
    history.append({"role": "user", "content": update.message.text})
    if len(history) > 20: history = history[-20:]
    try:
        reply = await ask_gemini(history)
        history.append({"role": "assistant", "content": reply})
        ud["history"] = history
        save_user_data(uid, ud)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте ещё раз.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(file.file_path)
        img_b64 = base64.b64encode(resp.content).decode()
    caption = update.message.caption or "Опиши и проанализируй это изображение подробно."
    try:
        history = [{"role": "user", "content": caption}]
        reply = await ask_gemini(history, image_b64=img_b64)
        await update.message.reply_text(f"🖼 *Анализ изображения:*\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.message.reply_text("⚠️ Не удалось обработать фото.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_access(uid): return
    await update.message.chat.send_action("typing")
    doc = update.message.document
    file = await context.bot.get_file(doc.file_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(file.file_path)
        content = resp.content
    caption = update.message.caption or "Прочитай и подробно объясни содержимое этого документа."
    try:
        text_content = content.decode("utf-8", errors="ignore")[:8000]
        doc_text = f"Документ '{doc.file_name}':\n\n{text_content}"
        history = [{"role": "user", "content": caption}]
        reply = await ask_gemini(history, doc_text=doc_text)
        await update.message.reply_text(f"📄 *Анализ документа:*\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await update.message.reply_text("⚠️ Не удалось обработать документ.")

async def check_reminders(app):
    now = datetime.now()
    data = load_data()
    changed = False
    for uid_str, ud in data.items():
        for r in ud.get("reminders", []):
            if r.get("fired"): continue
            if r["hour"] == now.hour and r["minute"] == now.minute:
                try:
                    await app.bot.send_message(int(uid_str), f"⏰ *НАПОМИНАНИЕ!*\n\n{r['text']}", parse_mode="Markdown")
                    r["fired"] = True
                    changed = True
                except Exception as e:
                    logger.error(f"Reminder error: {e}")
    if changed: save_data(data)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("addtask", cmd_addtask))
    app.add_handler(CommandHandler("donetask", cmd_donetask))
    app.add_handler(CommandHandler("deltask", cmd_deltask))
    app.add_handler(CommandHandler("notes", cmd_notes))
    app.add_handler(CommandHandler("addnote", cmd_addnote))
    app.add_handler(CommandHandler("delnote", cmd_delnote))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("delremind", cmd_delremind))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.ensure_future(check_reminders(app)), "cron", minute="*")
    scheduler.start()
    logger.info("🤖 JARVIS (Gemini) запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
