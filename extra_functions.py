import random
from telegram import Update
from telegram.ext import ContextTypes

def generate_random_cvv():
    return f"{random.randint(0, 999):03d}"

def adjust_card_length(card_number):
    if card_number.startswith(("34", "37")):
        return card_number[:15]
    elif card_number.startswith(("300", "301", "302", "303", "304", "305", "36", "38", "39")):
        return card_number[:14]
    elif card_number.startswith(("6011", "622126", "622925", "644", "645", "646", "647", "648", "649", "65")):
        return card_number[:16]
    elif card_number.startswith(("3528", "3529", "353", "354", "355", "356", "357", "358", "359")):
        return card_number[:16]
    elif card_number.startswith(("5062", "5063", "6506", "6507")):
        return card_number[:16]
    elif card_number.startswith(("5018", "5020", "5038", "5893", "6304", "6759", "6761", "6762", "6763")):
        return card_number[:16]
    elif card_number.startswith(("62")):
        return card_number[:16]
    elif card_number.startswith(("2200", "2201", "2202", "2203", "2204")):
        return card_number[:16]
    elif card_number.startswith(("9792")):
        return card_number[:16]
    elif card_number.startswith(("5067", "5090", "4011", "4312")):
        return card_number[:16]
    elif card_number.startswith(("636")):
        return card_number[:16]
    elif card_number.startswith(("1")):
        return card_number[:15]
    else:
        return card_number[:16]

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Este es un bot generador de tarjetas de crédito.\n"
        "Usa /gen <prefijo>|<mes>|<año> para generar tarjetas."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start - Inicia el bot\n"
        "/gen <prefijo>|<mes>|<año> - Genera tarjetas de crédito\n"
        "/info - Muestra información sobre el bot\n"
        "/help - Muestra esta ayuda"
    )