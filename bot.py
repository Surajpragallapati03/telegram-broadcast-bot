
import os
import sqlite3
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
DB_FILE = os.environ.get("DB_FILE", "broadcast.db")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups_list (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat INTEGER,
            source_message INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_messages (
            broadcast_id INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (broadcast_id, chat_id)
        )
    """)
    conn.commit()
    return conn


def is_owner(update: Update) -> bool:
    return (
        OWNER_ID != 0
        and update.effective_user is not None
        and update.effective_user.id == OWNER_ID
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.effective_message.reply_text(
        f"Your Telegram user ID is: {user_id}\n\n"
        "Keep this ID private. Add it as OWNER_ID in your hosting "
        "environment variables before using admin commands.\n\n"
        "Once configured, send messages here to broadcast them."
    )


async def addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.effective_message.reply_text("Not authorized.")
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Run /addgroup inside the destination group."
        )
        return

    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO groups_list VALUES (?, ?)",
        (chat.id, chat.title or str(chat.id)),
    )
    conn.commit()
    conn.close()

    await update.effective_message.reply_text(
        f"Added '{chat.title}' to the broadcast list."
    )


async def removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.effective_message.reply_text("Not authorized.")
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            "Run /removegroup inside the destination group."
        )
        return

    conn = db()
    conn.execute("DELETE FROM groups_list WHERE chat_id = ?", (chat.id,))
    conn.commit()
    conn.close()

    await update.effective_message.reply_text(
        "This group was removed from the broadcast list."
    )


async def groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.effective_message.reply_text("Not authorized.")
        return

    conn = db()
    rows = conn.execute(
        "SELECT title, chat_id FROM groups_list ORDER BY title"
    ).fetchall()
    conn.close()

    if not rows:
        await update.effective_message.reply_text(
            "No groups registered yet. Add the bot to a group and use /addgroup."
        )
        return

    result = "\n".join(
        f"• {title} ({chat_id})" for title, chat_id in rows
    )
    await update.effective_message.reply_text("Broadcast groups:\n\n" + result)


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.effective_message.reply_text("Not authorized.")
        return

    conn = db()
    row = conn.execute(
        "SELECT id FROM broadcasts ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if not row:
        conn.close()
        await update.effective_message.reply_text("Nothing to undo.")
        return

    broadcast_id = row[0]
    sent = conn.execute(
        "SELECT chat_id, message_id FROM sent_messages "
        "WHERE broadcast_id = ?",
        (broadcast_id,),
    ).fetchall()

    failed = 0
    for chat_id, message_id in sent:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception:
            failed += 1
            log.exception("Could not delete message in %s", chat_id)

    conn.execute(
        "DELETE FROM sent_messages WHERE broadcast_id = ?",
        (broadcast_id,),
    )
    conn.execute("DELETE FROM broadcasts WHERE id = ?", (broadcast_id,))
    conn.commit()
    conn.close()

    await update.effective_message.reply_text(
        "Undo finished. "
        + ("Some messages could not be deleted." if failed else
           "The last broadcast was deleted from all reachable groups.")
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    message = update.effective_message
    source_chat = update.effective_chat

    conn = db()
    targets = conn.execute(
        "SELECT chat_id FROM groups_list"
    ).fetchall()

    if not targets:
        conn.close()
        await message.reply_text(
            "No groups selected. Add me to your groups and use /addgroup there."
        )
        return

    cur = conn.execute(
        "INSERT INTO broadcasts (source_chat, source_message) VALUES (?, ?)",
        (source_chat.id, message.message_id),
    )
    broadcast_id = cur.lastrowid
    conn.commit()

    succeeded = 0
    failed = 0

    for (chat_id,) in targets:
        try:
            sent = await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source_chat.id,
                message_id=message.message_id,
            )
            conn.execute(
                "INSERT INTO sent_messages VALUES (?, ?, ?)",
                (broadcast_id, chat_id, sent.message_id),
            )
            conn.commit()
            succeeded += 1
        except Exception:
            failed += 1
            log.exception("Broadcast failed for group %s", chat_id)

    conn.close()
    await message.reply_text(
        f"Broadcast complete: {succeeded} group(s) reached; "
        f"{failed} failed."
    )


def main():
    if not TOKEN:
        raise RuntimeError("Set the BOT_TOKEN environment variable.")

    db().close()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addgroup", addgroup))
    app.add_handler(CommandHandler("removegroup", removegroup))
    app.add_handler(CommandHandler("groups", groups))
    app.add_handler(CommandHandler("undo", undo))

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            broadcast,
        )
    )

    log.info("Broadcast bot starting")
    app.run_polling()


if __name__ == "__main__":
    main()
