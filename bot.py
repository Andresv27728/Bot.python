#!/usr/bin/env python3
"""Bot de Telegram en un solo archivo.

La primera vez que se ejecuta pide el token por consola y lo guarda en
token.txt (junto a este archivo). En arranques posteriores lo lee solo.

Además se actualiza solo: descarga bot.py desde GitHub, y si hay una
versión nueva la escribe y se reinicia.

Uso:
    pip install "python-telegram-bot>=21"
    python bot.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from telegram import Bot, BotCommand, Update
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

SELF_PATH = Path(__file__).resolve()
TOKEN_FILE = SELF_PATH.with_name("token.txt")
TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

# Auto-actualización desde el repositorio original
UPDATE_URL = "https://raw.githubusercontent.com/Andresv27728/Bot.python/main/bot.py"
UPDATE_INTERVAL_SECONDS = 10 * 60

# (comando, argumentos para la ayuda, descripción para el menú de Telegram)
COMMANDS: list[tuple[str, str, str]] = [
    ("start", "", "mensaje de bienvenida"),
    ("help", "", "esta ayuda"),
    ("ping", "", "comprueba que sigo vivo"),
    ("echo", " <texto>", "repito tu texto"),
    ("info", "", "tus datos de Telegram"),
]


# --------------------------------------------------------------------------
# Token: leer, pedir y guardar
# --------------------------------------------------------------------------
def read_saved_token() -> str | None:
    """Devuelve el token guardado, o None si no existe o está vacío."""
    try:
        raw = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"No se pudo leer {TOKEN_FILE.name}: {exc}")
        return None
    return raw or None


def ask_for_token() -> str:
    """Pide el token por consola hasta recibir uno con formato válido."""
    print(f"No hay un token guardado en {TOKEN_FILE.name}.")
    print("Abre Telegram, habla con @BotFather, crea tu bot con /newbot y copia el token.")
    while True:
        try:
            token = input("Pega el token del bot: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelado.")
            raise SystemExit(1)
        if TOKEN_PATTERN.match(token):
            return token
        print("Ese token no tiene el formato esperado (ej. 123456789:AAF...). Prueba otra vez.")


def save_token(token: str) -> None:
    """Guarda el token en token.txt con permisos restringidos."""
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    print(f"Token guardado en {TOKEN_FILE}.")


def load_or_ask_token() -> str:
    """Lee el token guardado; si no sirve, lo pide y lo guarda."""
    token = read_saved_token()
    if token and TOKEN_PATTERN.match(token):
        return token
    token = ask_for_token()
    save_token(token)
    return token


def forget_token() -> None:
    """Borra el token guardado para volver a pedirlo."""
    try:
        TOKEN_FILE.unlink()
    except FileNotFoundError:
        pass
    print(f"Se eliminó {TOKEN_FILE.name}.")


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user and user.first_name else "amigo"
    await update.message.reply_text(
        f"¡Hola, {name}! 👋\n"
        "Soy tu bot de Telegram. Escribe /help para ver todo lo que puedo hacer."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["Comandos disponibles:"]
    for command, args, description in COMMANDS:
        lines.append(f"/{command}{args} — {description}")
    await update.message.reply_text("\n".join(lines))


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏓 pong")


async def cmd_echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Uso: /echo <texto>")
        return
    await update.message.reply_text(text)


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    username = f"@{user.username}" if user and user.username else "sin username"
    await update.message.reply_text(
        f"user_id: {user.id if user else 'desconocido'}\n"
        f"username: {username}\n"
        f"chat_id: {chat.id if chat else 'desconocido'}\n"
        f"tipo de chat: {chat.type if chat else 'desconocido'}"
    )


def check_token(token: str) -> bool:
    """Pregunta a Telegram si el token es válido (sin ruido de logs)."""

    async def _check() -> None:
        async with Bot(token) as bot:
            await bot.get_me()

    try:
        asyncio.run(_check())
    except InvalidToken:
        return False
    except NetworkError as exc:
        print(f"No se pudo contactar con Telegram: {exc}")
        raise SystemExit(1)
    return True


# --------------------------------------------------------------------------
# Auto-actualización desde GitHub
# --------------------------------------------------------------------------
def fetch_latest_source() -> str | None:
    """Descarga bot.py desde GitHub. Devuelve None si no se pudo."""
    url = f"{UPDATE_URL}?t={int(time.time())}"
    request = Request(
        url,
        headers={"User-Agent": "Bot.python-auto-update", "Cache-Control": "no-cache"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        print(f"No se pudo comprobar si hay actualizaciones: {exc}")
        return None


def check_and_update(verbose: bool = True) -> bool:
    """Escribe la versión de GitHub si es distinta. True si hay que reiniciar."""
    source = fetch_latest_source()
    if source is None:
        return False

    try:
        compile(source, str(SELF_PATH), "exec")
    except SyntaxError as exc:
        print(f"La versión de GitHub no compila, se ignora: {exc}")
        return False
    if "def main(" not in source:
        print("Lo descargado no parece este bot, se ignora.")
        return False

    current = SELF_PATH.read_text(encoding="utf-8")
    if source == current:
        if verbose:
            print("No hay actualizaciones.")
        return False

    temporary = SELF_PATH.with_name(SELF_PATH.name + ".new")
    temporary.write_text(source, encoding="utf-8")
    os.replace(temporary, SELF_PATH)
    print("Actualización descargada desde GitHub.")
    return True


def restart_with_new_version() -> None:
    """Reemplaza este proceso por el bot.py recién descargado."""
    print("Reiniciando con la versión nueva…")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, str(SELF_PATH), *sys.argv[1:]])


async def auto_update_loop() -> None:
    """Comprueba GitHub cada UPDATE_INTERVAL_SECONDS."""
    while True:
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
        if check_and_update(verbose=False):
            restart_with_new_version()


async def on_startup(application: Application) -> None:
    """Publica los comandos en Telegram y arranca el auto-update."""
    await register_commands(application)
    application.create_task(auto_update_loop())


async def register_commands(application: Application) -> None:
    """Publica el menú de comandos en Telegram (se ejecuta al arrancar)."""
    try:
        await application.bot.set_my_commands(
            [BotCommand(command, description) for command, _, description in COMMANDS]
        )
        print("Comandos registrados en el menú de Telegram.")
    except TelegramError as exc:
        print(f"No se pudieron registrar los comandos en el menú: {exc}")


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("echo", cmd_echo))
    application.add_handler(CommandHandler("info", cmd_info))


# --------------------------------------------------------------------------
# Arranque
# --------------------------------------------------------------------------
def main() -> None:
    while True:
        token = load_or_ask_token()

        if check_token(token):
            break

        print("Telegram rechazó ese token. Vamos a pedirlo otra vez.")
        forget_token()

    if check_and_update():
        restart_with_new_version()

    application = Application.builder().token(token).post_init(on_startup).build()
    register_handlers(application)

    print("Bot arrancado. Pulsa Ctrl+C para detenerlo.")
    application.run_polling()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot detenido.")
