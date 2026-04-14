"""
Telegram bot for crossword photo ingestion.

Send a photo to the bot from your phone and it will be saved to
~/Desktop/crossword_photos/Times/ ready for the image processor pipeline.

Usage:
    python -m services.photo_ingestion.bot

Commands:
    /start  — welcome message
    /list   — show last 5 saved photos
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PHOTOS_DIR = Path.home() / "Desktop" / "crossword_photos" / "Times"


SEND_AS_FILE_TIP = (
    "📎 For best results, send photos using 'Send as File' (hold the attach "
    "button → File) so the full resolution is preserved. Regular photo sends "
    "are compressed to 1280px and may not process correctly."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Send me a crossword photo and I'll save it to the collection.\n\n"
        + SEND_AS_FILE_TIP
        + "\n\nUse /list to see recently saved photos."
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photos = sorted(PHOTOS_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    if not photos:
        await update.message.reply_text("No photos saved yet.")
        return
    lines = [f"• {p.name}" for p in photos]
    await update.message.reply_text("Last 5 saved photos:\n" + "\n".join(lines))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    # Telegram sends multiple sizes; pick the largest
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"telegram_{timestamp}.jpg"
    dest = PHOTOS_DIR / filename

    await file.download_to_drive(dest)
    logger.info("Saved photo: %s (%.1f KB)", dest, dest.stat().st_size / 1024)

    await update.message.reply_text(
        f"✅ Saved as {filename}\n\n"
        "⚠️ This was compressed to 1280px by Telegram and may not process well. "
        + SEND_AS_FILE_TIP
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos sent as documents (uncompressed / 'Send as file')."""
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Please send an image file.")
        return

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    file = await context.bot.get_file(doc.file_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = Path(doc.file_name).suffix if doc.file_name else ".jpg"
    filename = f"telegram_{timestamp}{suffix}"
    dest = PHOTOS_DIR / filename

    await file.download_to_drive(dest)
    logger.info("Saved document: %s (%.1f KB)", dest, dest.stat().st_size / 1024)

    await update.message.reply_text(f"✅ Saved as {filename}")


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    logger.info("Bot started — polling for updates …")
    app.run_polling()


if __name__ == "__main__":
    main()
