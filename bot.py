#!/usr/bin/env python3
"""Bot de Telegram en un solo archivo.

La primera vez que se ejecuta pide el token por consola y lo guarda en
token.txt (junto a este archivo). En arranques posteriores lo lee solo.

Además se actualiza solo: descarga bot.py desde GitHub, y si hay una
versión nueva la escribe y se reinicia.

Casi todos los comandos usan APIs públicas gratuitas que no piden key
(Open-Meteo, CoinGecko, exchangerate-api, ip-api, Google DNS, RDAP,
Wikipedia, MyMemory, USGS, PokeAPI, Dog CEO, Cataas, JokeAPI, GitHub,
TinyURL, api.qrserver.com) o hacen scraping ligero de RSS y YouTube.

Unos pocos aprovechan claves gratuitas de APIs: el diccionario KEYS de
abajo ya trae las claves públicas de demostración (NASA DEMO_KEY, Twelve
Data "demo", TheMealDB/TheCocktailDB "1"), así que funcionan sin que
hagas nada. Para /gif y /pelicula solo hay que pegar una clave gratis
(ver /claves); el bot sigue funcionando igual sin ellas.

Uso:
    pip install "python-telegram-bot>=21"
    python bot.py
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import html
import io
import json
import operator
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote_plus, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import telegram
from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InputFile,
    InputTextMessageContent,
    Update,
)
from telegram.error import InvalidToken, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    InlineQueryHandler,
    CommandHandler,
    ContextTypes,
)

SELF_PATH = Path(__file__).resolve()
TOKEN_FILE = SELF_PATH.with_name("token.txt")
TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

VERSION = "3.29"
START_TIME = time.time()
LAST_UPDATE_CHECK = START_TIME

# --------------------------------------------------------------------------
# Claves de APIs. Todas son gratis y opcionales: las que vienen puestas son
# claves públicas de demostración y el bot funciona sin tocar nada. Si quieres
# más cuota o más símbolos, crea tu clave gratis y pégala aquí entre comillas.
# --------------------------------------------------------------------------
KEYS: dict[str, str] = {
    "nasa": "DEMO_KEY",  # api.nasa.gov (DEMO_KEY: 30 peticiones/hora)
    "twelvedata": "demo",  # twelvedata.com ("demo": AAPL, BTC/USD, EUR/USD)
    "giphy": "",  # developers.giphy.com (necesaria para /gif)
    "omdb": "",  # omdbapi.com (necesaria para /pelicula)
    "sportsdb": "123",  # thesportsdb.com (clave de prueba pública y gratuita)
    "yosoyyo": "yosoyyo_free",  # api-yosoyyo-api-ofc.onrender.com (clave gratis)
}

# (clave, nombre, dónde conseguirla, nota)
KEY_INFO: list[tuple[str, str, str, str]] = [
    ("nasa", "NASA", "https://api.nasa.gov", "DEMO_KEY ya funciona; la clave gratis sube el límite"),
    (
        "twelvedata",
        "Twelve Data (acciones y divisas)",
        "https://twelvedata.com/pricing",
        "con \"demo\" solo AAPL, BTC/USD y EUR/USD",
    ),
    ("giphy", "Giphy (GIFs)", "https://developers.giphy.com/dashboard/", "gratis; necesaria para /gif"),
    ("omdb", "OMDb (películas)", "https://www.omdbapi.com/apikey.aspx", "gratis por correo; necesaria para /pelicula"),
    (
        "sportsdb",
        "TheSportsDB (deportes)",
        "https://www.thesportsdb.com/pricing",
        "la clave de prueba pública «123» ya viene puesta; sirve para /deporte",
    ),
    (
        "yosoyyo",
        "YOSOYYO API (YouTube, redes, imágenes e IA)",
        "https://api-yosoyyo-api-ofc.onrender.com",
        "la clave gratis «yosoyyo_free» ya viene puesta; 100 peticiones al día",
    ),
]

# Auto-actualización desde el repositorio original
UPDATE_URL = "https://raw.githubusercontent.com/Andresv27728/Bot.python/main/bot.py"
UPDATE_INTERVAL_SECONDS = 10 * 60

# HTTP
HTTP_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# (comando, argumentos para la ayuda, descripción para el menú de Telegram)
COMMANDS: list[tuple[str, str, str]] = [
    ("start", "", "mensaje de bienvenida"),
    ("menu", " [categoría]", "menú interactivo por categorías"),
    ("help", " [todo]", "menú por categorías o lista completa"),
    ("ping", "", "comprueba que sigo vivo"),
    ("info", "", "tus datos de Telegram"),
    ("buscar", " <texto>", "encuentra un comando por nombre o tema"),
    ("salud", "", "estado de las APIs y de la caché"),
    ("recordar", " [tiempo] <texto>", "te aviso en X tiempo (10m, 2h, 09:30)"),
    ("convertir", " <formato>", "convierte el archivo al que respondes"),
    ("resumen", " <url>", "resume el contenido de un enlace"),
    ("estado", "", "estado y versión del bot"),
    ("version", "", "compara tu versión con la de GitHub"),
    ("calc", " <operación>", "calculadora (ej. 2+2*3)"),
    ("clima", " <ciudad>", "clima y pronóstico de 3 días"),
    ("noticias", "", "titulares de BBC Mundo y El País"),
    ("wiki", " <tema>", "resumen de Wikipedia"),
    ("traducir", " <idioma> <texto>", "traduce desde español"),
    ("cripto", " <moneda>", "precio de una criptomoneda"),
    ("divisa", " <cantidad> <de> <a>", "convierte divisas (ej. 100 USD EUR)"),
    ("sismos", "", "últimos sismos de magnitud 2.5+"),
    ("pokemon", " <nombre>", "datos de un Pokémon"),
    ("perro", "", "foto de un perro"),
    ("gato", "", "foto de un gato"),
    ("chiste", "", "un chiste malo"),
    ("github", " <usuario>", "perfil de GitHub"),
    ("ip", " <ip o dominio>", "geolocaliza una IP"),
    ("dns", " <dominio> [tipo]", "consulta DNS (A, MX, TXT…)"),
    ("whois", " <dominio>", "registro y caducidad de un dominio"),
    ("yt", " <búsqueda>", "busca vídeos en YouTube"),
    ("qr", " <texto>", "genera un código QR"),
    ("acortar", " <url>", "acorta una URL"),
    ("dado", " [caras]", "lanza un dado"),
    ("moneda", "", "lanza una moneda al aire"),
    ("nasa", " [tema]", "foto del día o imágenes de la NASA"),
    ("receta", " [plato]", "receta con ingredientes e instrucciones"),
    ("trago", " [nombre]", "cóctel con ingredientes y preparación"),
    ("serie", " <título>", "busca una serie de televisión"),
    ("anime", " <título>", "busca un anime"),
    ("libro", " <título>", "busca un libro en Open Library"),
    ("aire", " <ciudad>", "calidad del aire de una ciudad"),
    ("pais", " <nombre>", "datos de un país"),
    ("stack", " <búsqueda>", "preguntas de Stack Overflow"),
    ("radio", " <nombre>", "emisoras de radio en línea"),
    ("mc", " <servidor>", "estado de un servidor de Minecraft"),
    ("carta", " [cantidad]", "saca cartas de la baraja"),
    ("trivia", "", "pregunta de trivia con opciones"),
    ("wayback", " <url>", "copia antigua de una página en Internet Archive"),
    ("publicar", " <texto>", "publica un texto largo en telegra.ph"),
    ("sol", " <ciudad>", "amanecer y atardecer de una ciudad"),
    ("accion", " <símbolo>", "precio de una acción o divisa (Twelve Data)"),
    ("gif", " <búsqueda>", "busca un GIF (necesita clave de Giphy)"),
    ("pelicula", " <título>", "busca una película (necesita clave de OMDb)"),
    ("claves", "", "estado de las claves de APIs"),
    ("iss", "", "dónde está la Estación Espacial Internacional"),
    ("arte", " <búsqueda>", "obras de arte del museo de Chicago"),
    ("poema", " [autor]", "un poema al azar (o de un autor)"),
    ("consejo", "", "un consejo al azar, en inglés y español"),
    ("nombre", " <nombre>", "edad, género y país probables de un nombre"),
    ("feriados", " <código país> [año]", "feriados oficiales (ej. /feriados CO)"),
    ("cerveza", " <nombre>", "cervecerías de Open Brewery DB"),
    ("producto", " <código de barras>", "info nutricional de un producto"),
    ("musica", " <artista>", "ficha de un artista en MusicBrainz"),
    ("letra", " <artista> - <canción>", "letra de una canción"),
    ("color", " <hex>", "datos de un color (ej. /color 24b1e0)"),
    ("aburrido", "", "una actividad al azar para no aburrirte"),
    ("zorro", "", "foto de un zorro"),
    ("dato", "", "dato curioso al azar"),
    ("tcg", " <carta>", "carta Pokémon de TCGdex"),
    ("f1", "", "resultados de la última carrera de F1"),
    ("mar", " <ciudad>", "oleaje y periodo de olas"),
    ("http", " <código>", "gato con el significado de un código HTTP"),
    ("universidad", " <país>", "universidades de un país"),
    ("deporte", " <equipo>", "ficha de un equipo (TheSportsDB)"),
    ("covid", " [país]", "datos de COVID-19 de un país o del mundo"),
    ("app", " <búsqueda>", "busca apps en la App Store"),
    ("mp3", " <enlace|búsqueda>", "audio con portada y todos sus datos"),
    ("mp4", " [calidad] <enlace|búsqueda>", "vídeo con su ficha completa (360p, 720p…)"),
    ("spotify", " <búsqueda>", "busca canciones en Spotify"),
    ("spotifydl", " <enlace>", "descarga una canción de Spotify"),
    ("ytmusic", " <búsqueda>", "busca música en YouTube Music"),
    ("pinterest", " <búsqueda>", "imágenes de Pinterest"),
    ("instagram", " <enlace>", "descarga foto o vídeo de Instagram"),
    ("facebook", " <enlace>", "descarga un vídeo de Facebook"),
    ("tiktok", " <enlace|búsqueda>", "descarga o busca vídeos de TikTok"),
    ("fbstalk", " <enlace>", "info de un vídeo o perfil de Facebook"),
    ("mediafire", " <enlace>", "descarga un archivo de MediaFire"),
    ("tourl", " <url>", "sube un archivo y devuelve enlaces"),
    ("captura", " <url>", "captura de pantalla de una web"),
    ("ia", " <pregunta>", "pregúntale a la IA DeepSeek"),
    ("deepai", " <pregunta>", "chatea con la IA de DeepAI"),
    ("tts", " <texto>", "convierte un texto en voz"),
    ("brat", " <texto>", "imagen con el estilo brat"),
    ("bratvid", " <texto>", "vídeo con el estilo brat"),
    ("banner", " <texto>", "banner de bienvenida"),
    ("qwindows", " <texto>", "meme de Windows Media Player con tu texto"),
    ("cumple", " <nombre> <edad> <fecha>", "tarjeta de cumpleaños"),
    ("nivel", " <nombre> <nivel> <xp> <máx>", "tarjeta de nivel"),
    ("apistatus", "", "consumo de la API de YOSOYYO"),
    ("echo", " <texto>", "repito tu texto"),
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
# Utilidades HTTP
# --------------------------------------------------------------------------
class CommandError(Exception):
    """Error cuyo mensaje se le muestra tal cual al usuario."""


def _build_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    clean = {key: value for key, value in params.items() if value is not None}
    if not clean:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(clean)}"


def _http_get(url: str, accept: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "es,en;q=0.8",
        },
    )
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return response.read()


async def http_json(url: str, params: dict | None = None) -> object:
    raw = await asyncio.to_thread(_http_get, _build_url(url, params), "application/json")
    return json.loads(raw.decode("utf-8", "replace"))


async def http_text(url: str, params: dict | None = None) -> str:
    raw = await asyncio.to_thread(
        _http_get, _build_url(url, params), "application/json, text/html;q=0.9, */*;q=0.8"
    )
    return raw.decode("utf-8", "replace")


def _http_post(url: str, payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return response.read()


async def http_post_json(url: str, payload: dict) -> object:
    raw = await asyncio.to_thread(_http_post, url, payload)
    return json.loads(raw.decode("utf-8", "replace"))


def fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return decomposed.encode("ascii", "ignore").decode("ascii").lower().strip()


def strip_html(text: str | None) -> str:
    """Quita etiquetas HTML y espacios repetidos (sinpsosis de APIs)."""
    clean = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(clean)).strip()


def truncate(text: str, limit: int = 3900) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def money(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:,.6f}"


# --------------------------------------------------------------------------
# Estado persistente (recordatorios, preferencias y uso de comandos)
# --------------------------------------------------------------------------
DATA_FILE = SELF_PATH.with_name("bot_data.json")

# Tu ID de Telegram para recibir avisos cuando una API falle (míralo en /info).
OWNER_ID: int | None = None

_DATA: dict = {"reminders": [], "usage": {}, "prefs": {}}
_DATA_DIRTY = False


def load_data() -> None:
    try:
        parsed = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return
    if not isinstance(parsed, dict):
        return
    for key in ("reminders", "usage", "prefs"):
        if isinstance(parsed.get(key), type(_DATA[key])):
            _DATA[key] = parsed[key]


def save_data(force: bool = False) -> None:
    global _DATA_DIRTY
    if not _DATA_DIRTY and not force:
        return
    try:
        DATA_FILE.write_text(
            json.dumps(_DATA, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        _DATA_DIRTY = False
    except OSError as exc:
        print(f"No pude guardar {DATA_FILE.name}: {exc}")


def bump_usage(command: str) -> None:
    global _DATA_DIRTY
    _DATA["usage"][command] = int(_DATA["usage"].get(command, 0)) + 1
    _DATA_DIRTY = True


# --------------------------------------------------------------------------
# Caché con caducidad (ahorra latencia y cupo de las APIs)
# --------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_STATS = {"hits": 0, "misses": 0}
_CACHE_MAX = 400


async def cached(key: str, ttl: int, factory) -> object:
    """Devuelve el valor cacheado o lo calcula con factory() y lo guarda."""
    entry = _CACHE.get(key)
    now = time.time()
    if entry and entry[0] > now:
        _CACHE_STATS["hits"] += 1
        return entry[1]
    _CACHE_STATS["misses"] += 1
    value = await factory()
    if value:
        if len(_CACHE) > _CACHE_MAX:
            for stale in [k for k, (exp, _) in _CACHE.items() if exp <= now][:100]:
                _CACHE.pop(stale, None)
        _CACHE[key] = (now + ttl, value)
    return value


# --------------------------------------------------------------------------
# Salud de los proveedores (circuit breaker)
# --------------------------------------------------------------------------
_PROVIDERS: dict[str, dict] = {}
_ALERTS: list[str] = []
PROVIDER_FAILS_TO_OPEN = 3
PROVIDER_OPEN_SECONDS = 600


def _provider(name: str, label: str) -> dict:
    return _PROVIDERS.setdefault(
        name,
        {
            "label": label,
            "fails": 0,
            "open_until": 0.0,
            "last_ok": 0.0,
            "last_error": "",
            "alerted": False,
        },
    )


def check_provider(name: str) -> None:
    """Si el proveedor está en cuarentena, no lo intentamos siquiera."""
    state = _PROVIDERS.get(name)
    if not state:
        return
    wait = state["open_until"] - time.time()
    if wait > 0:
        raise CommandError(
            f"⏳ {state['label']} está fallando ahora mismo. "
            f"Lo reintento en {int(wait) + 1} s (detalles en /salud)."
        )


def provider_ok(name: str, label: str) -> None:
    state = _provider(name, label)
    state["fails"] = 0
    state["open_until"] = 0.0
    state["last_ok"] = time.time()
    state["alerted"] = False


def provider_fail(name: str, label: str, error: object) -> None:
    state = _provider(name, label)
    state["fails"] += 1
    state["last_error"] = str(error)[:140]
    if state["fails"] >= PROVIDER_FAILS_TO_OPEN:
        state["open_until"] = time.time() + PROVIDER_OPEN_SECONDS
        if not state["alerted"]:
            state["alerted"] = True
            _ALERTS.append(
                f"⚠️ {label} lleva {state['fails']} fallos seguidos y queda "
                f"{PROVIDER_OPEN_SECONDS // 60} min en cuarentena.\nÚltimo: {state['last_error']}"
            )


# --------------------------------------------------------------------------
# Fechas de los recordatorios
# --------------------------------------------------------------------------
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def pretty_duration(seconds: float) -> str:
    total = int(max(0, seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not days:
        parts.append(f"{secs}s")
    return " ".join(parts) or "0s"


def parse_when(text: str) -> tuple[float | None, str]:
    """«10m comprar pan» o «09:30 despertar» → (segundos, texto)."""
    match = re.match(r"^\s*((?:\d+[smhd]\s*)+|\d{1,2}:\d{2})\s+(.+)$", text, re.I)
    if not match:
        return None, text.strip()
    stamp, rest = match.group(1), match.group(2).strip()
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})", stamp)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23 or minute > 59:
            return None, text.strip()
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds(), rest
    total = 0
    for amount, unit in re.findall(r"(\d+)([smhd])", stamp.lower()):
        total += int(amount) * _DURATION_UNITS[unit]
    if total <= 0:
        return None, text.strip()
    return float(total), rest


def require_args(context: ContextTypes.DEFAULT_TYPE, usage: str) -> str:
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        raise CommandError(f"Uso: {usage}")
    return text


# --------------------------------------------------------------------------
# Menú interactivo por categorías
# (clave, emoji, nombre corto del botón, título, comandos)
# --------------------------------------------------------------------------
CATEGORIES: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    (
        "descargas",
        "🎬",
        "Descargas y redes",
        "Descargas y redes sociales",
                ("mp3", "mp4", "ytmusic", "yt", "spotify", "spotifydl", "pinterest",
            "instagram", "facebook", "tiktok", "fbstalk", "mediafire", "tourl", "captura",
        ),
    ),
    (
        "mundo",
        "🌍",
        "Mundo y clima",
        "Mundo, clima y datos",
        (
            "clima", "noticias", "wiki", "traducir", "pais", "aire", "sol", "mar",
            "sismos", "feriados", "covid", "universidad", "deporte", "f1", "iss",
            "nombre",
        ),
    ),
    ("dinero", "💰", "Dinero", "Dinero y mercados", ("cripto", "divisa", "accion")),
    (
        "tech",
        "🖥",
        "Tecnología",
        "Tecnología y web",
        (
            "ip", "dns", "whois", "github", "stack", "mc", "http", "app",
            "producto", "qr", "acortar", "wayback", "color",
        ),
    ),
    ("musica", "🎵", "Música", "Música y audio", ("musica", "letra", "radio", "tts")),
    (
        "imagenes",
        "🎨",
        "Imágenes",
        "Imágenes y generadores",
        (
            "brat", "bratvid", "banner", "qwindows", "nivel", "cumple", "nasa",
            "arte", "gif", "perro", "gato", "zorro", "convertir",
        ),
    ),
    (
        "ocio",
        "📚",
        "Cultura y ocio",
        "Cultura y ocio",
        (
            "receta", "trago", "serie", "anime", "libro", "pelicula", "cerveza",
            "poema", "chiste", "dato", "consejo", "aburrido",
        ),
    ),
    (
        "juegos",
        "🎲",
        "Juegos y azar",
        "Juegos y azar",
        ("dado", "moneda", "carta", "trivia", "pokemon", "tcg"),
    ),
    (
        "ia",
        "🤖",
        "IA y herramientas",
        "IA y herramientas",
        ("ia", "deepai", "calc", "claves", "apistatus", "publicar", "resumen"),
    ),
    (
        "bot",
        "⚙️",
        "El bot",
        "El bot",
        ("menu", "start", "help", "ping", "info", "estado", "version", "echo",
            "buscar", "salud", "recordar"),
    ),
]


def _needs_arguments(spec: str) -> bool:
    """True si el comando tiene algún argumento obligatorio (<...>)."""
    return any(part.startswith("<") for part in spec.split())


# Comandos que funcionan sin argumentos: se pueden lanzar desde un botón.
RUNNABLE = {
    command
    for command, args, _ in COMMANDS
    if not _needs_arguments(args) and command not in ("start", "help", "menu", "echo")
}
# /recordar sin argumentos lista los pendientes, así que también sirve de botón.
RUNNABLE.add("recordar")


def _command_line(name: str) -> str:
    """Línea de ayuda de un comando, con sus argumentos."""
    for command, args, description in COMMANDS:
        if command == name:
            return f"/{command}{args} — {description}"
    return f"/{name}"


def build_menu_keyboard() -> InlineKeyboardMarkup:
    """Botones de categorías, de dos en dos, más el comando al azar."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, emoji, short, _, commands in CATEGORIES:
        row.append(
            InlineKeyboardButton(f"{emoji} {short} ({len(commands)})", callback_data=f"m:c:{key}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🎲 Comando al azar", callback_data="m:r")])
    return InlineKeyboardMarkup(rows)


def category_keyboard(key: str) -> InlineKeyboardMarkup:
    """Volver, al azar y un botón ▶️ por cada comando que no pide datos."""
    commands = next((cmds for cat_key, _, _, _, cmds in CATEGORIES if cat_key == key), ())
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("⬅️ Menú", callback_data="m:h"),
            InlineKeyboardButton("🎲 Al azar", callback_data="m:r"),
        ]
    ]
    row: list[InlineKeyboardButton] = []
    for name in commands:
        if name not in RUNNABLE:
            continue
        row.append(InlineKeyboardButton(f"▶️ /{name}", callback_data=f"m:x:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def menu_text() -> str:
    total = sum(len(commands) for *_, commands in CATEGORIES)
    lines = [
        "🧭 MENÚ DEL BOT",
        f"v{VERSION} · {total} comandos en {len(CATEGORIES)} categorías",
        "",
        "Toca una categoría para ver sus comandos con ejemplos.",
        "La 🎲 te sugiere uno al azar.",
        "",
    ]
    for _, emoji, _, title, commands in CATEGORIES:
        lines.append(f"{emoji} {title} · {len(commands)}")
    lines += ["", "/menu <categoría> abre una directamente.", "/help todo muestra la lista seguida."]
    return truncate("\n".join(lines))


def category_text(key: str) -> str:
    for cat_key, emoji, _, title, commands in CATEGORIES:
        if cat_key != key:
            continue
        lines = [f"{emoji} {title.upper()} · {len(commands)} comandos", ""]
        lines.extend(_command_line(name) for name in commands)
        if any(name in RUNNABLE for name in commands):
            lines += ["", "▶️ Toca un botón para ejecutarlo aquí mismo."]
        return truncate("\n".join(lines))
    return "No encontré esa categoría. Abre /menu para ver las que hay."


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip().lower() if context.args else ""
    if args:
        for key, _, _, title, _ in CATEGORIES:
            if args in (key, title.lower()) or title.lower().startswith(args):
                await update.effective_message.reply_text(
                    category_text(key), reply_markup=category_keyboard(key)
                )
                return
        raise CommandError(f"No conozco la categoría «{args}». Abre /menu para verlas.")
    await update.effective_message.reply_text(menu_text(), reply_markup=build_menu_keyboard())


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navegación del menú: categorías, volver y comando al azar."""
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""
    if data == "m:r":
        options = [command for _, _, _, _, commands in CATEGORIES for command in commands]
        options = [command for command in options if command not in ("start", "help", "menu")]
        await query.answer(f"🎲 {_command_line(random.choice(options))}", show_alert=True)
        return
    if data.startswith("m:x:"):
        command = data[4:]
        handler = HANDLERS.get(command)
        if command not in RUNNABLE or handler is None:
            await query.answer(
                f"/{command} necesita datos: escríbelo con sus argumentos.", show_alert=True
            )
            return
        await query.answer(f"▶️ Ejecutando /{command}…")
        message = update.effective_message
        context.args = []
        try:
            await handler(update, context)
        except CommandError as exc:
            await message.reply_text(f"⚠️ {exc}")
        except Exception as exc:  # cualquier fallo raro se avisa en el chat
            await message.reply_text(f"⚠️ /{command} falló: {exc}")
        return
    await query.answer()
    if data.startswith("m:c:"):
        key = data[4:]
        text, markup = category_text(key), category_keyboard(key)
    else:
        text, markup = menu_text(), build_menu_keyboard()
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except TelegramError:
        pass


# --------------------------------------------------------------------------
# Comandos básicos
# --------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user and user.first_name else "amigo"
    await update.effective_message.reply_text(
        f"¡Hola, {name}! 👋\n\n{menu_text()}",
        reply_markup=build_menu_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = " ".join(context.args).strip().lower() if context.args else ""
    if args not in ("todo", "lista", "all"):
        await update.effective_message.reply_text(
            menu_text(), reply_markup=build_menu_keyboard()
        )
        return
    lines = ["Comandos disponibles:"]
    for command, args, description in COMMANDS:
        lines.append(f"/{command}{args} — {description}")
    # Telegram limita cada mensaje a 4096 caracteres: lo parto por líneas.
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3900:
            await update.effective_message.reply_text(chunk)
            chunk = ""
        chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        await update.effective_message.reply_text(chunk)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    started = time.perf_counter()
    sent = await update.effective_message.reply_text("🏓 …")
    latency = (time.perf_counter() - started) * 1000
    lines = [
        "🏓 pong",
        f"⚡ Latencia: {latency:.0f} ms",
        f"⏱ Activo desde hace {uptime_text()}",
    ]
    try:
        await sent.edit_text("\n".join(lines))
    except TelegramError:
        await update.effective_message.reply_text("\n".join(lines))


async def cmd_echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.effective_message.reply_text("Uso: /echo <texto>")
        return
    await update.effective_message.reply_text(text)


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    username = f"@{user.username}" if user and user.username else "sin username"
    await update.effective_message.reply_text(
        f"user_id: {user.id if user else 'desconocido'}\n"
        f"username: {username}\n"
        f"chat_id: {chat.id if chat else 'desconocido'}\n"
        f"tipo de chat: {chat.type if chat else 'desconocido'}"
    )


def uptime_text() -> str:
    """Tiempo que lleva el bot encendido, en formato legible."""
    seconds = int(time.time() - START_TIME)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h {minutes}m {secs}s"
    return f"{hours}h {minutes}m {secs}s"


def next_check_in() -> int:
    """Segundos que faltan para el próximo chequeo automático de GitHub."""
    remaining = LAST_UPDATE_CHECK + UPDATE_INTERVAL_SECONDS - time.time()
    return max(0, int(remaining))


def human_seconds(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def _version_tuple(value: str) -> tuple[int, ...]:
    """"3.10" se convierte en (3, 10) para poder comparar versiones."""
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers) if numbers else (0,)


def _remote_version_info() -> tuple[str | None, bool]:
    """Versión publicada en GitHub y si el archivo es idéntico al de aquí."""
    source = fetch_latest_source()
    if source is None:
        return None, False
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    remote = match.group(1) if match else None
    try:
        local = SELF_PATH.read_text(encoding="utf-8")
    except OSError:
        return remote, False
    return remote, source == local


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    remote, identical = await asyncio.to_thread(_remote_version_info)
    if not remote:
        raise CommandError(
            "No pude leer la versión de GitHub.\n"
            f"Tu versión local es v{VERSION}.\n{UPDATE_URL}"
        )
    local = _version_tuple(VERSION)
    latest = _version_tuple(remote)
    newer = latest > local
    if newer:
        headline = f"⬆️ Hay una versión nueva: v{remote}. Tú tienes v{VERSION}."
        advice = "Toca ⬆️ Actualizar ahora para instalarla en este momento."
    elif latest < local:
        headline = f"🧪 Vas por delante: v{VERSION} > v{remote} de GitHub."
        advice = "GitHub todavía no tiene ese cambio subido."
    else:
        headline = f"✅ Estás actualizado: v{VERSION} es la última."
        advice = f"Auto-update activo cada {UPDATE_INTERVAL_SECONDS // 60} min."
    lines = [
        "🔎 VERSIÓN DEL BOT",
        "",
        headline,
        f"Local: v{VERSION} · GitHub: v{remote}",
        f"Archivo igual al de GitHub: {'sí' if identical else 'no'}",
        f"Comandos: {len(COMMANDS)}",
        "",
        advice,
        f"⏳ Próximo auto-update en {human_seconds(next_check_in())}",
        f"Origen: {UPDATE_URL}",
    ]
    markup = None
    if newer:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬆️ Actualizar ahora", callback_data="upd:now")]]
        )
    await update.effective_message.reply_text("\n".join(lines), reply_markup=markup)


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"🤖 Bot v{VERSION}\n"
        f"⏱ Encendido desde hace {uptime_text()}\n"
        f"🧩 Comandos: {len(COMMANDS)}\n"
        f"🐍 Python {sys.version.split()[0]}\n"
        f"📦 python-telegram-bot {telegram.__version__}\n"
        f"🔄 Auto-update cada {UPDATE_INTERVAL_SECONDS // 60} min\n"
        f"🗄 Caché: {len(_CACHE)} entradas\n\n"
        f"{usage_report()}"
    )


# --------------------------------------------------------------------------
# Calculadora (sin eval)
# --------------------------------------------------------------------------
_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        if isinstance(node.value, (int, float)):
            return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise CommandError("Esa potencia es demasiado grande.")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise CommandError("Solo permito números y operaciones: + - * / // % ** y paréntesis.")


async def cmd_calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    expression = require_args(context, "/calc <operación>   ej. /calc (2+3)*4")
    if len(expression) > 200:
        raise CommandError("La operación es demasiado larga.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise CommandError("No entendí la operación. Usa números y + - * / // % ** y paréntesis.")
    try:
        result = _eval_node(tree.body)
    except ZeroDivisionError:
        raise CommandError("No se puede dividir entre cero.")
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    formatted = f"{result:,.6g}" if isinstance(result, float) else f"{result:,}"
    await update.effective_message.reply_text(f"{expression} = {formatted}")


# --------------------------------------------------------------------------
# Clima (Open-Meteo, sin key)
# --------------------------------------------------------------------------
WMO_CODES = {
    0: "despejado ☀️",
    1: "mayormente despejado 🌤",
    2: "parcialmente nublado ⛅",
    3: "nublado ☁️",
    45: "niebla 🌫",
    48: "niebla con escarcha 🌫",
    51: "llovizna ligera 🌦",
    53: "llovizna 🌦",
    55: "llovizna fuerte 🌧",
    56: "llovizna helada 🌧",
    57: "llovizna helada fuerte 🌧",
    61: "lluvia ligera 🌦",
    63: "lluvia 🌧",
    65: "lluvia fuerte 🌧",
    66: "lluvia helada 🌧",
    67: "lluvia helada fuerte 🌧",
    71: "nieve ligera 🌨",
    73: "nieve 🌨",
    75: "nieve fuerte ❄️",
    77: "granos de nieve ❄️",
    80: "chubascos ligeros 🌦",
    81: "chubascos 🌧",
    82: "chubascos fuertes ⛈",
    85: "chubascos de nieve 🌨",
    86: "chubascos de nieve fuertes ❄️",
    95: "tormenta ⛈",
    96: "tormenta con granizo ⛈",
    99: "tormenta fuerte con granizo ⛈",
}


def weather_label(code: int | float | None) -> str:
    return WMO_CODES.get(int(code or 0), f"código {code}")


async def geocode(city: str) -> dict:
    """Busca una ciudad en Open-Meteo y devuelve el primer resultado."""
    geo = await http_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "es", "format": "json"},
    )
    results = (geo or {}).get("results") or []
    if not results:
        raise CommandError(f"No encontré la ciudad «{city}». Prueba con otro nombre.")
    return results[0]


async def weather_report(city: str) -> str:
    place = await geocode(city)
    latitude = place["latitude"]
    longitude = place["longitude"]

    data = await http_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 3,
        },
    )
    current = (data or {}).get("current") or {}
    daily = (data or {}).get("daily") or {}

    location = ", ".join(
        part for part in (place.get("name"), place.get("admin1"), place.get("country")) if part
    )
    lines = [
        f"🌍 {location}",
        f"🌡 Ahora: {current.get('temperature_2m')} °C "
        f"(sensación {current.get('apparent_temperature')} °C)",
        f"🌤 {weather_label(current.get('weather_code'))}",
        f"💧 Humedad: {current.get('relative_humidity_2m')}%  •  "
        f"🌬 Viento: {current.get('wind_speed_10m')} km/h  •  "
        f"🌧 Precipitación: {current.get('precipitation')} mm",
    ]
    days = daily.get("time") or []
    maximums = daily.get("temperature_2m_max") or []
    minimums = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_probability_max") or []
    if days:
        lines.append("\n📅 Próximos días:")
        for index, day in enumerate(days[:3]):
            label = "hoy" if index == 0 else day[5:]
            high = maximums[index] if index < len(maximums) else "?"
            low = minimums[index] if index < len(minimums) else "?"
            rain = rains[index] if index < len(rains) else "?"
            lines.append(f"• {label}: {high} / {low} °C — lluvia {rain}%")
    return "\n".join(lines)


async def cmd_clima(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = require_args(context, "/clima <ciudad>   ej. /clima Bogotá")
    report = await cached(f"clima:{fold(city)}", 600, lambda: weather_report(city))
    await update.effective_message.reply_text(truncate(str(report)))


# --------------------------------------------------------------------------
# Noticias (scraping de RSS)
# --------------------------------------------------------------------------
NEWS_FEEDS = [
    ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml"),
    ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"),
]


def parse_feed(xml_text: str, limit: int = 4) -> list[tuple[str, str]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items: list[tuple[str, str]] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = ""
        link = ""
        for child in node:
            child_tag = child.tag.split("}")[-1]
            if child_tag == "title" and not title:
                title = html.unescape((child.text or "").strip())
            elif child_tag == "link" and not link:
                link = (child.text or "").strip() or (child.get("href") or "").strip()
        if title and link:
            items.append((title, link))
        if len(items) >= limit:
            break
    return items


async def news_report() -> str:
    responses = await asyncio.gather(
        *(http_text(url) for _, url in NEWS_FEEDS), return_exceptions=True
    )
    blocks = []
    for (source, _), response in zip(NEWS_FEEDS, responses):
        if isinstance(response, BaseException):
            continue
        items = parse_feed(response)
        if items:
            lines = [f"📰 {source}"]
            lines.extend(f"• {title}\n  {link}" for title, link in items)
            blocks.append("\n".join(lines))
    if not blocks:
        raise CommandError("No pude leer los periódicos ahora mismo. Inténtalo en un rato.")
    return "\n\n".join(blocks)


async def cmd_noticias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await news_report()))


# --------------------------------------------------------------------------
# Wikipedia
# --------------------------------------------------------------------------
async def wiki_report(query: str) -> str:
    search = await http_json(
        "https://es.wikipedia.org/w/api.php",
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1,
        },
    )
    hits = (((search or {}).get("query") or {}).get("search")) or []
    if not hits:
        raise CommandError(f"No encontré nada sobre «{query}» en Wikipedia.")
    title = hits[0]["title"]
    page = quote(title.replace(" ", "_"), safe="")
    try:
        summary = await http_json(f"https://es.wikipedia.org/api/rest_v1/page/summary/{page}")
    except HTTPError:
        summary = {}
    extract = (summary or {}).get("extract") or f"Artículo: {title}"
    link = (
        ((summary or {}).get("content_urls") or {}).get("desktop") or {}
    ).get("page") or f"https://es.wikipedia.org/wiki/{page}"
    return f"📚 {title}\n\n{truncate(extract, 1500)}\n\n🔗 {link}"


async def cmd_wiki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/wiki <tema>   ej. /wiki Python")
    report = await cached(f"wiki:{fold(query)}", 1800, lambda: wiki_report(query))
    await update.effective_message.reply_text(truncate(str(report)))


# --------------------------------------------------------------------------
# Traducción (MyMemory, sin key)
# --------------------------------------------------------------------------
def normalise_language(value: str) -> str:
    return value.strip().lower().replace("_", "-").split("-")[0][:3]


async def translate(text: str, source: str, target: str) -> str:
    if len(text) > 480:
        raise CommandError("El texto es muy largo: mándame menos de 480 caracteres.")
    data = await http_json(
        "https://api.mymemory.translated.net/get",
        {"q": text, "langpair": f"{source}|{target}"},
    )
    status = (data or {}).get("responseStatus")
    translated = ((data or {}).get("responseData") or {}).get("translatedText") or ""
    if str(status) != "200" or not translated:
        detail = (data or {}).get("responseDetails") or "el servicio no devolvió traducción"
        raise CommandError(f"No pude traducir: {detail}")
    return html.unescape(translated)


async def cmd_traducir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 2:
        raise CommandError(
            "Uso: /traducir <idioma> <texto>\n"
            "Ej: /traducir en hola mundo\n"
            "También: /traducir es>en texto en inglés"
        )
    target = args[0].lower().replace(">", "|")
    text = " ".join(args[1:]).strip()
    if "|" in target:
        source, _, target = target.partition("|")
    else:
        source = "es"
    source = normalise_language(source)
    target = normalise_language(target)
    if not source or not target:
        raise CommandError("Dime el idioma con dos letras: en, es, fr, de, pt…")
    translated = await translate(text, source, target)
    await update.effective_message.reply_text(
        truncate(f"🌐 {source} → {target}\n\n{translated}")
    )


# --------------------------------------------------------------------------
# Criptomonedas (CoinGecko, sin key)
# --------------------------------------------------------------------------
async def crypto_report(query: str) -> str:
    search = await http_json("https://api.coingecko.com/api/v3/search", {"query": query})
    coins = (search or {}).get("coins") or []
    if not coins:
        raise CommandError(f"No encontré la criptomoneda «{query}».")
    coin = coins[0]
    coin_id = coin["id"]
    prices = await http_json(
        "https://api.coingecko.com/api/v3/simple/price",
        {
            "ids": coin_id,
            "vs_currencies": "usd,eur",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        },
    )
    data = (prices or {}).get(coin_id) or {}
    if not data:
        raise CommandError("CoinGecko no devolvió el precio ahora mismo.")
    change = data.get("usd_24h_change")
    arrow = "📈" if (change or 0) >= 0 else "📉"
    updated = data.get("last_updated_at")
    stamp = ""
    if isinstance(updated, (int, float)):
        stamp = datetime.fromtimestamp(updated, tz=timezone.utc).strftime("%d/%m %H:%M UTC")
    return (
        f"🪙 {coin.get('name')} ({str(coin.get('symbol') or '').upper()})\n"
        f"💵 ${money(data.get('usd', 0))}\n"
        f"💶 €{money(data.get('eur', 0))}\n"
        f"{arrow} 24 h: {change:+.2f}%\n"
        f"🕒 {stamp}"
    )


async def cmd_cripto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/cripto <moneda>   ej. /cripto bitcoin")
    await update.effective_message.reply_text(truncate(await crypto_report(query)))


# --------------------------------------------------------------------------
# Divisas (exchangerate-api, sin key)
# --------------------------------------------------------------------------
async def exchange_report(amount: float, source: str, target: str) -> str:
    source = source.upper()
    target = target.upper()
    data = await http_json(f"https://open.er-api.com/v6/latest/{quote(source, safe='')}")
    if (data or {}).get("result") != "success":
        raise CommandError(f"No conozco la moneda «{source}». Usa códigos como USD, EUR, COP…")
    rates = (data or {}).get("rates") or {}
    if target not in rates:
        raise CommandError(f"No conozco la moneda «{target}». Usa códigos como USD, EUR, COP…")
    rate = rates[target]
    converted = amount * rate
    return (
        f"💱 {amount:,.2f} {source} = {converted:,.2f} {target}\n"
        f"1 {source} = {rate:,.4f} {target}\n"
        f"Actualizado: {(data or {}).get('time_last_update_utc', '')}"
    )


async def cmd_divisa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    if len(args) < 2:
        raise CommandError("Uso: /divisa <cantidad> <de> <a>   ej. /divisa 100 USD EUR")
    amount = 1.0
    if re.fullmatch(r"\d+([.,]\d+)?", args[0]):
        amount = float(args.pop(0).replace(",", "."))
    if len(args) < 2:
        raise CommandError("Me faltan las monedas: /divisa 100 USD EUR")
    await update.effective_message.reply_text(
        truncate(await exchange_report(amount, args[0], args[1]))
    )


# --------------------------------------------------------------------------
# Sismos (USGS, sin key)
# --------------------------------------------------------------------------
async def earthquakes_report() -> str:
    data = await http_json(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
    )
    features = (data or {}).get("features") or []
    if not features:
        return "🌎 No hay sismos de magnitud 2.5 o más en las últimas 24 horas."
    ranked = sorted(
        features, key=lambda item: (item.get("properties") or {}).get("mag") or 0, reverse=True
    )[:5]
    lines = ["🌎 Sismos más fuertes (24 h, magnitud 2.5+):"]
    for item in ranked:
        props = item.get("properties") or {}
        moment = props.get("time")
        stamp = (
            datetime.fromtimestamp(moment / 1000, tz=timezone.utc).strftime("%d/%m %H:%M UTC")
            if isinstance(moment, (int, float))
            else "?"
        )
        lines.append(
            f"• M{props.get('mag')} — {props.get('place') or 'sin lugar'}\n  {stamp}"
        )
    return "\n".join(lines)


async def cmd_sismos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await earthquakes_report()))


# --------------------------------------------------------------------------
# Pokémon (PokeAPI, sin key)
# --------------------------------------------------------------------------
async def pokemon_report(name: str) -> tuple[str, str | None]:
    try:
        data = await http_json(
            "https://pokeapi.co/api/v2/pokemon/"
            + quote(name.strip().lower().replace(" ", "-"), safe="")
        )
    except HTTPError as exc:
        if exc.code == 404:
            raise CommandError(f"No encontré el Pokémon «{name}». Revisa cómo se escribe.")
        raise CommandError(f"PokeAPI respondió HTTP {exc.code}. Prueba otra vez en un rato.")
    except (URLError, TimeoutError, OSError) as exc:
        raise CommandError(f"No pude contactar con PokeAPI: {exc}")
    if not isinstance(data, dict) or "name" not in data:
        raise CommandError(f"No encontré el Pokémon «{name}».")
    types = ", ".join(
        entry.get("type", {}).get("name", "?") for entry in data.get("types") or []
    )
    stats = {entry["stat"]["name"]: entry["base_stat"] for entry in data.get("stats") or []}
    height = (data.get("height") or 0) / 10
    weight = (data.get("weight") or 0) / 10
    lines = [
        f"⚡ #{data.get('id')} {str(data.get('name')).replace('-', ' ').title()}",
        f"🧬 Tipo: {types or '?'}",
        f"📏 {height:.1f} m  •  ⚖️ {weight:.1f} kg",
        f"❤️ PS {stats.get('hp', '?')}  •  ⚔️ ATQ {stats.get('attack', '?')}  •  "
        f"🛡 DEF {stats.get('defense', '?')}  •  💨 VEL {stats.get('speed', '?')}",
    ]
    sprites = data.get("sprites") or {}
    artwork = (sprites.get("other") or {}).get("official-artwork") or {}
    image = artwork.get("front_default") or sprites.get("front_default")
    return "\n".join(lines), (str(image) if image else None)


async def cmd_pokemon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = require_args(context, "/pokemon <nombre>   ej. /pokemon pikachu")
    report, image = await pokemon_report(name)
    message = update.effective_message
    if image:
        try:
            await message.reply_photo(photo=image, caption=truncate(report, 1000))
            return
        except TelegramError:
            pass
    await message.reply_text(truncate(report))


# --------------------------------------------------------------------------
# Fotos: perros y gatos
# --------------------------------------------------------------------------
async def cmd_perro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await http_json("https://dog.ceo/api/breeds/image/random")
    image = (data or {}).get("message")
    if not image:
        raise CommandError("El servicio de perros no respondió. Prueba otra vez.")
    await update.effective_message.reply_photo(photo=image, caption="🐶")


async def cmd_gato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    image = f"https://cataas.com/cat?width=600&t={random.randint(1, 10**9)}"
    await update.effective_message.reply_photo(photo=image, caption="🐱")


# --------------------------------------------------------------------------
# Chistes (JokeAPI, sin key)
# --------------------------------------------------------------------------
async def joke_report() -> str:
    data = await http_json(
        "https://v2.jokeapi.dev/joke/Any",
        {"lang": "es", "blacklistFlags": "nsfw,racist,sexist"},
    )
    if not isinstance(data, dict) or data.get("error"):
        data = await http_json(
            "https://v2.jokeapi.dev/joke/Any",
            {"lang": "en", "blacklistFlags": "nsfw,racist,sexist"},
        )
    if not isinstance(data, dict) or data.get("error"):
        raise CommandError("No encontré chistes ahora mismo.")
    if data.get("type") == "twopart":
        return f"😂 {data.get('setup')}\n\n{data.get('delivery')}"
    return f"😂 {data.get('joke')}"


async def cmd_chiste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await joke_report()))


# --------------------------------------------------------------------------
# GitHub (API pública sin token, 60 peticiones/hora)
# --------------------------------------------------------------------------
async def github_report(username: str) -> str:
    user = await http_json(f"https://api.github.com/users/{quote(username.strip(), safe='')}")
    if not isinstance(user, dict) or not user.get("login"):
        raise CommandError(f"No encontré el usuario «{username}» en GitHub.")
    repos = await http_json(
        f"https://api.github.com/users/{quote(user['login'], safe='')}/repos",
        {"sort": "updated", "per_page": 3},
    )
    lines = [f"🐙 {user.get('name') or user['login']} (@{user['login']})"]
    if user.get("bio"):
        lines.append(f"📝 {user['bio']}")
    lines.append(
        f"📦 {user.get('public_repos', 0)} repos  •  👥 {user.get('followers', 0)} seguidores  •  "
        f"➡️ {user.get('following', 0)} siguiendo"
    )
    if user.get("location"):
        lines.append(f"📍 {user['location']}")
    if user.get("created_at"):
        lines.append(f"📅 En GitHub desde {str(user['created_at'])[:10]}")
    if isinstance(repos, list) and repos:
        lines.append("\n🕒 Repos actualizados:")
        for repo in repos[:3]:
            star = repo.get("stargazers_count", 0)
            lines.append(f"• {repo.get('name')} ⭐{star}\n  {repo.get('html_url')}")
    lines.append(f"\n🔗 {user.get('html_url')}")
    return "\n".join(lines)


async def cmd_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = require_args(context, "/github <usuario>   ej. /github torvalds")
    await update.effective_message.reply_text(truncate(await github_report(username)))


# --------------------------------------------------------------------------
# IP, DNS y WHOIS
# --------------------------------------------------------------------------
async def ip_report(query: str) -> str:
    data = await http_json(
        f"http://ip-api.com/json/{quote(query, safe='')}",
        {
            "fields": "status,message,country,regionName,city,isp,org,as,query,timezone,lat,lon",
            "lang": "es",
        },
    )
    if not isinstance(data, dict) or data.get("status") != "success":
        message = (data or {}).get("message") if isinstance(data, dict) else None
        raise CommandError(f"No pude consultar esa IP: {message or 'dirección no válida'}.")
    lines = [
        f"🌐 IP: {data.get('query')}",
        f"📍 {', '.join(filter(None, (data.get('city'), data.get('regionName'), data.get('country'))))}",
        f"🏢 {data.get('isp') or data.get('org') or '?'}",
        f"🕒 Zona horaria: {data.get('timezone')}",
    ]
    if data.get("as"):
        lines.append(f"🔗 {data['as']}")
    if data.get("lat") is not None:
        lines.append(
            f"🗺 https://www.openstreetmap.org/?mlat={data['lat']}&mlon={data['lon']}"
            f"#map=10/{data['lat']}/{data['lon']}"
        )
    return "\n".join(lines)


async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if query:
        query = query.replace("https://", "").replace("http://", "").strip("/")
    await update.effective_message.reply_text(truncate(await ip_report(query)))


DNS_STATUS = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}
DNS_TYPES = {"A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "SRV", "CAA"}


async def dns_report(domain: str, record_type: str) -> str:
    record_type = record_type.upper()
    if record_type not in DNS_TYPES:
        raise CommandError(f"Tipo no soportado. Usa: {', '.join(sorted(DNS_TYPES))}.")
    data = await http_json(
        "https://dns.google/resolve", {"name": domain, "type": record_type}
    )
    if not isinstance(data, dict):
        raise CommandError("El resolutor DNS no devolvió datos.")
    status = DNS_STATUS.get(data.get("Status"), f"error {data.get('Status')}")
    answers = data.get("Answer") or []
    lines = [f"🧭 {domain} — registro {record_type}", f"Estado: {status}"]
    if not answers:
        lines.append("Sin registros de ese tipo.")
    for answer in answers[:12]:
        lines.append(f"• {answer.get('data')}  (TTL {answer.get('TTL')})")
    return "\n".join(lines)


async def cmd_dns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    if not args:
        raise CommandError("Uso: /dns <dominio> [tipo]   ej. /dns github.com MX")
    domain = args[0]
    record_type = args[1] if len(args) > 1 else "A"
    await update.effective_message.reply_text(truncate(await dns_report(domain, record_type)))


def clean_domain(value: str) -> str:
    domain = value.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    domain = domain.split("/")[0].split("@")[-1].split(":")[0]
    return domain.removeprefix("www.")


async def whois_report(domain: str) -> str:
    data = await http_json(f"https://rdap.org/domain/{quote(domain, safe='')}")
    if not isinstance(data, dict) or not (data.get("ldhName") or data.get("handle")):
        raise CommandError(
            f"No hay datos RDAP para «{domain}». Prueba con el dominio sin http ni rutas."
        )
    lines = [f"🔎 {data.get('ldhName') or domain}"]
    events = {event.get("eventAction"): str(event.get("eventDate"))[:10] for event in data.get("events") or []}
    for label, key in (
        ("Creado", "registration"),
        ("Caduca", "expiration"),
        ("Actualizado", "last changed"),
    ):
        if events.get(key):
            lines.append(f"📅 {label}: {events[key]}")
    nameservers = [
        (server.get("ldhName") or "").lower() for server in data.get("nameservers") or []
    ]
    nameservers = [server for server in nameservers if server]
    if nameservers:
        lines.append("🌐 Nameservers: " + ", ".join(nameservers[:6]))
    statuses = data.get("status") or []
    if statuses:
        lines.append("⚙️ Estado: " + ", ".join(str(status) for status in statuses[:4]))
    entities = data.get("entities") or []
    if entities:
        roles = sorted(
            {
                role
                for entity in entities
                for role in entity.get("roles") or []
                if role not in ("registrar", "abuse")
            }
        )
        if roles:
            lines.append("👤 Roles: " + ", ".join(roles[:6]))
    lines.append(f"🔗 https://rdap.org/domain/{domain}")
    return "\n".join(lines)


async def cmd_whois(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    domain = clean_domain(require_args(context, "/whois <dominio>   ej. /whois github.com"))
    await update.effective_message.reply_text(truncate(await whois_report(domain)))


# --------------------------------------------------------------------------
# YouTube (scraping de la página de resultados)
# --------------------------------------------------------------------------
def extract_json_object(text: str, marker: str) -> dict | None:
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : position + 1])
                except json.JSONDecodeError:
                    return None
    return None


def collect_videos(node: object, found: list[tuple[str, str, str, str]], limit: int = 5) -> None:
    if len(found) >= limit:
        return
    if isinstance(node, dict):
        renderer = node.get("videoRenderer")
        if isinstance(renderer, dict):
            video_id = renderer.get("videoId")
            runs = (renderer.get("title") or {}).get("runs") or []
            title = "".join(run.get("text", "") for run in runs).strip()
            if video_id and title and all(item[0] != video_id for item in found):
                channel_runs = (renderer.get("ownerText") or {}).get("runs") or []
                channel = channel_runs[0].get("text", "") if channel_runs else ""
                length = (renderer.get("lengthText") or {}).get("simpleText", "")
                views = (renderer.get("viewCountText") or {}).get("simpleText", "")
                found.append((video_id, title, channel, f"{length} · {views}".strip(" ·")))
        for value in node.values():
            collect_videos(value, found, limit)
    elif isinstance(node, list):
        for value in node:
            collect_videos(value, found, limit)


async def youtube_report(query: str) -> str:
    page = await http_text(
        "https://www.youtube.com/results", {"search_query": query, "hl": "es"}
    )
    data = extract_json_object(page, "ytInitialData")
    videos: list[tuple[str, str, str, str]] = []
    if data:
        collect_videos(data, videos)
    if not videos:
        raise CommandError("YouTube no devolvió resultados. Prueba con otra búsqueda.")
    lines = [f"▶️ Resultados para «{query}»:"]
    for video_id, title, channel, extra in videos:
        lines.append(f"• {title}\n  {channel}  {extra}\n  https://youtu.be/{video_id}")
    return "\n".join(lines)


async def cmd_yt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/yt <búsqueda>   ej. /yt música para estudiar")
    await update.effective_message.reply_text(truncate(await youtube_report(query)))


# --------------------------------------------------------------------------
# QR, acortador, dados y moneda
# --------------------------------------------------------------------------
async def cmd_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    content = require_args(context, "/qr <texto o enlace>")
    image = (
        "https://quickchart.io/qr?size=500&margin=2&text=" + quote(content, safe="")
    )
    await update.effective_message.reply_photo(photo=image, caption="📱 Tu QR")


async def cmd_acortar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = require_args(context, "/acortar <url>   ej. /acortar https://example.com")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise CommandError("Mándame una URL completa que empiece por http:// o https://")
    short = (await http_text(
        "https://tinyurl.com/api-create.php", {"url": url}
    )).strip()
    if not short.startswith("http"):
        raise CommandError("El acortador no aceptó esa URL. Prueba con otra.")
    await update.effective_message.reply_text(f"🔗 {short}")


async def cmd_dado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    faces = 6
    if context.args:
        try:
            faces = int(context.args[0])
        except ValueError:
            raise CommandError("Uso: /dado [caras]   ej. /dado 20")
    if not 2 <= faces <= 1000:
        raise CommandError("El dado tiene que tener entre 2 y 1000 caras.")
    await update.effective_message.reply_text(f"🎲 {random.randint(1, faces)} (de {faces})")


async def cmd_moneda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(random.choice(["🪙 Cara", "🪙 Cruz"]))


# --------------------------------------------------------------------------
# NASA (DEMO_KEY / images-api) y CDN de imágenes
# --------------------------------------------------------------------------
async def nasa_report(topic: str | None) -> tuple[str, str | None]:
    """Devuelve (texto, imagen). Sin tema: foto astronómica del día."""
    if topic:
        data = await http_json(
            "https://images-api.nasa.gov/search",
            {"q": topic, "media_type": "image", "page_size": 3},
        )
        items = ((data or {}).get("collection") or {}).get("items") or []
        if not items:
            raise CommandError(f"La NASA no tiene imágenes de «{topic}». Prueba otra palabra.")
        lines = [f"🚀 NASA — imágenes de «{topic}»", ""]
        image: str | None = None
        for item in items[:3]:
            info = (item.get("data") or [{}])[0]
            title = info.get("title") or "Sin título"
            date = (info.get("date_created") or "")[:10]
            nasa_id = info.get("nasa_id") or ""
            lines.append(f"• {title} ({date})")
            if nasa_id:
                lines.append(f"  https://images.nasa.gov/details/{nasa_id}")
            if image is None:
                links = item.get("links") or []
                if links:
                    image = links[0].get("href")
        return "\n".join(lines), image

    # La foto de hoy a veces falla en la API, así que pido la de ayer.
    day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    data = await http_json(
        "https://api.nasa.gov/planetary/apod",
        {"api_key": KEYS.get("nasa") or "DEMO_KEY", "date": day},
    )
    if not isinstance(data, dict) or not data.get("title"):
        raise CommandError("La NASA no respondió bien ahora mismo. Prueba en un rato.")
    if data.get("media_type") != "image" or "To view this video" in str(
        data.get("explanation") or ""
    ):
        # Algunos días la foto del día es un vídeo: muestro imágenes de NASA.
        return await nasa_report("astronomy")
    lines = [
        f"🚀 {data.get('title')} ({data.get('date')})",
        "",
        truncate(strip_html(data.get("explanation")), 900),
    ]
    if data.get("copyright"):
        lines.append(f"\n📸 {data['copyright']}")
    link = data.get("hdurl") or data.get("url")
    if link:
        lines.append(f"\n🔗 {link}")
    image = data.get("url") if data.get("media_type") == "image" else None
    return "\n".join(lines), image


async def cmd_nasa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    topic = " ".join(context.args).strip() if context.args else ""
    text, image = await nasa_report(topic or None)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


# --------------------------------------------------------------------------
# Recetas y cócteles (TheMealDB / TheCocktailDB, clave pública "1")
# --------------------------------------------------------------------------
async def meal_report(query: str | None) -> tuple[str, str | None]:
    if query:
        data = await http_json(
            "https://www.themealdb.com/api/json/v1/1/search.php", {"s": query}
        )
    else:
        data = await http_json("https://www.themealdb.com/api/json/v1/1/random.php")
    meals = (data.get("meals") or []) if isinstance(data, dict) else []
    if not meals:
        raise CommandError(
            f"No encontré recetas de «{query}». Prueba con un plato o ingrediente en inglés "
            "(chicken, rice, pasta…)."
        )
    meal = meals[0]
    ingredients = []
    for index in range(1, 21):
        name = (meal.get(f"strIngredient{index}") or "").strip()
        measure = (meal.get(f"strMeasure{index}") or "").strip()
        if name:
            ingredients.append(" ".join(part for part in (measure, name) if part))
    lines = [
        f"🍲 {meal.get('strMeal')}",
        f"🏷 {meal.get('strCategory') or '?'}  •  "
        f"🌎 {meal.get('strArea') or meal.get('strCountry') or '?'}",
        "",
        "🧺 Ingredientes:",
        *[f"• {item}" for item in ingredients[:15]],
        "",
        "👩‍🍳 Preparación:",
        truncate(strip_html(meal.get("strInstructions")), 1200),
    ]
    source = meal.get("strSource") or meal.get("strYoutube")
    if source:
        lines.append(f"\n🔗 {source}")
    else:
        lines.append(f"\n🔗 https://www.themealdb.com/meal/{meal.get('idMeal')}")
    return "\n".join(lines), meal.get("strMealThumb")


async def cmd_receta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    text, image = await meal_report(query or None)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


async def drink_report(query: str | None) -> tuple[str, str | None]:
    if query:
        data = await http_json(
            "https://www.thecocktaildb.com/api/json/v1/1/search.php", {"s": query}
        )
    else:
        data = await http_json("https://www.thecocktaildb.com/api/json/v1/1/random.php")
    drinks = (data.get("drinks") or []) if isinstance(data, dict) else []
    if not drinks:
        raise CommandError(f"No encontré cócteles de «{query}». Prueba con otro nombre.")
    drink = drinks[0]
    ingredients = []
    for index in range(1, 16):
        name = (drink.get(f"strIngredient{index}") or "").strip()
        measure = (drink.get(f"strMeasure{index}") or "").strip()
        if name:
            ingredients.append(" ".join(part for part in (measure, name) if part))
    lines = [
        f"🍹 {drink.get('strDrink')}",
        f"🏷 {drink.get('strCategory') or '?'}  •  🍸 {drink.get('strAlcoholic') or '?'}  •  "
        f"🥃 {drink.get('strGlass') or '?'}",
        "",
        "🧺 Ingredientes:",
        *[f"• {item}" for item in ingredients[:12]],
        "",
        "👨‍🍳 Preparación:",
        truncate(strip_html(drink.get("strInstructions")), 1000),
    ]
    return "\n".join(lines), drink.get("strDrinkThumb")


async def cmd_trago(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    text, image = await drink_report(query or None)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


# --------------------------------------------------------------------------
# Series (TVMaze) y anime (AniList, GraphQL)
# --------------------------------------------------------------------------
async def series_report(title: str) -> tuple[str, str | None]:
    results = await http_json("https://api.tvmaze.com/search/shows", {"q": title})
    if not isinstance(results, list) or not results:
        raise CommandError(f"No encontré la serie «{title}».")
    show = results[0].get("show") or {}
    network = (
        (show.get("network") or {}).get("name")
        or (show.get("webChannel") or {}).get("name")
        or "?"
    )
    rating = (show.get("rating") or {}).get("average")
    lines = [
        f"📺 {show.get('name')}",
        f"🏷 {', '.join(show.get('genres') or []) or 'sin género'}  •  📡 {network}",
        f"📅 {show.get('premiered') or '?'} → {show.get('ended') or 'en emisión'}  •  "
        f"⭐ {rating or '?'}/10",
        f"📆 Estado: {show.get('status') or '?'}",
        "",
        truncate(strip_html(show.get("summary")) or "Sin sinopsis.", 900),
        f"\n🔗 {show.get('url') or ''}",
    ]
    image = (show.get("image") or {}).get("medium")
    return "\n".join(lines), image


async def cmd_serie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = require_args(context, "/serie <título>   ej. /serie dark")
    text, image = await series_report(title)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


ANILIST_QUERY = """
query ($search: String) {
  Media(search: $search, type: ANIME) {
    title { romaji english }
    format status episodes averageScore genres
    description(asHtml: false)
    siteUrl
    coverImage { large }
  }
}
"""


async def anime_report(title: str) -> tuple[str, str | None]:
    data = await http_post_json(
        "https://graphql.anilist.co",
        {"query": ANILIST_QUERY, "variables": {"search": title}},
    )
    media = ((data or {}).get("data") or {}).get("Media") if isinstance(data, dict) else None
    if not media:
        raise CommandError(f"No encontré el anime «{title}».")
    names = media.get("title") or {}
    heading = names.get("romaji") or title
    if names.get("english"):
        heading += f" ({names['english']})"
    lines = [
        f"🍥 {heading}",
        f"📺 {media.get('format') or '?'}  •  📆 {media.get('status') or '?'}  •  "
        f"🎞 {media.get('episodes') or '?'} episodios",
        f"⭐ {media.get('averageScore') or '?'}/100  •  "
        f"🏷 {', '.join(media.get('genres') or []) or '?'}",
        "",
        truncate(strip_html(media.get("description")) or "Sin sinopsis.", 900),
        f"\n🔗 {media.get('siteUrl')}",
    ]
    return "\n".join(lines), (media.get("coverImage") or {}).get("large")


async def cmd_anime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = require_args(context, "/anime <título>   ej. /anime one piece")
    text, image = await anime_report(title)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


# --------------------------------------------------------------------------
# Libros (Open Library), aire (Open-Meteo) y países
# --------------------------------------------------------------------------
async def book_report(title: str) -> tuple[str, str | None]:
    data = await http_json("https://openlibrary.org/search.json", {"q": title, "limit": 1})
    docs = (data.get("docs") or []) if isinstance(data, dict) else []
    if not docs:
        raise CommandError(f"No encontré el libro «{title}» en Open Library.")
    book = docs[0]
    authors = ", ".join((book.get("author_name") or [])[:3]) or "autor desconocido"
    pages = book.get("number_of_pages_median")
    lines = [
        f"📖 {book.get('title')}",
        f"✍️ {authors}",
        f"📅 {book.get('first_publish_year') or '?'}  •  📄 {pages or '?'} páginas",
        f"\n🔗 https://openlibrary.org{book.get('key')}",
    ]
    cover = None
    if book.get("cover_i"):
        cover = f"https://covers.openlibrary.org/b/id/{book['cover_i']}-L.jpg"
    return "\n".join(lines), cover


async def cmd_libro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = require_args(context, "/libro <título>   ej. /libro cien años de soledad")
    text, image = await book_report(title)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


def aqi_label(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "sin dato"
    if number <= 20:
        return "buena 🟢"
    if number <= 40:
        return "aceptable 🟡"
    if number <= 60:
        return "moderada 🟠"
    if number <= 80:
        return "mala 🔴"
    if number <= 100:
        return "muy mala 🟣"
    return "peligrosa ⚫"


async def air_report(city: str) -> str:
    place = await geocode(city)
    data = await http_json(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "pm2_5,pm10,ozone,nitrogen_dioxide,european_aqi,us_aqi",
            "timezone": "auto",
        },
    )
    current = (data or {}).get("current") or {}
    if not current:
        raise CommandError(f"No hay datos de calidad del aire para «{city}».")
    location = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    return "\n".join(
        [
            f"🌬 Calidad del aire en {location}",
            f"🇪🇺 Índice europeo: {current.get('european_aqi')} — "
            f"{aqi_label(current.get('european_aqi'))}",
            f"🇺🇸 Índice de EE. UU.: {current.get('us_aqi')}",
            f"😷 PM2.5: {current.get('pm2_5')} µg/m³  •  PM10: {current.get('pm10')} µg/m³",
            f"🧪 Ozono: {current.get('ozone')} µg/m³  •  "
            f"NO₂: {current.get('nitrogen_dioxide')} µg/m³",
            f"🕒 Medido: {current.get('time')}",
        ]
    )


async def cmd_aire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = require_args(context, "/aire <ciudad>   ej. /aire Medellín")
    await update.effective_message.reply_text(truncate(await air_report(city)))


async def country_report(name: str) -> str:
    data = await http_json(
        "https://countriesnow.space/api/v0.1/countries/info",
        {"returns": "capital,currency,flag"},
    )
    rows = (data.get("data") or []) if isinstance(data, dict) else []
    query = fold(name)
    match = next((row for row in rows if fold(str(row.get("name"))) == query), None)
    if match is None:
        match = next((row for row in rows if query in fold(str(row.get("name")))), None)
    if match is None:
        raise CommandError(
            f"No encontré el país «{name}». Prueba con el nombre en inglés "
            "(Peru, United States, Japan…)."
        )
    lines = [
        f"🌍 {match.get('name')}",
        f"🏛 Capital: {match.get('capital') or '?'}",
        f"💵 Moneda: {match.get('currency') or '?'}",
    ]
    try:
        summary = await http_json(
            "https://es.wikipedia.org/api/rest_v1/page/summary/"
            + quote(str(match.get("name")).replace(" ", "_"), safe="")
        )
        extract = (summary or {}).get("extract")
        if extract:
            lines.extend(["", truncate(extract, 800)])
    except (HTTPError, URLError, TimeoutError, OSError):
        pass
    if match.get("flag"):
        lines.append(f"\n🏳 {match['flag']}")
    return "\n".join(lines)


async def cmd_pais(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = require_args(context, "/pais <nombre>   ej. /pais Colombia")
    await update.effective_message.reply_text(truncate(await country_report(name)))


# --------------------------------------------------------------------------
# Stack Overflow, radio, Minecraft, baraja y trivia
# --------------------------------------------------------------------------
async def stack_report(query: str) -> str:
    data = await http_json(
        "https://api.stackexchange.com/2.3/search/advanced",
        {
            "order": "desc",
            "sort": "relevance",
            "q": query,
            "site": "stackoverflow",
            "pagesize": 3,
            "filter": "default",
        },
    )
    items = (data.get("items") or []) if isinstance(data, dict) else []
    if not items:
        raise CommandError(f"No encontré preguntas de «{query}» en Stack Overflow.")
    lines = [f"💻 Stack Overflow — «{query}»", ""]
    for item in items[:3]:
        lines.append(f"• {html.unescape(item.get('title') or '')}")
        lines.append(
            f"  👍 {item.get('score', 0)}  •  ✅ {item.get('answer_count', 0)} respuestas  •  "
            f"👀 {item.get('view_count', 0)} visitas"
        )
        lines.append(f"  {item.get('link')}")
    return "\n".join(lines)


async def cmd_stack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/stack <búsqueda>   ej. /stack telegram bot python")
    await update.effective_message.reply_text(truncate(await stack_report(query)))


async def radio_report(query: str) -> str:
    data = await http_json(
        "https://de1.api.radio-browser.info/json/stations/search",
        {
            "name": query,
            "limit": 3,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        },
    )
    stations = data if isinstance(data, list) else []
    if not stations:
        raise CommandError(f"No encontré emisoras de «{query}».")
    lines = [f"📻 Emisoras para «{query}»", ""]
    for station in stations[:3]:
        lines.append(f"• {str(station.get('name') or '').strip()}")
        details = [
            station.get("country"),
            f"{station.get('bitrate') or '?'} kbps",
            station.get("codec"),
        ]
        lines.append("  " + " • ".join(str(part) for part in details if part))
        stream = station.get("url_resolved") or station.get("url")
        if stream:
            lines.append(f"  {stream}")
        if station.get("homepage"):
            lines.append(f"  {station['homepage']}")
    return "\n".join(lines)


async def cmd_radio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/radio <nombre>   ej. /radio salsa")
    await update.effective_message.reply_text(truncate(await radio_report(query)))


async def minecraft_report(host: str) -> str:
    data = await http_json(
        f"https://api.mcsrvstat.us/3/{quote(host.strip(), safe='')}"
    )
    if not isinstance(data, dict):
        raise CommandError("El servicio de Minecraft no respondió. Prueba otra vez.")
    if not data.get("online"):
        raise CommandError(f"El servidor «{host}» está apagado, no existe o no responde.")
    players = data.get("players") or {}
    version = data.get("version") or "?"
    if isinstance(version, dict):
        version = version.get("name") or "?"
    motd = " ".join((data.get("motd") or {}).get("clean") or [])
    lines = [
        f"⛏ {host} — en línea 🟢",
        f"👥 Jugadores: {players.get('online', 0)}/{players.get('max', 0)}",
        f"🧩 Versión: {version}",
    ]
    if motd:
        lines.append(f"💬 {motd}")
    names = players.get("list") or []
    if names:
        lines.append("🙋 " + ", ".join(str(name) for name in names[:10]))
    return "\n".join(lines)


async def cmd_mc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    host = require_args(context, "/mc <servidor>   ej. /mc mc.hypixel.net")
    await update.effective_message.reply_text(truncate(await minecraft_report(host)))


CARD_SUITS = {"HEARTS": "♥️", "DIAMONDS": "♦️", "CLUBS": "♣️", "SPADES": "♠️"}
CARD_VALUES = {"JACK": "J", "QUEEN": "Q", "KING": "K", "ACE": "A"}


async def cards_report(count: int) -> tuple[str, str | None]:
    data = await http_json(
        "https://deckofcardsapi.com/api/deck/new/draw/", {"count": count}
    )
    cards = (data.get("cards") or []) if isinstance(data, dict) else []
    if not cards:
        raise CommandError("La baraja no respondió. Prueba otra vez.")
    shown = []
    for card in cards:
        suit = CARD_SUITS.get(card.get("suit"), "")
        value = CARD_VALUES.get(card.get("value"), card.get("value"))
        shown.append(f"{suit} {value}")
    return "🃏 " + "   ".join(shown), cards[0].get("image")


async def cmd_carta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = 1
    if context.args:
        try:
            count = int(context.args[0])
        except ValueError:
            raise CommandError("Uso: /carta [cantidad]   ej. /carta 3")
    if not 1 <= count <= 10:
        raise CommandError("Puedo sacar entre 1 y 10 cartas.")
    text, image = await cards_report(count)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=text)
    else:
        await update.effective_message.reply_text(text)


async def trivia_report() -> str:
    data = await http_json(
        "https://opentdb.com/api.php",
        {"amount": 1, "type": "multiple", "encode": "url3986"},
    )
    results = (data.get("results") or []) if isinstance(data, dict) else []
    if not results:
        raise CommandError("No pude sacar una pregunta. Prueba otra vez en un momento.")
    item = results[0]
    question = unquote_plus(item.get("question") or "")
    correct = unquote_plus(item.get("correct_answer") or "")
    options = [unquote_plus(option) for option in item.get("incorrect_answers") or []]
    options.append(correct)
    random.shuffle(options)
    letters = "ABCDEF"
    lines = [f"❓ {question}", ""]
    for index, option in enumerate(options):
        lines.append(f"{letters[index]}. {option}")
    lines.append("")
    lines.append(
        f"🏷 {unquote_plus(item.get('category') or '?')}  •  "
        f"🎚 {unquote_plus(item.get('difficulty') or '?')}"
    )
    lines.append(f"✅ Respuesta: {correct}")
    return "\n".join(lines)


async def cmd_trivia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await trivia_report()))


# --------------------------------------------------------------------------
# Internet Archive, Telegraph y sol
# --------------------------------------------------------------------------
async def wayback_report(url: str) -> str:
    data = await http_json("https://archive.org/wayback/available", {"url": url})
    snapshot = ((data or {}).get("archived_snapshots") or {}).get("closest") or {}
    if not snapshot.get("available"):
        raise CommandError(
            "Internet Archive no tiene copia de esa página.\n"
            f"Puedes guardarla a mano en https://web.archive.org/save/{url}"
        )
    stamp = str(snapshot.get("timestamp") or "")
    when = "fecha desconocida"
    if len(stamp) >= 12:
        when = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]}"
    return f"🕰 Copia más cercana: {when}\n🔗 {snapshot.get('url')}"


async def cmd_wayback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = require_args(context, "/wayback <url>   ej. /wayback google.com")
    await update.effective_message.reply_text(truncate(await wayback_report(url)))


async def publish_report(title: str, body_text: str) -> str:
    account = await http_post_json(
        "https://api.telegra.ph/createAccount",
        {"short_name": "botpy", "author_name": "Bot"},
    )
    token = ((account or {}).get("result") or {}).get("access_token")
    if not token:
        raise CommandError("Telegraph no me dejó publicar ahora mismo.")
    paragraphs = [line.strip() for line in body_text.splitlines() if line.strip()]
    if not paragraphs:
        raise CommandError("No hay texto para publicar.")
    content = [{"tag": "p", "children": [line]} for line in paragraphs[:120]]
    page = await http_post_json(
        "https://api.telegra.ph/createPage",
        {
            "access_token": token,
            "title": title[:256],
            "author_name": "Bot",
            "content": content,
        },
    )
    result = (page or {}).get("result") or {}
    if not result.get("url"):
        error = (page or {}).get("error") or "error desconocido"
        raise CommandError(f"Telegraph rechazó la publicación: {error}")
    return f"📝 Publicado: {result['url']}"


async def cmd_publicar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = require_args(
        context, "/publicar [título |] <texto>   ej. /publicar Mis notas | Hola mundo"
    )
    if len(raw) > 6000:
        raise CommandError("El texto es muy largo: mándame menos de 6000 caracteres.")
    if "|" in raw:
        title, body_text = raw.split("|", 1)
    else:
        title, body_text = "Publicación del bot", raw
    await update.effective_message.reply_text(truncate(await publish_report(title.strip(), body_text)))


async def sun_report(city: str) -> str:
    place = await geocode(city)
    data = await http_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "sunrise,sunset,daylight_duration",
            "timezone": "auto",
            "forecast_days": 1,
        },
    )
    daily = (data or {}).get("daily") or {}
    sunrise = (daily.get("sunrise") or [None])[0]
    sunset = (daily.get("sunset") or [None])[0]
    if not sunrise or not sunset:
        raise CommandError(f"No hay datos de sol para «{city}».")
    duration = (daily.get("daylight_duration") or [0])[0]
    hours, minutes = divmod(int(duration or 0) // 60, 60)
    location = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    return "\n".join(
        [
            f"🌅 {location}",
            f"☀️ Amanecer: {str(sunrise)[11:16]}",
            f"🌇 Atardecer: {str(sunset)[11:16]}",
            f"⏳ Duración del día: {hours} h {minutes} min",
        ]
    )


async def cmd_sol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = require_args(context, "/sol <ciudad>   ej. /sol Bogotá")
    await update.effective_message.reply_text(truncate(await sun_report(city)))


# --------------------------------------------------------------------------
# Acciones (Twelve Data) y claves de APIs
# --------------------------------------------------------------------------
async def stock_report(symbol: str) -> str:
    key = KEYS.get("twelvedata") or "demo"
    try:
        data = await http_json(
            "https://api.twelvedata.com/quote", {"symbol": symbol.upper(), "apikey": key}
        )
    except HTTPError as exc:
        # Twelve Data responde 401 con el motivo en el cuerpo.
        raw = await asyncio.to_thread(exc.read)
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            data = {"message": f"Twelve Data respondió {exc.code}."}
    if not isinstance(data, dict) or data.get("code") or data.get("status") == "error":
        message = (data or {}).get("message") if isinstance(data, dict) else None
        hint = ""
        if str((data or {}).get("code")) == "401":
            hint = (
                "\n\nCon la clave \"demo\" solo funcionan AAPL, BTC/USD y EUR/USD. "
                "Pide tu clave gratis en https://twelvedata.com/pricing y pégala en "
                "KEYS['twelvedata'] al inicio de bot.py (ver /claves)."
            )
        raise CommandError((message or f"No encontré el símbolo «{symbol}».") + hint)
    try:
        change = float(data.get("change") or 0)
    except (TypeError, ValueError):
        change = 0.0
    arrow = "📈" if change >= 0 else "📉"
    close = data.get("close") or 0
    try:
        price = money(float(close))
    except (TypeError, ValueError):
        price = str(close)
    market = "mercado abierto 🟢" if data.get("is_market_open") else "mercado cerrado 🔴"
    return "\n".join(
        [
            f"{arrow} {data.get('name') or symbol.upper()} ({data.get('symbol')})",
            f"🏦 {data.get('exchange') or '?'}  •  💵 {data.get('currency') or '?'}",
            f"💰 Precio: {price}",
            f"🔁 Cambio: {data.get('change')} ({data.get('percent_change')}%)",
            f"📊 Abrió {data.get('open')}  •  ⬆️ {data.get('high')}  •  "
            f"⬇️ {data.get('low')}",
            f"🕒 {data.get('datetime') or ''}  •  {market}",
        ]
    )


async def cmd_accion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = require_args(context, "/accion <símbolo>   ej. /accion AAPL")
    await update.effective_message.reply_text(truncate(await stock_report(symbol)))


def mask_key(value: str) -> str:
    if not value:
        return "vacía"
    if len(value) <= 8:
        return value[:2] + "…"
    return f"{value[:4]}…{value[-4:]}"


async def cmd_claves(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["🔑 Claves de APIs", ""]
    for name, label, url, note in KEY_INFO:
        current = KEYS.get(name, "")
        state = f"puesta ({mask_key(current)})" if current else "vacía"
        lines.append(f"• {label}: {state}")
        lines.append(f"  {note}")
        lines.append(f"  {url}")
    lines.append("")
    lines.append(
        "Pégalas en el diccionario KEYS al inicio de bot.py y reinicia el bot. "
        "Todas tienen plan gratis y el bot funciona sin ellas."
    )
    await update.effective_message.reply_text(truncate("\n".join(lines)))


# --------------------------------------------------------------------------
# GIF y películas (necesitan clave propia y gratis)
# --------------------------------------------------------------------------
async def gif_report(query: str) -> str:
    key = KEYS.get("giphy", "")
    if not key:
        raise CommandError(
            "Para /gif necesito una clave gratis de Giphy.\n"
            "Créala en https://developers.giphy.com/dashboard/ y pégala en "
            "KEYS['giphy'] al inicio de bot.py. (Ver /claves)"
        )
    data = await http_json(
        "https://api.giphy.com/v1/gifs/search",
        {"api_key": key, "q": query, "limit": 1, "rating": "g", "lang": "es"},
    )
    hits = (data.get("data") or []) if isinstance(data, dict) else []
    if not hits:
        raise CommandError(f"No encontré GIFs de «{query}». Prueba con otra palabra.")
    gif = hits[0]
    media = (gif.get("images") or {}).get("fixed_height") or {}
    url = media.get("url") or gif.get("url")
    if not url:
        raise CommandError("Giphy no devolvió el GIF.")
    return html.unescape(gif.get("title") or query), url


async def cmd_gif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/gif <búsqueda>   ej. /gif gato bailando")
    caption, url = await gif_report(query)
    await update.effective_message.reply_animation(animation=url, caption=truncate(caption, 200))


async def movie_report(title: str) -> tuple[str, str | None]:
    key = KEYS.get("omdb", "")
    if not key:
        raise CommandError(
            "Para /pelicula necesito una clave gratis de OMDb.\n"
            "Pídela en https://www.omdbapi.com/apikey.aspx y pégala en "
            "KEYS['omdb'] al inicio de bot.py. (Ver /claves)"
        )
    data = await http_json(
        "https://www.omdbapi.com/", {"apikey": key, "t": title, "plot": "short"}
    )
    if not isinstance(data, dict) or data.get("Response") == "False":
        error = (data or {}).get("Error") if isinstance(data, dict) else None
        raise CommandError(f"No encontré la película «{title}». {error or ''}".strip())
    lines = [
        f"🎬 {data.get('Title')} ({data.get('Year')})",
        f"🏷 {data.get('Genre')}  •  ⏱ {data.get('Runtime')}  •  "
        f"⭐ {data.get('imdbRating')}/10",
        f"🎥 Director: {data.get('Director')}",
        f"👥 {data.get('Actors')}",
        "",
        truncate(data.get("Plot") or "Sin sinopsis.", 700),
        f"\n🔗 https://www.imdb.com/title/{data.get('imdbID')}/",
    ]
    poster = data.get("Poster")
    image = poster if poster and poster != "N/A" else None
    return "\n".join(lines), image


async def cmd_pelicula(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    title = require_args(context, "/pelicula <título>   ej. /pelicula matrix")
    text, image = await movie_report(title)
    if image:
        await update.effective_message.reply_photo(photo=image, caption=truncate(text, 1000))
    else:
        await update.effective_message.reply_text(truncate(text))


# --------------------------------------------------------------------------
# Segunda tanda de APIs (públicas, gratis y en su mayoría sin clave)
# --------------------------------------------------------------------------
async def iss_report() -> str:
    data = await http_json("https://api.wheretheiss.at/v1/satellites/25544")
    if not isinstance(data, dict) or data.get("latitude") is None:
        raise CommandError("No pude localizar la ISS ahora mismo.")
    lat = float(data["latitude"])
    lon = float(data["longitude"])
    visibility = {
        "daylight": "a la luz del día ☀️",
        "eclipsed": "en la sombra de la Tierra 🌑",
    }.get(str(data.get("visibility")), str(data.get("visibility") or "?"))
    return "\n".join(
        [
            "🛰 Estación Espacial Internacional",
            f"📍 Latitud {lat:.3f}  •  Longitud {lon:.3f}",
            f"🚀 Altitud {float(data.get('altitude') or 0):,.0f} km  •  "
            f"⚡ {float(data.get('velocity') or 0):,.0f} km/h",
            f"🌗 Ahora mismo: {visibility}",
            f"🗺 https://www.openstreetmap.org/?mlat={lat:.3f}&mlon={lon:.3f}"
            f"#map=3/{lat:.3f}/{lon:.3f}",
        ]
    )


async def cmd_iss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await iss_report()))


async def art_report(query: str) -> tuple[str, str | None]:
    data = await http_json(
        "https://api.artic.edu/api/v1/artworks/search",
        {
            "q": query,
            "limit": 1,
            "fields": "title,artist_display,date_display,medium_display,image_id,department_title",
        },
    )
    hits = (data.get("data") or []) if isinstance(data, dict) else []
    if not hits:
        raise CommandError(f"No encontré obras de «{query}» en el museo de Chicago.")
    item = hits[0]
    lines = [
        f"🖼 {html.unescape(str(item.get('title') or 'Sin título'))}",
        f"🎨 {html.unescape(str(item.get('artist_display') or 'Autor desconocido'))}",
        f"📅 {item.get('date_display') or '?'}  •  "
        f"🏛 {item.get('department_title') or '?'}",
        f"🧱 {item.get('medium_display') or ''}".strip(),
    ]
    image_id = item.get("image_id")
    image = (
        f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
        if image_id
        else None
    )
    return "\n".join(line for line in lines if line), image


async def cmd_arte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/arte <búsqueda>   ej. /arte girasoles")
    text, image = await art_report(query)
    if image:
        await update.effective_message.reply_photo(
            photo=image, caption=truncate(text, 900)
        )
    else:
        await update.effective_message.reply_text(truncate(text))


async def poem_report(author: str | None) -> str:
    if author:
        titles = await http_json(
            f"https://poetrydb.org/author/{quote(author, safe='')}/title"
        )
        if not isinstance(titles, list) or not titles:
            raise CommandError(f"No encontré poemas de «{author}».")
        title = str(random.choice(titles).get("title") or "")
        poems = await http_json(f"https://poetrydb.org/title/{quote(title, safe='')}")
    else:
        poems = await http_json("https://poetrydb.org/random/1")
    if not isinstance(poems, list) or not poems:
        raise CommandError("PoetryDB no devolvió ningún poema. Prueba otra vez.")
    poem = poems[0]
    lines = [str(line) for line in (poem.get("lines") or [])]
    body = "\n".join(lines[:18])
    if len(lines) > 18:
        body += "\n…"
    return (
        f"📜 {poem.get('title') or 'Sin título'}\n"
        f"✍️ {poem.get('author') or 'Anónimo'}\n\n{body}\n\n📚 poetrydb.org"
    )


async def cmd_poema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    author = " ".join(context.args).strip() if context.args else None
    await update.effective_message.reply_text(truncate(await poem_report(author)))


async def advice_report() -> str:
    data = await http_json(
        "https://api.adviceslip.com/advice", {"t": int(time.time())}
    )
    slip = (data.get("slip") or {}) if isinstance(data, dict) else {}
    advice = slip.get("advice")
    if not advice:
        raise CommandError("No pude sacar un consejo. Prueba otra vez.")
    lines = ["💡 Consejo del día", "", f"🇬🇧 {advice}"]
    try:
        spanish = await translate(advice, "en", "es")
    except (CommandError, HTTPError, URLError, ValueError):
        spanish = ""
    if spanish and spanish.strip().lower() != advice.strip().lower():
        lines += ["", f"🇪🇸 {spanish}"]
    return "\n".join(lines)


async def cmd_consejo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await advice_report()))


async def name_report(name: str) -> str:
    age, gender, nation = await asyncio.gather(
        http_json("https://api.agify.io/", {"name": name}),
        http_json("https://api.genderize.io/", {"name": name}),
        http_json("https://api.nationalize.io/", {"name": name}),
        return_exceptions=True,
    )
    lines = [f"🧑 Analizando el nombre «{name}»", ""]
    if isinstance(age, dict) and age.get("age"):
        count = age.get("count") or 0
        lines.append(f"🎂 Edad media: {age.get('age')} años ({count:,} personas)")
    if isinstance(gender, dict) and gender.get("gender"):
        percent = float(gender.get("probability") or 0) * 100
        label = "hombre" if gender.get("gender") == "male" else "mujer"
        lines.append(f"🚻 Probablemente {label} ({percent:.0f}%)")
    countries = (nation.get("country") or []) if isinstance(nation, dict) else []
    if countries:
        parts = []
        for item in countries[:3]:
            percent = float(item.get("probability") or 0) * 100
            parts.append(f"{item.get('country_id')} ({percent:.0f}%)")
        lines.append("🌍 Países probables: " + ", ".join(parts))
    if len(lines) <= 3:
        raise CommandError("No hay suficientes datos para ese nombre. Prueba con otro.")
    lines += ["", "📊 Datos de agify.io, genderize.io y nationalize.io"]
    return "\n".join(lines)


async def cmd_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = require_args(context, "/nombre <nombre>   ej. /nombre andres")
    await update.effective_message.reply_text(truncate(await name_report(name)))


async def holidays_report(country: str, year: int | None) -> str:
    code = country.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise CommandError(
            "Dime el país con su código de dos letras: CO, MX, AR, CL, ES, PE…"
        )
    year = year or time.gmtime().tm_year
    if not 1990 <= year <= 2100:
        raise CommandError("El año debe estar entre 1990 y 2100.")
    data = await http_json(
        f"https://date.nager.at/api/v3/PublicHolidays/{year}/{code}"
    )
    if not isinstance(data, list) or not data:
        raise CommandError(f"No hay feriados para «{code}» en {year}.")
    lines = [f"📅 Feriados de {code} en {year}", ""]
    for item in data[:15]:
        lines.append(
            f"• {item.get('date')} — {item.get('localName') or item.get('name')}"
        )
    if len(data) > 15:
        lines.append(f"… y {len(data) - 15} más")
    return "\n".join(lines)


async def cmd_feriados(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        raise CommandError("/feriados <código país> [año]   ej. /feriados CO 2027")
    year = None
    if len(args) > 1:
        try:
            year = int(args[1])
        except ValueError:
            raise CommandError("El año debe ser un número, ej. /feriados CO 2027")
    await update.effective_message.reply_text(
        truncate(await holidays_report(args[0], year))
    )


async def brewery_report(query: str) -> str:
    data = await http_json(
        "https://api.openbrewerydb.org/v1/breweries",
        {"by_name": query, "per_page": 3},
    )
    if not isinstance(data, list) or not data:
        raise CommandError(f"No encontré cervecerías que se llamen «{query}».")
    lines = [f"🍺 Cervecerías: {query}", ""]
    for item in data:
        place = ", ".join(
            str(x)
            for x in [item.get("city"), item.get("state_province"), item.get("country")]
            if x
        )
        lines.append(f"• {item.get('name')} ({item.get('brewery_type') or 'tipo ?'})")
        if place:
            lines.append(f"  📍 {place}")
        if item.get("website_url"):
            lines.append(f"  🔗 {item.get('website_url')}")
    return "\n".join(lines)


async def cmd_cerveza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/cerveza <nombre>   ej. /cerveza heineken")
    await update.effective_message.reply_text(truncate(await brewery_report(query)))


async def product_report(code: str) -> tuple[str, str | None]:
    if not re.fullmatch(r"\d{6,14}", code):
        raise CommandError("Mándame un código de barras numérico (6 a 14 dígitos).")
    data = await http_json(
        f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
        {
            "fields": "product_name,brands,quantity,nutriscore_grade,nova_group,"
            "ingredients_text,image_url"
        },
    )
    product = data.get("product") if isinstance(data, dict) else None
    if not product:
        raise CommandError(f"No encontré el producto con código {code}.")
    lines = [
        f"🛒 {product.get('product_name') or 'Producto sin nombre'}",
        f"🏷 {product.get('brands') or '?'}  •  ⚖️ {product.get('quantity') or '?'}",
        f"🥗 Nutri-Score: {str(product.get('nutriscore_grade') or '?').upper()}  •  "
        f"🏭 NOVA: {product.get('nova_group') or '?'}",
    ]
    ingredients = strip_html(product.get("ingredients_text") or "")
    if ingredients:
        lines += ["", truncate(ingredients, 600)]
    image = product.get("image_url") or None
    return "\n".join(lines), image


async def cmd_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = require_args(context, "/producto <código de barras>   ej. /producto 3017624010701")
    text, image = await product_report(code.strip())
    if image:
        await update.effective_message.reply_photo(
            photo=image, caption=truncate(text, 900)
        )
    else:
        await update.effective_message.reply_text(truncate(text))


async def music_report(query: str) -> str:
    data = await http_json(
        "https://musicbrainz.org/ws/2/artist/",
        {"query": f"artist:{query}", "fmt": "json", "limit": 1},
    )
    artists = (data.get("artists") or []) if isinstance(data, dict) else []
    if not artists:
        raise CommandError(f"No encontré al artista «{query}» en MusicBrainz.")
    artist = artists[0]
    span = artist.get("life-span") or {}
    lines = [f"🎵 {artist.get('name')}"]
    if artist.get("type"):
        lines.append(f"🎤 Tipo: {artist.get('type')}")
    if artist.get("country"):
        lines.append(f"🌍 País: {artist.get('country')}")
    if span.get("begin") or span.get("end"):
        lines.append(f"📅 {span.get('begin') or '?'} → {span.get('end') or 'hoy'}")
    if artist.get("disambiguation"):
        lines.append(f"📝 {artist.get('disambiguation')}")
    tags = [t.get("name") for t in (artist.get("tags") or []) if t.get("name")]
    if tags:
        lines.append("🏷 " + ", ".join(str(tag) for tag in tags[:5]))
    lines.append(f"🔗 https://musicbrainz.org/artist/{artist.get('id')}")
    return "\n".join(lines)


async def cmd_musica(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/musica <artista>   ej. /musica bad bunny")
    await update.effective_message.reply_text(truncate(await music_report(query)))


async def cmd_letra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = require_args(
        context, "/letra <artista> - <canción>   ej. /letra shakira - la bicicleta"
    )
    if " - " in raw:
        artist, _, title = raw.partition(" - ")
    elif "|" in raw:
        artist, _, title = raw.partition("|")
    else:
        raise CommandError(
            "Separa el artista y la canción con « - », ej. /letra shakira - la bicicleta"
        )
    artist, title = artist.strip(), title.strip()
    if not artist or not title:
        raise CommandError("Me faltó el artista o la canción.")
    message = update.effective_message
    status = await message.reply_text("🎤 Buscando la letra…")
    try:
        info = await song_metadata(artist, title)
        info.setdefault("artist", artist)
        info["artist"] = info.get("artist") or artist
        info["title"] = info.get("title") or title
        found = await send_lyrics(message, info)
    finally:
        try:
            await status.delete()
        except TelegramError:
            pass
    if not found:
        raise CommandError(
            f"No encontré la letra de «{title}» de {artist}. "
            "Prueba con el nombre exacto, ej. /letra coldplay - yellow"
        )


async def color_report(value: str) -> str:
    hex_value = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{3,8}", hex_value):
        raise CommandError("Mándame un color en hexadecimal, ej. /color 24b1e0")
    data = await http_json("https://www.thecolorapi.com/id", {"hex": hex_value})
    if not isinstance(data, dict) or not data.get("hex"):
        raise CommandError("No pude leer ese color.")
    name = (data.get("name") or {}).get("value") or "?"
    rgb = data.get("rgb") or {}
    hsl = data.get("hsl") or {}
    cmyk = data.get("cmyk") or {}
    return "\n".join(
        [
            f"🎨 {name}",
            f"Hex: {(data.get('hex') or {}).get('value')}",
            f"RGB: {rgb.get('r')}, {rgb.get('g')}, {rgb.get('b')}",
            f"HSL: {hsl.get('h')}°, {hsl.get('s')}%, {hsl.get('l')}%",
            f"CMYK: {cmyk.get('c')}, {cmyk.get('m')}, {cmyk.get('y')}, {cmyk.get('k')}",
            f"🔗 https://www.thecolorapi.com/id?hex={hex_value}",
        ]
    )


async def cmd_color(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    value = require_args(context, "/color <hex>   ej. /color 24b1e0")
    await update.effective_message.reply_text(truncate(await color_report(value)))


async def bored_report() -> str:
    data = await http_json("https://bored-api.appbrewery.com/random")
    if not isinstance(data, dict) or not data.get("activity"):
        raise CommandError("No encontré nada que hacer. Irónico.")
    lines = [
        f"🎲 {data.get('activity')}",
        f"🏷 Tipo: {data.get('type') or '?'}  •  👥 Participantes: "
        f"{data.get('participants') or '?'}",
    ]
    if data.get("price") not in (None, ""):
        lines.append(
            f"💸 Precio: {data.get('price')}/5  •  💪 Esfuerzo: "
            f"{data.get('accessibility')}/5"
        )
    if data.get("duration"):
        lines.append(f"⏱ Duración: {data.get('duration')} {data.get('duration_unit') or ''}".strip())
    return "\n".join(lines) + "\n\n(La idea llega en inglés, pero se entiende.)"


async def cmd_aburrido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await bored_report()))


async def cmd_zorro(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await http_json("https://randomfox.ca/floof/")
    url = data.get("image") if isinstance(data, dict) else None
    if not url:
        raise CommandError("El zorro se escondió. Prueba otra vez.")
    await update.effective_message.reply_photo(photo=url, caption="🦊 Un zorro")


async def fact_report() -> str:
    data = await http_json(
        "https://uselessfacts.jsph.pl/api/v2/facts/random", {"language": "es"}
    )
    text = data.get("text") if isinstance(data, dict) else None
    if not text:
        raise CommandError("No encontré ningún dato. Increíble.")
    lines = ["🤓 Dato curioso", "", text]
    if data.get("source"):
        lines += ["", f"Fuente: {data.get('source')}"]
    return "\n".join(lines) + "\n\n(El dato llega en inglés.)"


async def cmd_dato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await fact_report()))


async def tcg_image(base: object) -> bytes | None:
    """Descarga la imagen de la carta (TCGdex da la URL sin extensión)."""
    if not isinstance(base, str) or not base:
        return None
    candidates = [base] if re.search(r"\.(png|jpe?g|webp)$", base, re.I) else [
        base + "/high.png",
        base + "/low.png",
    ]
    for url in candidates:
        blob = await asyncio.to_thread(_download_media, url)
        if blob:
            return blob
    return None


async def tcg_report(query: str) -> tuple[str, bytes | None]:
    cards = await http_json(
        "https://api.tcgdex.net/v2/es/cards?name="
        f"{quote(query)}&pagination:itemsPerPage=6"
    )
    if not isinstance(cards, list) or not cards:
        raise CommandError(f"No encontré cartas Pokémon de «{query}».")
    ids = [
        str(card.get("id")) for card in cards if isinstance(card, dict) and card.get("id")
    ][:6]
    details = await asyncio.gather(
        *(http_json(f"https://api.tcgdex.net/v2/es/cards/{card_id}") for card_id in ids),
        return_exceptions=True,
    )
    found = [detail for detail in details if isinstance(detail, dict)]
    if not found:
        raise CommandError(f"No pude leer los datos de «{query}» en TCGdex.")
    # Las expansiones nuevas a veces no tienen imagen en español: uso la primera que sí.
    info = next((detail for detail in found if detail.get("image")), found[0])
    lines = [f"🃏 {info.get('name') or query}"]
    if info.get("category"):
        lines.append(
            f"🏷 {info.get('category')}  •  ⭐ {info.get('rarity') or '?'}"
        )
    if info.get("illustrator"):
        lines.append(f"✏️ Ilustra: {info.get('illustrator')}")
    if info.get("hp"):
        lines.append(f"❤️ HP: {info.get('hp')}")
    types = info.get("types") or []
    if types:
        lines.append("⚡ Tipo: " + ", ".join(str(t) for t in types))
    if info.get("description"):
        lines += ["", truncate(str(info.get("description")), 600)]
    image = await tcg_image(info.get("image"))
    return "\n".join(lines), image


async def cmd_tcg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/tcg <carta>   ej. /tcg pikachu")
    report, image = await cached(
        f"tcg:{fold(query)}", 3600, lambda: tcg_report(query)
    )
    message = update.effective_message
    if image:
        try:
            await message.reply_photo(photo=image, caption=truncate(report, 1000))
            return
        except TelegramError:
            pass
    await message.reply_text(truncate(report))


async def f1_report() -> str:
    data = await http_json("https://api.jolpi.ca/ergast/f1/current/last/results.json")
    races = (
        (((data.get("MRData") or {}).get("RaceTable") or {}).get("Races") or [])
        if isinstance(data, dict)
        else []
    )
    if not races:
        raise CommandError("No hay resultados de F1 disponibles ahora.")
    race = races[0]
    results = race.get("Results") or []
    lines = [
        f"🏎 F1 {race.get('season')} · Ronda {race.get('round')}",
        f"🏁 {race.get('raceName')} — {race.get('date')}",
        "",
    ]
    for item in results[:5]:
        driver = item.get("Driver") or {}
        team = (item.get("Constructor") or {}).get("name") or "?"
        name = f"{driver.get('givenName') or ''} {driver.get('familyName') or ''}".strip()
        time_value = (item.get("Time") or {}).get("time") or item.get("status") or ""
        lines.append(f"{item.get('position')}. {name} ({team}) — {time_value}")
    return "\n".join(lines)


async def cmd_f1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(await f1_report()))


async def sea_report(city: str) -> str:
    place = await geocode(city)
    data = await http_json(
        "https://marine-api.open-meteo.com/v1/marine",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "wave_height_max,wave_period_max,wave_direction_dominant",
            "timezone": "auto",
            "forecast_days": 3,
        },
    )
    daily = (data.get("daily") or {}) if isinstance(data, dict) else {}
    dates = daily.get("time") or []
    if not dates:
        raise CommandError(
            "No hay datos marinos para ese lugar (quizá está tierra adentro)."
        )
    heights = daily.get("wave_height_max") or []
    periods = daily.get("wave_period_max") or []
    directions = daily.get("wave_direction_dominant") or []
    lines = [f"🌊 Mar en {place.get('name') or city}", ""]
    for index, day in enumerate(dates[:3]):
        height = heights[index] if index < len(heights) else "?"
        period = periods[index] if index < len(periods) else "?"
        direction = directions[index] if index < len(directions) else "?"
        lines.append(
            f"• {day}: olas de {height} m, periodo {period} s, dirección {direction}°"
        )
    return "\n".join(lines)


async def cmd_mar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city = require_args(context, "/mar <ciudad>   ej. /mar cartagena")
    await update.effective_message.reply_text(truncate(await sea_report(city)))


HTTP_STATUS_TEXT = {
    100: "Continuar",
    200: "OK",
    201: "Creado",
    204: "Sin contenido",
    301: "Movido permanentemente",
    302: "Encontrado",
    304: "No modificado",
    400: "Petición incorrecta",
    401: "No autorizado",
    403: "Prohibido",
    404: "No encontrado",
    405: "Método no permitido",
    409: "Conflicto",
    418: "Soy una tetera",
    422: "Entidad no procesable",
    429: "Demasiadas peticiones",
    500: "Error interno del servidor",
    501: "No implementado",
    502: "Puerta de enlace incorrecta",
    503: "Servicio no disponible",
    504: "Tiempo de espera agotado",
}


async def cmd_http(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = require_args(context, "/http <código>   ej. /http 404")
    try:
        code = int(raw)
    except ValueError:
        raise CommandError("Mándame un código HTTP numérico, ej. 404.")
    if not 100 <= code <= 599:
        raise CommandError("Los códigos HTTP van del 100 al 599.")
    caption = f"🐱 HTTP {code}: {HTTP_STATUS_TEXT.get(code, 'código poco común')}"
    try:
        await update.effective_message.reply_photo(
            photo=f"https://http.cat/{code}.jpg", caption=caption
        )
    except TelegramError:
        await update.effective_message.reply_text(caption)


async def university_report(country: str) -> str:
    data = await http_json(
        "http://universities.hipolabs.com/search", {"country": country}
    )
    items = data if isinstance(data, list) else []
    if not items:
        raise CommandError(
            f"No encontré universidades en «{country}». Prueba con el nombre en "
            "inglés, ej. Colombia."
        )
    lines = [f"🎓 Universidades: {country}", ""]
    for item in items[:8]:
        lines.append(f"• {item.get('name')}")
        domains = item.get("domains") or []
        pages = item.get("web_pages") or []
        if domains:
            lines.append(f"  🔗 {domains[0]}")
        if pages:
            lines.append(f"  🌐 {pages[0]}")
    if len(items) > 8:
        lines.append(f"… y {len(items) - 8} más")
    return "\n".join(lines)


async def cmd_universidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    country = require_args(context, "/universidad <país>   ej. /universidad Colombia")
    await update.effective_message.reply_text(truncate(await university_report(country)))


async def sport_report(query: str) -> str:
    key = KEYS.get("sportsdb") or "123"
    data = await http_json(
        f"https://www.thesportsdb.com/api/v1/json/{key}/searchteams.php",
        {"t": query},
    )
    teams = (data.get("teams") or []) if isinstance(data, dict) else []
    if not teams:
        raise CommandError(f"No encontré el equipo «{query}».")
    team = teams[0]
    lines = [
        f"🏟 {team.get('strTeam')}",
        f"⚽ {team.get('strSport') or '?'}  •  🏆 {team.get('strLeague') or '?'}",
    ]
    if team.get("intFormedYear"):
        lines.append(f"📅 Fundado en {team.get('intFormedYear')}")
    if team.get("strStadium"):
        lines.append(f"🏟 Estadio: {team.get('strStadium')}")
    if team.get("strLocation"):
        lines.append(f"📍 {team.get('strLocation')}")
    description = team.get("strDescriptionES") or team.get("strDescriptionEN") or ""
    if description:
        lines += ["", truncate(str(description), 700)]
    if team.get("strWebsite"):
        lines.append(f"\n🔗 {team.get('strWebsite')}")
    return "\n".join(lines)


async def cmd_deporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/deporte <equipo>   ej. /deporte real madrid")
    await update.effective_message.reply_text(truncate(await sport_report(query)))


async def covid_report(place: str) -> str:
    if fold(place) in ("mundo", "global", "mundial", "world"):
        data = await http_json("https://disease.sh/v3/covid-19/all")
        title = "🌍 Mundo"
    else:
        try:
            data = await http_json(
                f"https://disease.sh/v3/covid-19/countries/{quote(place)}",
                {"strict": "true"},
            )
        except HTTPError as exc:
            if exc.code == 404:
                raise CommandError(f"No encontré datos de «{place}».") from exc
            raise
        country = (data or {}).get("country") if isinstance(data, dict) else None
        title = f"🦠 {country or place}"
    if not isinstance(data, dict):
        raise CommandError("No pude leer los datos de COVID-19.")
    updated = data.get("updated")
    stamp = ""
    if isinstance(updated, (int, float)):
        stamp = datetime.fromtimestamp(updated / 1000, timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    lines = [
        title,
        f"🧪 Casos: {int(data.get('cases') or 0):,}",
        f"💀 Muertes: {int(data.get('deaths') or 0):,}",
        f"💚 Recuperados: {int(data.get('recovered') or 0):,}",
        f"🤒 Activos: {int(data.get('active') or 0):,}",
    ]
    if stamp:
        lines.append(f"🕒 Actualizado: {stamp}")
    lines.append("\n📊 Fuente: disease.sh")
    return "\n".join(lines)


async def cmd_covid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    place = " ".join(context.args).strip() if context.args else "mundo"
    await update.effective_message.reply_text(truncate(await covid_report(place)))


async def app_report(query: str) -> str:
    data = await http_json(
        "https://itunes.apple.com/search",
        {"term": query, "media": "software", "limit": 3, "country": "co"},
    )
    items = (data.get("results") or []) if isinstance(data, dict) else []
    if not items:
        raise CommandError(f"No encontré apps de «{query}» en la App Store.")
    lines = [f"📱 Apps: {query}", ""]
    for item in items:
        lines.append(
            f"• {item.get('trackName')} — {item.get('formattedPrice') or '?'}"
        )
        lines.append(f"  👤 {item.get('sellerName') or '?'}")
        rating = item.get("averageUserRating")
        if rating:
            votes = int(item.get("userRatingCount") or 0)
            lines.append(f"  ⭐ {float(rating):.1f} ({votes:,} votos)")
        if item.get("trackViewUrl"):
            lines.append(f"  🔗 {item.get('trackViewUrl')}")
    return "\n".join(lines)


async def cmd_app(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/app <búsqueda>   ej. /app whatsapp")
    await update.effective_message.reply_text(truncate(await app_report(query)))


# --------------------------------------------------------------------------
# YOSOYYO API: descargas de YouTube, Spotify, redes sociales, imágenes
# generadas, IA, OCR y voz. Clave gratis "yosoyyo_free" (100 al día).
# --------------------------------------------------------------------------
YOYO_TIMEOUT = 120
YOYO_BASES = (
    "https://api-yosoyyo-api-ofc.onrender.com",
    "https://yosoyyo-api-2.onrender.com",
)
YOYO_MAX_UPLOAD = 45 * 1024 * 1024


def _fetch_url(
    url: str,
    accept: str,
    timeout: int,
    method: str = "GET",
    payload: dict | None = None,
    extra_headers: dict | None = None,
) -> tuple[int, bytes, str]:
    """Petición HTTP con timeout propio que no lanza en errores 4xx/5xx."""
    data = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "es,en;q=0.8",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except HTTPError as exc:
        try:
            body = exc.read()
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            return exc.code, body, content_type
        finally:
            exc.close()


def _yoyo_blocking(
    path: str,
    params: dict | None,
    accept: str,
    method: str,
    payload: dict | None,
    timeout: int,
) -> tuple[bytes, str]:
    """Prueba las dos URLs de la API y devuelve el primer 200 con cuerpo."""
    query = dict(params or {})
    query["apiKey"] = KEYS.get("yosoyyo") or "yosoyyo_free"
    problems: list[str] = []
    details: list[str] = []
    for base in YOYO_BASES:
        url = _build_url(base + path, query)
        try:
            status, body, content_type = _fetch_url(url, accept, timeout, method, payload)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            problems.append(f"{base}: {exc}")
            continue
        if status == 200 and body:
            return body, content_type
        try:
            parsed = json.loads(body.decode("utf-8", "replace"))
            if isinstance(parsed, dict):
                details.append(str(parsed.get("message") or parsed.get("error") or ""))
        except ValueError:
            pass
        problems.append(f"{base}: HTTP {status}")
    detail = next((item for item in details if item), "")
    if detail:
        raise CommandError(detail)
    raise CommandError(
        "La API de YOSOYYO no respondió bien. " + (problems[-1] if problems else "sin detalles")
    )


async def yoyo_json(
    path: str,
    params: dict | None = None,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = YOYO_TIMEOUT,
) -> object:
    check_provider("yosoyyo")
    try:
        result = await _yoyo_json_once(path, params, method, payload, timeout)
    except CommandError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        provider_fail("yosoyyo", "YOSOYYO", exc)
        raise
    provider_ok("yosoyyo", "YOSOYYO")
    return result


async def _yoyo_json_once(
    path: str,
    params: dict | None,
    method: str,
    payload: dict | None,
    timeout: int,
) -> object:
    body, _ = await asyncio.to_thread(
        _yoyo_blocking, path, params, "application/json", method, payload, timeout
    )
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise CommandError("La API de YOSOYYO devolvió una respuesta que no entiendo.")
    if isinstance(data, dict) and data.get("status") is False:
        raise CommandError(str(data.get("message") or "La API rechazó la petición."))
    if isinstance(data, dict) and "result" in data:
        return data["result"] if data["result"] is not None else {}
    return data


async def yoyo_bytes(
    path: str, params: dict | None = None, timeout: int = YOYO_TIMEOUT
) -> bytes:
    body, content_type = await asyncio.to_thread(
        _yoyo_blocking, path, params, "image/*,audio/*,video/*,*/*;q=0.5", "GET", None, timeout
    )
    if content_type.lower().startswith(("image/", "audio/", "video/")):
        return body
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("status"):
        raise CommandError("La API devolvió datos en vez del archivo. Prueba otra vez.")
    message = data.get("message") if isinstance(data, dict) else None
    raise CommandError(str(message or "La API no devolvió ningún archivo."))


def _download_media(url: str, cancelled=None, progress=None) -> bytes | None:
    """Baja un archivo para subirlo al chat (None si falla, pesa demasiado o lo cancelan).

    Si se pasa `progress`, se llama con (bajado, total) para pintar la barra.
    """
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
            "Accept": "*/*",
        },
    )
    chunks: list[bytes] = []
    size = 0
    try:
        with urlopen(request, timeout=120) as response:
            # Por trozos para poder cortar si el usuario cancela y para no
            # reservar memoria de más en un archivo enorme.
            total = _size_bytes(response.headers.get("Content-Length"))
            if progress is not None:
                progress(0, total)
            while True:
                if cancelled is not None and cancelled():
                    return None
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > YOYO_MAX_UPLOAD:
                    return None
                chunks.append(chunk)
                if progress is not None:
                    progress(size, total)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    return b"".join(chunks)


async def send_media_safely(
    message,
    kind: str,
    url: str,
    filename: str,
    caption: str = "",
    cancelled=None,
    progress=None,
    meta: dict | None = None,
    cover: bytes | None = None,
    thumb: bytes | None = None,
) -> bool:
    """Manda audio, vídeo o documento; si no puede, deja que Telegram lo baje.

    Con `meta` y `cover` el audio sale etiquetado (título, artista, álbum,
    portada) y el vídeo lleva su miniatura.
    """
    blob = await asyncio.to_thread(_download_media, url, cancelled, progress)
    if cancelled is not None and cancelled():
        raise DownloadCancelled("Descarga cancelada ⛔️")
    if progress is not None:
        # Ya está bajado: vuelve la animación mientras Telegram lo recibe.
        progress(0, 0)
    song = meta or {}
    if blob and kind == "audio" and song and filename.lower().endswith(".mp3"):
        # Solo el MP3 lleva ID3; un .m4a se quedaría roto con la etiqueta delante.
        blob = tag_mp3(blob, song, cover)
    thumb_bytes = thumb or cover
    thumbnail = (
        InputFile(io.BytesIO(thumb_bytes), filename="cover.jpg") if thumb_bytes else None
    )
    if blob:
        try:
            if kind == "audio":
                await message.reply_audio(
                    audio=blob,
                    filename=filename,
                    caption=caption[:1000] or None,
                    title=(str(song.get("title") or "") or caption)[:64] or None,
                    performer=str(song.get("artist") or "")[:64] or None,
                    duration=int(song["duration"]) if song.get("duration") else None,
                    thumbnail=thumbnail,
                )
            elif kind == "video":
                await message.reply_video(
                    video=blob,
                    filename=filename,
                    caption=caption[:1000] or None,
                    supports_streaming=True,
                    thumbnail=thumbnail,
                )
            else:
                await message.reply_document(
                    document=blob, filename=filename, caption=caption[:1000] or None
                )
            return True
        except TelegramError:
            pass
    try:
        if kind == "audio":
            await message.reply_audio(
                audio=url,
                caption=caption[:1000] or None,
                title=str(song.get("title") or "")[:64] or caption[:64] or None,
                performer=str(song.get("artist") or "")[:64] or None,
                duration=int(song["duration"]) if song.get("duration") else None,
            )
        elif kind == "video":
            await message.reply_video(
                video=url, caption=caption[:1000] or None, supports_streaming=True
            )
        else:
            await message.reply_document(document=url, caption=caption[:1000] or None)
        return True
    except TelegramError:
        # Si Telegram rechaza los extras, mando el enlace a secas.
        try:
            if kind == "audio":
                await message.reply_audio(audio=url, caption=caption[:1000] or None)
            elif kind == "video":
                await message.reply_video(video=url, caption=caption[:1000] or None)
            else:
                await message.reply_document(document=url, caption=caption[:1000] or None)
            return True
        except TelegramError:
            return False


# --------------------------------------------------------------------------
# Cola de descargas: varias a la vez tumbaban el hosting por memoria
# --------------------------------------------------------------------------
DOWNLOAD_LIMIT = 2  # descargas en marcha a la vez en todo el bot
DOWNLOAD_USER_PENDING = 2  # descargas pendientes por usuario (1 activa + 1 en cola)
DOWNLOAD_PANEL_SECONDS = 2  # cada cuánto refresco la animación de carga
DOWNLOAD_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_DL_ACTIVE = 0
_DL_TICK = 0  # contador para mover la animación en cada refresco
_DL_QUEUE: list["DownloadJob"] = []
_DL_JOBS: dict[str, "DownloadJob"] = {}


class DownloadCancelled(CommandError):
    """El usuario pulsó el botón de cancelar."""


class DownloadJob:
    """Una descarga con su hueco en la cola y su mensaje de progreso."""

    def __init__(self, chat_id: int, user_id: int, user_name: str, label: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.chat_id = chat_id
        self.user_id = user_id
        self.user_name = user_name
        self.label = label
        self.state = "queued"
        self.cancelled = False
        self.stage = "Preparando la descarga"
        self.created = time.time()
        self.started = 0.0
        self.downloaded = 0
        self.total = 0
        self.download_started = 0.0

    def begin(self) -> None:
        """Vuelve a la animación de carga con la barra a cero."""
        self.downloaded = 0
        self.total = 0
        self.download_started = time.time()

    def track(self, done: int, total: int) -> None:
        """Lo llama la descarga por trozos con lo que lleva y lo que pesa."""
        self.downloaded = max(0, int(done))
        self.total = max(0, int(total))
        if not self.download_started:
            self.download_started = time.time()


async def _dl_acquire(job: DownloadJob) -> bool:
    """Espera un hueco: máximo global y una descarga activa por usuario."""
    global _DL_ACTIVE
    while True:
        if job.cancelled:
            return False
        free = _DL_ACTIVE < DOWNLOAD_LIMIT and not any(
            other.state == "running" and other.user_id == job.user_id
            for other in _DL_JOBS.values()
        )
        if job in _DL_QUEUE:
            if free and _DL_QUEUE[0] is job:
                _DL_QUEUE.remove(job)
                break
        elif free:
            break
        else:
            _DL_QUEUE.append(job)
        await asyncio.sleep(0.7)
    _DL_ACTIVE += 1
    job.state = "running"
    job.started = time.time()
    job.begin()
    return True


def _dl_release(job: DownloadJob) -> None:
    global _DL_ACTIVE
    if job.state == "running":
        _DL_ACTIVE = max(0, _DL_ACTIVE - 1)
    if job in _DL_QUEUE:
        _DL_QUEUE.remove(job)
    if job.state in ("queued", "running"):
        job.state = "cancelled" if job.cancelled else "done"


def _dl_check(job: DownloadJob) -> None:
    if job.cancelled:
        raise DownloadCancelled("Descarga cancelada ⛔️")


def _dl_stopped(job: DownloadJob):
    """Callable para que la descarga por trozos se corte al cancelar."""
    return lambda: job.cancelled


def _dl_user_pending(user_id: int) -> list[DownloadJob]:
    return [
        job
        for job in _DL_JOBS.values()
        if job.user_id == user_id and job.state in ("queued", "running")
    ]


def _dl_spinner() -> str:
    """Un fotograma de la animación de carga, distinto en cada refresco."""
    global _DL_TICK
    _DL_TICK += 1
    return DOWNLOAD_SPINNER[_DL_TICK % len(DOWNLOAD_SPINNER)]


def _dl_panel_text(job: DownloadJob) -> str:
    """Solo la animación de carga: sin datos, sin barra y sin botones."""
    return _dl_spinner()


async def _dl_refresh(job: DownloadJob, panel) -> None:
    while job.state in ("queued", "running"):
        try:
            await panel.edit_text(_dl_panel_text(job))
        except RetryAfter as exc:
            # Telegram pide esperar: respeto su tiempo para no inundar el chat.
            await asyncio.sleep(float(getattr(exc, "retry_after", 5) or 5))
            continue
        except TelegramError:
            pass
        await asyncio.sleep(DOWNLOAD_PANEL_SECONDS)


@asynccontextmanager
async def download_task(update: Update, label: str):
    """Espera turno para descargar mostrando solo la animación de carga."""
    message = update.effective_message
    user = getattr(update, "effective_user", None)
    user_id = int(getattr(user, "id", 0) or 0)
    pending = _dl_user_pending(user_id)
    if len(pending) >= DOWNLOAD_USER_PENDING:
        raise CommandError(
            f"Ya tienes {len(pending)} descargas pendientes. Espera a que terminen "
            "antes de pedir otra."
        )
    job = DownloadJob(
        int(getattr(message, "chat_id", 0) or 0),
        user_id,
        getattr(user, "first_name", "") or "",
        label,
    )
    _DL_JOBS[job.id] = job
    panel = await message.reply_text(_dl_panel_text(job))
    refresh = asyncio.create_task(_dl_refresh(job, panel))
    try:
        if not await _dl_acquire(job):
            raise DownloadCancelled("Descarga cancelada ⛔️")
        yield job
    finally:
        _dl_release(job)
        refresh.cancel()
        _DL_JOBS.pop(job.id, None)
        try:
            if job.cancelled:
                await panel.edit_text("⛔️ Descarga cancelada. No se envió ningún archivo.")
            else:
                await panel.delete()
        except TelegramError:
            pass


# --------------------------------------------------------------------------
# Metadatos musicales y letras sincronizadas
# Varios proveedores y scrapers que se combinan entre sí: iTunes, Deezer,
# MusicBrainz + Cover Art Archive, la ficha de la página de YouTube, LRCLIB
# (letra con tiempos) y lyrics.ovh (letra normal) de respaldo.
# --------------------------------------------------------------------------
META_TIMEOUT = 18
COVER_MAX_BYTES = 3 * 1024 * 1024
MUSIC_TITLE_NOISE = re.compile(
    r"[\(\[]\s*(?:official|oficial|video|videoclip|audio|lyric|letra|hd|hq|4k|8k|"
    r"remaster(?:ed)?|visualizer|mv|m/v|en vivo|live|performance)[^\)\]]*[\)\]]",
    re.I,
)
LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_COVER_CACHE: dict[str, bytes] = {}


def split_song_title(raw: str) -> tuple[str, str]:
    """«Artista - Título (Official Video)» → («Artista», «Título»)."""
    text = MUSIC_TITLE_NOISE.sub(" ", raw or "")
    text = re.sub(r"\s+", " ", text).strip(" -–—|•")
    for separator in (" - ", " – ", " — ", " | ", " • ", " / "):
        if separator in text:
            artist, _, title = text.partition(separator)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
    return "", text


def _similar(left: object, right: object) -> float:
    """Cuánto se parecen dos nombres, de 0 a 1 (sin tildes ni mayúsculas)."""
    a, b = fold(str(left or "")), fold(str(right or ""))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.8
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _as_int(value: object) -> int:
    try:
        return int(float(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0


def _meta_json(url: str, accept: str = "application/json", headers: dict | None = None) -> object:
    """GET que devuelve JSON ya parseado (lanza si la respuesta no sirve)."""
    status, body, _ = _fetch_url(url, accept, META_TIMEOUT, extra_headers=headers)
    if status != 200 or not body:
        raise CommandError(f"HTTP {status}")
    return json.loads(body.decode("utf-8", "replace"))


async def _meta_call(name: str, label: str, func, *args) -> list[dict]:
    """Llama a un proveedor en un hilo y apunta su salud para /salud."""
    check_provider(name)
    try:
        items = await asyncio.to_thread(func, *args)
    except (CommandError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        provider_fail(name, label, exc)
        return []
    provider_ok(name, label)
    return items if isinstance(items, list) else []


# ---- 1) iTunes Search API (sin clave) ------------------------------------
def _itunes_blocking(term: str) -> list[dict]:
    data = _meta_json(
        _build_url(
            "https://itunes.apple.com/search",
            {"term": term, "entity": "song", "media": "music", "limit": 8},
        )
    )
    found: list[dict] = []
    for item in (data.get("results") if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict) or not item.get("trackName"):
            continue
        artwork = str(item.get("artworkUrl100") or "")
        artwork = re.sub(r"/\d+x\d+bb\.", "/1000x1000bb.", artwork)
        found.append(
            {
                "title": str(item.get("trackName") or ""),
                "artist": str(item.get("artistName") or ""),
                "album": str(item.get("collectionName") or ""),
                "year": str(item.get("releaseDate") or "")[:4],
                "genre": str(item.get("primaryGenreName") or ""),
                "track": _as_int(item.get("trackNumber")),
                "total_tracks": _as_int(item.get("trackCount")),
                "duration": _as_int(item.get("trackTimeMillis")) // 1000,
                "cover_url": artwork if artwork.startswith("http") else "",
                "source": "iTunes",
            }
        )
    return found


# ---- 2) Deezer (sin clave) -----------------------------------------------
def _deezer_blocking(artist: str, title: str) -> list[dict]:
    query = f'track:"{title}"'
    if artist:
        query += f' artist:"{artist}"'
    data = _meta_json(
        _build_url("https://api.deezer.com/search", {"q": query, "limit": 8})
    )
    found: list[dict] = []
    for item in (data.get("data") if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        who = item.get("artist") if isinstance(item.get("artist"), dict) else {}
        found.append(
            {
                "title": str(item.get("title") or ""),
                "artist": str(who.get("name") or ""),
                "album": str(album.get("title") or ""),
                "year": "",
                "genre": "",
                "track": 0,
                "total_tracks": 0,
                "duration": _as_int(item.get("duration")),
                "cover_url": str(album.get("cover_xl") or album.get("cover_big") or ""),
                "album_id": str(album.get("id") or ""),
                "source": "Deezer",
            }
        )
    return found


def _deezer_album_blocking(album_id: str) -> dict:
    data = _meta_json(f"https://api.deezer.com/album/{album_id}")
    if not isinstance(data, dict):
        return {}
    genres = data.get("genres") if isinstance(data.get("genres"), dict) else {}
    names = [
        str(entry.get("name"))
        for entry in (genres.get("data") or [])
        if isinstance(entry, dict) and entry.get("name")
    ]
    return {
        "year": str(data.get("release_date") or "")[:4],
        "genre": ", ".join(names[:2]),
        "total_tracks": _as_int(data.get("nb_tracks")),
        "cover_url": str(data.get("cover_xl") or ""),
        "label": str(data.get("label") or ""),
        "album": str(data.get("title") or ""),
    }


# ---- 3) MusicBrainz + Cover Art Archive ----------------------------------
MUSICBRAINZ_HEADERS = {
    "User-Agent": f"BotPython/{VERSION} (https://github.com/Andresv27728/Bot.python)"
}


def _musicbrainz_blocking(artist: str, title: str) -> list[dict]:
    query = f'recording:"{title}"'
    if artist:
        query += f' AND artist:"{artist}"'
    data = _meta_json(
        _build_url(
            "https://musicbrainz.org/ws/2/recording",
            {"query": query, "fmt": "json", "limit": 8},
        ),
        headers=MUSICBRAINZ_HEADERS,
    )
    found: list[dict] = []
    for item in (data.get("recordings") if isinstance(data, dict) else []) or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        credits = item.get("artist-credit") or []
        who = ""
        if credits and isinstance(credits[0], dict):
            who = str((credits[0].get("artist") or {}).get("name") or "")
        releases = [entry for entry in (item.get("releases") or []) if isinstance(entry, dict)]
        release = releases[0] if releases else {}
        groups = release.get("release-group") if isinstance(release.get("release-group"), dict) else {}
        found.append(
            {
                "title": str(item.get("title") or ""),
                "artist": who,
                "album": str(release.get("title") or ""),
                "year": str(release.get("date") or "")[:4],
                "genre": "",
                "track": 0,
                "total_tracks": 0,
                "duration": _as_int(item.get("length")) // 1000,
                "cover_url": "",
                "release_id": str(release.get("id") or ""),
                "kind": str(groups.get("primary-type") or ""),
                "source": "MusicBrainz",
            }
        )
    return found


# ---- Portadas ------------------------------------------------------------
def _cover_blocking(url: str) -> bytes | None:
    try:
        status, body, content_type = _fetch_url(
            url, "image/jpeg,image/png,image/*;q=0.8", META_TIMEOUT
        )
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if status != 200 or not body or len(body) > COVER_MAX_BYTES:
        return None
    looks_image = body[:3] == b"\xff\xd8\xff" or body[:8].startswith(b"\x89PNG")
    if not looks_image and not content_type.lower().startswith("image/"):
        return None
    return body


async def song_cover(info: dict) -> bytes | None:
    """Baja la portada: la del proveedor que la tenga o la de Cover Art Archive."""
    urls: list[str] = []
    if info.get("cover_url"):
        urls.append(str(info["cover_url"]))
    if info.get("release_id"):
        urls.append(f"https://coverartarchive.org/release/{info['release_id']}/front-500")
    if not urls:
        return None
    key = fold(f"{info.get('artist')}|{info.get('album') or info.get('title')}")
    if key in _COVER_CACHE:
        return _COVER_CACHE[key]
    for url in urls:
        cover = await asyncio.to_thread(_cover_blocking, url)
        if cover:
            if len(_COVER_CACHE) > 120:
                _COVER_CACHE.clear()
            _COVER_CACHE[key] = cover
            return cover
    return None


def _thumbnail(cover: bytes | None) -> bytes | None:
    """Miniatura cuadrada de 320 px en JPEG (lo que pide Telegram)."""
    if not cover:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(cover)) as raw:
            image = raw.convert("RGB")
        image.thumbnail((320, 320))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
        data = out.getvalue()
        return data if len(data) < 200 * 1024 else None
    except Exception:
        return None


# ---- 4) Ficha de YouTube (scraper + oEmbed de respaldo) ------------------
def _json_after(page: str, marker: str) -> dict:
    """Saca el JSON que hay justo después de un marcador del HTML."""
    index = page.find(marker)
    if index < 0:
        return {}
    start = page.find("{", index)
    if start < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(page[start:])
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dig(node: object, key: str, depth: int = 0) -> object:
    """Busca una clave a cualquier profundidad dentro del JSON de YouTube."""
    if depth > 10:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _dig(value, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node[:40]:
            found = _dig(value, key, depth + 1)
            if found is not None:
                return found
    return None


def _text_of(node: object) -> str:
    """Texto de un nodo de YouTube: simpleText, runs o cadena."""
    if isinstance(node, dict):
        if node.get("simpleText"):
            return str(node["simpleText"]).strip()
        runs = node.get("runs")
        if isinstance(runs, list):
            return "".join(
                str(item.get("text") or "") for item in runs if isinstance(item, dict)
            ).strip()
    if isinstance(node, str):
        return node.strip()
    return ""


SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _spanish_date(text: str) -> str:
    """«30 may 2011» → «2011-05-30»."""
    match = re.search(r"(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóú]{3,})\.?\s+(?:de\s+)?(\d{4})", text or "")
    if not match:
        return ""
    month = SPANISH_MONTHS.get(fold(match.group(2))[:3])
    if not month:
        return ""
    return f"{match.group(3)}-{month:02d}-{int(match.group(1)):02d}"


def _stamp(value: object) -> str:
    """Convierte un timestamp (segundos o milisegundos) en fecha legible."""
    number = _as_int(value)
    if not number:
        return ""
    if number > 10 ** 11:
        number //= 1000
    try:
        return datetime.fromtimestamp(number, timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _youtube_details_blocking(video_id: str) -> list[dict]:
    """Scraper de la página del vídeo: player, datos iniciales y microformat."""
    url = f"https://www.youtube.com/watch?v={video_id}&hl=es&gl=ES"
    status, body, _ = _fetch_url(
        url,
        "text/html,application/xhtml+xml",
        META_TIMEOUT,
        extra_headers={"Accept-Language": "es-ES,es;q=0.9"},
    )
    page = body.decode("utf-8", "replace") if body else ""
    if status != 200 or not page:
        raise CommandError(f"HTTP {status}")
    player = _json_after(page, "ytInitialPlayerResponse")
    data = _json_after(page, "ytInitialData")
    video = player.get("videoDetails") if isinstance(player.get("videoDetails"), dict) else {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    primary = _dig(data, "videoPrimaryInfoRenderer") or {}
    secondary = _dig(data, "videoSecondaryInfoRenderer") or {}
    owner = ((secondary.get("owner") or {}).get("videoOwnerRenderer") or {})
    views_block = ((primary.get("viewCount") or {}).get("videoViewCountRenderer") or {})
    views = _as_int(video.get("viewCount")) or _as_int(views_block.get("originalViewCount"))
    if not views:
        digits = re.sub(r"[^0-9]", "", _text_of(views_block.get("viewCount")))
        views = _as_int(digits)
    thumbs = ((video.get("thumbnail") or {}).get("thumbnails") or [])
    best_thumb = ""
    if thumbs and isinstance(thumbs[-1], dict):
        best_thumb = str(thumbs[-1].get("url") or "")
    result = {
        "title": str(video.get("title") or _text_of(primary.get("title")) or ""),
        "channel": str(
            video.get("author")
            or micro.get("ownerChannelName")
            or _text_of(owner.get("title"))
            or ""
        ),
        "duration": _as_int(video.get("lengthSeconds")) or _as_int(micro.get("lengthSeconds")),
        "views": views,
        "published": str(micro.get("publishDate") or micro.get("uploadDate") or "")[:10]
        or _spanish_date(_text_of(primary.get("dateText"))),
        "category": str(micro.get("category") or ""),
        "description": str(video.get("shortDescription") or "")[:400],
        "thumbnail": best_thumb
        or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "video_id": video_id,
        "source": "YouTube",
    }
    if not result["title"]:
        raise CommandError("YouTube no dejó leer la ficha de ese vídeo")
    return [result]


INVIDIOUS_HOSTS = (
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
)
PIPED_HOSTS = (
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://api.piped.private.coffee",
)


def _invidious_blocking(video_id: str) -> list[dict]:
    """Instancias públicas de Invidious: ficha completa sin tocar YouTube."""
    for host in INVIDIOUS_HOSTS:
        try:
            status, body, _ = _fetch_url(
                f"{host}/api/v1/videos/{video_id}", "application/json", 8
            )
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        if status != 200 or not body:
            continue
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(data, dict) or not data.get("title"):
            continue
        thumbs = data.get("videoThumbnails") or []
        thumbnail = ""
        if thumbs:
            best = max(
                (item for item in thumbs if isinstance(item, dict)),
                key=lambda item: _as_int(item.get("width")),
                default={},
            )
            thumbnail = str(best.get("url") or "")
        return [
            {
                "title": str(data.get("title") or ""),
                "channel": str(data.get("author") or ""),
                "duration": _as_int(data.get("lengthSeconds")),
                "views": _as_int(data.get("viewCount")),
                "published": _stamp(data.get("published")),
                "category": str(data.get("genre") or ""),
                "description": str(data.get("description") or "")[:400],
                "thumbnail": thumbnail,
                "video_id": video_id,
                "source": "Invidious",
            }
        ]
    raise CommandError("Ninguna instancia de Invidious respondió")


def _piped_blocking(video_id: str) -> list[dict]:
    """API de Piped (otra puerta a los datos del vídeo)."""
    for host in PIPED_HOSTS:
        try:
            status, body, _ = _fetch_url(
                f"{host}/streams/{video_id}", "application/json", 10
            )
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        if status != 200 or not body:
            continue
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(data, dict) or not data.get("title"):
            continue
        return [
            {
                "title": str(data.get("title") or ""),
                "channel": str(data.get("uploader") or data.get("uploaderName") or ""),
                "duration": _as_int(data.get("duration")),
                "views": _as_int(data.get("views")),
                "published": _stamp(data.get("uploaded") or data.get("uploadDate")),
                "category": str(data.get("category") or ""),
                "description": str(data.get("description") or "")[:400],
                "thumbnail": str(data.get("thumbnailUrl") or ""),
                "video_id": video_id,
                "source": "Piped",
            }
        ]
    raise CommandError("Ninguna instancia de Piped respondió")


def _oembed_blocking(video_id: str) -> list[dict]:
    """oEmbed de YouTube y noembed: siempre dan título, canal y miniatura."""
    watch = f"https://www.youtube.com/watch?v={video_id}"
    urls = (
        _build_url("https://www.youtube.com/oembed", {"url": watch, "format": "json"}),
        _build_url("https://noembed.com/embed", {"url": watch}),
    )
    for url in urls:
        try:
            status, body, _ = _fetch_url(url, "application/json", 12)
        except (HTTPError, URLError, TimeoutError, OSError):
            continue
        if status != 200 or not body:
            continue
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(data, dict) or not data.get("title"):
            continue
        return [
            {
                "title": str(data.get("title") or ""),
                "channel": str(data.get("author_name") or ""),
                "thumbnail": str(data.get("thumbnail_url") or ""),
                "video_id": video_id,
                "source": "oEmbed",
            }
        ]
    raise CommandError("oEmbed no respondió")


async def youtube_details(video_id: str) -> dict:
    """Ficha del vídeo juntando scraper, Invidious, Piped y oEmbed."""
    groups = await asyncio.gather(
        _meta_call("youtube", "YouTube (ficha)", _youtube_details_blocking, video_id),
        _meta_call("invidious", "Invidious", _invidious_blocking, video_id),
        _meta_call("piped", "Piped", _piped_blocking, video_id),
        _meta_call("oembed", "oEmbed", _oembed_blocking, video_id),
        return_exceptions=True,
    )
    info: dict = {}
    sources: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if value and not info.get(key):
                    info[key] = value
            if item.get("source"):
                sources.append(str(item["source"]))
    if not info.get("title"):
        return {}
    info.setdefault("thumbnail", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
    info["video_id"] = video_id
    info["providers"] = " · ".join(dict.fromkeys(sources))
    return info


async def video_cover(details: dict) -> bytes | None:
    """Miniatura del vídeo, recortada al tamaño que acepta Telegram."""
    candidates = [
        str(details.get("thumbnail") or ""),
        f"https://i.ytimg.com/vi/{details.get('video_id')}/hqdefault.jpg",
    ]
    for url in candidates:
        if not url.startswith("http"):
            continue
        cover = await asyncio.to_thread(_cover_blocking, url)
        if cover:
            return _thumbnail(cover)
    return None


# ---- Combinar los proveedores -------------------------------------------
async def song_metadata(
    artist: str, title: str, album: str = "", duration: int = 0, job=None
) -> dict:
    """Pregunta a iTunes, Deezer y MusicBrainz y junta lo mejor de cada uno."""
    title = (title or "").strip()
    artist = (artist or "").strip()
    term = f"{artist} {title}".strip() or title
    if not term:
        return {}
    if job is not None:
        job.stage = "Buscando portada, álbum y artista"
    groups = await asyncio.gather(
        _meta_call("itunes", "iTunes", _itunes_blocking, term),
        _meta_call("deezer", "Deezer", _deezer_blocking, artist, title),
        _meta_call("musicbrainz", "MusicBrainz", _musicbrainz_blocking, artist, title),
        return_exceptions=True,
    )
    candidates: list[dict] = []
    for group in groups:
        if isinstance(group, list):
            candidates.extend(item for item in group if isinstance(item, dict))
    best: dict = {}
    best_score = 0.0
    for item in candidates:
        score = 2 * _similar(item.get("title"), title) + _similar(item.get("artist"), artist)
        if duration and item.get("duration"):
            score += 0.5 if abs(_as_int(item["duration"]) - duration) <= 8 else -0.3
        if score > best_score:
            best, best_score = item, score
    if not best:
        return {"title": title, "artist": artist, "album": album, "duration": duration}
    info = {
        "title": str(best.get("title") or title),
        "artist": str(best.get("artist") or artist),
        "album": str(best.get("album") or album),
        "year": str(best.get("year") or ""),
        "genre": str(best.get("genre") or ""),
        "track": _as_int(best.get("track")),
        "total_tracks": _as_int(best.get("total_tracks")),
        "duration": _as_int(best.get("duration")) or duration,
        "cover_url": str(best.get("cover_url") or ""),
        "release_id": str(best.get("release_id") or ""),
        "album_id": str(best.get("album_id") or ""),
    }
    sources = {str(best.get("source") or "")} - {""}
    # Rellenar los huecos con el resto de proveedores.
    for item in candidates:
        if _similar(item.get("title"), info["title"]) < 0.6:
            continue
        for key in (
            "album", "year", "genre", "track", "total_tracks",
            "cover_url", "release_id", "album_id", "duration",
        ):
            if not info.get(key) and item.get(key):
                info[key] = item[key]
                if item.get("source"):
                    sources.add(str(item["source"]))
    if info["album_id"] and (not info["year"] or not info["genre"] or not info["total_tracks"]):
        extra = await _meta_call(
            "deezer", "Deezer", _deezer_album_blocking, str(info["album_id"])
        )
        if extra and isinstance(extra[0], dict):
            for key, value in extra[0].items():
                if value and not info.get(key):
                    info[key] = value
            sources.add("Deezer (álbum)")
    if job is not None:
        job.stage = "Bajando la portada"
    info["cover"] = await song_cover(info)
    info["providers"] = " · ".join(sorted(sources)) or "—"
    return info


# ---- Etiquetas ID3 (título, artista, álbum, año, pista y portada) --------
def _syncsafe(value: int) -> bytes:
    return bytes(
        [(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]
    )


def _id3_frame(frame_id: str, payload: bytes) -> bytes:
    return frame_id.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _id3_text(frame_id: str, value: str) -> bytes:
    return _id3_frame(frame_id, b"\x01" + value.encode("utf-16"))


def tag_mp3(blob: bytes, info: dict, cover: bytes | None = None) -> bytes:
    """Escribe una etiqueta ID3v2.3 en el MP3 con todos los datos y la portada."""
    if not blob or blob.startswith(b"ID3") or not info:
        return blob
    frames: list[bytes] = []
    title = str(info.get("title") or "").strip()
    artist = str(info.get("artist") or "").strip()
    album = str(info.get("album") or "").strip()
    if title:
        frames.append(_id3_text("TIT2", title))
    if artist:
        frames.append(_id3_text("TPE1", artist))
    if album:
        frames.append(_id3_text("TALB", album))
    if info.get("year"):
        frames.append(_id3_text("TYER", str(info["year"])[:4]))
    if info.get("genre"):
        frames.append(_id3_text("TCON", str(info["genre"])))
    if info.get("track"):
        total = _as_int(info.get("total_tracks"))
        frames.append(
            _id3_text("TRCK", f"{info['track']}/{total}" if total else str(info["track"]))
        )
    if cover:
        mime = b"image/png" if cover[:8].startswith(b"\x89PNG") else b"image/jpeg"
        frames.append(
            _id3_frame("APIC", b"\x00" + mime + b"\x00" + b"\x03" + b"\x00" + cover)
        )
    if not frames:
        return blob
    body = b"".join(frames)
    tag = b"ID3\x03\x00\x00" + _syncsafe(len(body)) + body
    return tag + blob


def song_filename(info: dict, fallback: str = "audio.mp3") -> str:
    title = str(info.get("title") or "").strip()
    if not title:
        return fallback
    name = f"{info.get('artist') or ''} - {title}".strip(" -")
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', " ", name)
    name = re.sub(r"\s+", " ", name).strip()[:90] or title[:90]
    extension = fallback.rsplit(".", 1)[-1] if "." in fallback else "mp3"
    return f"{name}.{extension}"


def song_caption(info: dict, extra: str = "") -> str:
    title = str(info.get("title") or "Audio")
    artist = str(info.get("artist") or "")
    lines = [f"🎵 {title}" + (f" — {artist}" if artist else "")]
    album, year = str(info.get("album") or ""), str(info.get("year") or "")
    if album:
        lines.append(f"💿 {album}" + (f" ({year})" if year else ""))
    elif year:
        lines.append(f"📅 {year}")
    track, total = _as_int(info.get("track")), _as_int(info.get("total_tracks"))
    details = []
    if info.get("genre"):
        details.append(str(info["genre"]))
    if track:
        details.append(f"pista {track}" + (f" de {total}" if total else ""))
    if info.get("duration"):
        details.append(pretty_duration(_as_int(info["duration"])))
    if details:
        lines.append("🎼 " + " · ".join(details))
    if extra:
        lines.append(extra)
    if info.get("cover"):
        lines.append("🖼 Portada incluida en la carátula y en las etiquetas del MP3")
    return "\n".join(lines)


def video_caption(details: dict, resolution: int = 0) -> str:
    title = str(details.get("title") or "Vídeo de YouTube")
    lines = [f"🎬 {title}"]
    if details.get("channel"):
        lines.append(f"👤 {details['channel']}")
    bits = []
    if resolution:
        bits.append(f"{resolution}p")
    if details.get("duration"):
        bits.append(pretty_duration(_as_int(details["duration"])))
    if details.get("views"):
        bits.append(f"{int(details['views']):,} visitas".replace(",", "."))
    if details.get("published"):
        bits.append(f"publicado el {details['published']}")
    if details.get("category"):
        bits.append(str(details["category"]))
    if bits:
        lines.append("📊 " + " · ".join(bits))
    return "\n".join(lines)


# ---- Letras: LRCLIB (con tiempos) y lyrics.ovh de respaldo --------------
def _lrclib_blocking(artist: str, title: str, album: str = "", duration: int = 0) -> list[dict]:
    params: dict = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    try:
        data = _meta_json(_build_url("https://lrclib.net/api/get", params))
        if isinstance(data, dict) and (data.get("syncedLyrics") or data.get("plainLyrics")):
            return [data]
    except (CommandError, HTTPError, URLError, TimeoutError, OSError, ValueError):
        pass
    search: dict = {"q": f"{artist} {title}".strip() or title}
    if artist:
        search["artist_name"] = artist
    search["track_name"] = title
    data = _meta_json(_build_url("https://lrclib.net/api/search", search))
    return [item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)]


def _lyrics_ovh_blocking(artist: str, title: str) -> list[dict]:
    data = _meta_json(
        f"https://api.lyrics.ovh/v1/{quote(artist, safe='')}/{quote(title, safe='')}"
    )
    text = data.get("lyrics") if isinstance(data, dict) else ""
    if not text:
        raise CommandError("La letra no está en lyrics.ovh")
    return [{"plainLyrics": str(text), "syncedLyrics": "", "source": "lyrics.ovh"}]


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Convierte un LRC en [(segundos, línea)] ordenado, respetando el offset."""
    entries: list[tuple[float, str]] = []
    offset = 0.0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("[offset:"):
            numbers = re.findall(r"-?\d+", line)
            offset = _as_int(numbers[0]) / 1000 if numbers else 0.0
            continue
        stamps = LRC_TIME.findall(line)
        if not stamps:
            continue
        content = LRC_TIME.sub("", line).strip()
        if not content:
            continue
        for minutes, seconds, fraction in stamps:
            ms = _as_int(fraction.ljust(3, "0")) if fraction else 0
            entries.append(
                (max(0.0, int(minutes) * 60 + int(seconds) + ms / 1000 + offset), content)
            )
    entries.sort(key=lambda pair: pair[0])
    return entries


async def synced_lyrics(artist: str, title: str, album: str = "", duration: int = 0) -> dict:
    """Letra con tiempos (LRCLIB) o, si no hay, letra normal (lyrics.ovh)."""
    result = {"synced": "", "plain": "", "lines": [], "source": ""}
    items = await _meta_call("lrclib", "LRCLIB (letras)", _lrclib_blocking, artist, title, album, duration)
    if not items:
        items = []
    best = None
    for item in items:
        if item.get("syncedLyrics"):
            if best is None or (
                duration and item.get("duration")
                and abs(_as_int(item["duration"]) - duration) < abs(_as_int(best.get("duration")) - duration)
            ):
                best = item
    if best is None:
        best = next((item for item in items if item.get("plainLyrics")), None)
    if best:
        result["synced"] = str(best.get("syncedLyrics") or "")
        result["plain"] = str(best.get("plainLyrics") or "")
        result["lines"] = parse_lrc(result["synced"]) if result["synced"] else []
        result["source"] = "LRCLIB"
    if not result["synced"] and not result["plain"]:
        extra = await _meta_call(
            "lyricsovh", "lyrics.ovh", _lyrics_ovh_blocking, artist, title
        )
        if extra and isinstance(extra[0], dict):
            result["plain"] = str(extra[0].get("plainLyrics") or "")
            result["source"] = "lyrics.ovh"
    return result


async def send_lyrics(message, info: dict) -> bool:
    """Manda la letra como texto normal: sin archivos .lrc y sin decir de dónde sale."""
    artist = str(info.get("artist") or "")
    title = str(info.get("title") or "")
    if not title:
        return False
    try:
        lyrics = await synced_lyrics(
            artist, title, str(info.get("album") or ""), _as_int(info.get("duration"))
        )
    except CommandError:
        return False
    body = str(lyrics.get("plain") or "").strip()
    if not body and lyrics.get("lines"):
        body = "\n".join(text for _, text in lyrics["lines"]).strip()
    if not body and lyrics.get("synced"):
        body = "\n".join(
            re.sub(r"\[[^\]]*\]", "", line).strip()
            for line in str(lyrics["synced"]).splitlines()
            if line.strip()
        ).strip()
    if not body:
        return False
    header = f"🎤 {title}" + (f" — {artist}" if artist else "")
    try:
        await message.reply_text(truncate(f"{header}\n\n{body}"))
    except TelegramError:
        return False
    return True


async def send_song(
    message,
    url: str,
    artist: str,
    title: str,
    album: str = "",
    duration: int = 0,
    filename: str = "audio.mp3",
    job=None,
    cancelled=None,
    progress=None,
    extra: str = "",
) -> bool:
    """Baja un audio y le pone portada y todos sus datos en las etiquetas internas."""
    info = await song_metadata(artist, title, album, duration, job=job)
    if job is not None:
        job.stage = "Etiquetando el audio con la portada y los datos"
    cover = info.get("cover") if isinstance(info.get("cover"), bytes) else None
    caption = song_caption(info, extra)
    info.pop("cover", None)
    ok = await send_media_safely(
        message,
        "audio",
        url,
        song_filename(info, filename),
        caption,
        cancelled,
        progress,
        info,
        cover,
        _thumbnail(cover),
    )
    return ok


def find_youtube_url(text: str) -> str | None:
    match = re.search(
        r"((?:https?://)?(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/[^\s]+)", text, re.I
    )
    if not match:
        return None
    url = match.group(1)
    return url if url.lower().startswith("http") else "https://" + url


async def youtube_link(target: str) -> str:
    """Devuelve el enlace del vídeo: el que mandó el usuario o el primero de /api/youtube."""
    link = find_youtube_url(target)
    if link:
        return link
    data = await yoyo_json("/api/youtube", {"q": target})
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    for item in items:
        candidate = item.get("videoUrl") or item.get("url")
        if candidate:
            return str(candidate)
    raise CommandError(f"No encontré ningún vídeo para «{target}».")


def _size_bytes(value: object) -> int:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else 0


def _resolution(value: object) -> int:
    match = re.search(r"(\d{3,4})p", str(value or ""))
    return int(match.group(1)) if match else 0


QUALITY_HEIGHTS = (144, 240, 360, 480, 720, 1080, 1440, 2160)


def split_quality(text: str) -> tuple[str, int | None]:
    """Separa una calidad tipo «360» o «720p» del resto (devuelve texto y altura)."""
    tokens = text.split()
    for index, token in enumerate(tokens):
        match = re.fullmatch(r"(\d{3,4})p?", token.strip().lower())
        if not match:
            continue
        height = int(match.group(1))
        if height not in QUALITY_HEIGHTS:
            continue
        rest = " ".join(tokens[:index] + tokens[index + 1 :]).strip()
        if rest:
            return rest, height
    return text, None


def youtube_candidates(
    info: dict, want: str, height: int | None = None
) -> list[dict]:
    """Enlaces ordenados por preferencia: audio m4a/mp3 o MP4 desde 360p."""
    items = info.get("results") or []
    if want == "audio":
        options = [
            item
            for item in items
            if item.get("download") and str(item.get("type", "")).lower() == "audio"
        ]
        preferred = [
            item
            for item in options
            if str(item.get("extension", "")).lower() in ("mp3", "m4a")
        ]
        return preferred + [item for item in options if item not in preferred]
    videos = [
        item
        for item in items
        if item.get("download")
        and str(item.get("type", "")).lower() == "video"
        and str(item.get("extension", "")).lower() == "mp4"
    ]
    if not videos:
        videos = [
            item
            for item in items
            if item.get("download") and str(item.get("type", "")).lower() == "video"
        ]
    videos.sort(
        key=lambda item: (_resolution(item.get("quality")) or 10000, _size_bytes(item.get("fileSize")))
    )
    if height:
        # Calidades que pide el usuario, de la más alta a la más baja, y
        # primero las que caben en la subida de Telegram.
        matching = [
            item
            for item in videos
            if 0 < _resolution(item.get("quality")) <= height
        ]
        if matching:
            matching.sort(key=lambda item: _resolution(item.get("quality")), reverse=True)
            fits = [
                item
                for item in matching
                if not _size_bytes(item.get("fileSize"))
                or _size_bytes(item.get("fileSize")) <= YOYO_MAX_UPLOAD
            ]
            return fits + [item for item in matching if item not in fits]
        return videos
    # Prefiero la calidad más baja que siga viéndose bien (360p o más).
    good = [item for item in videos if _resolution(item.get("quality")) >= 360]
    return good + [item for item in videos if item not in good]


def pick_youtube_media(info: dict, want: str) -> dict | None:
    candidates = youtube_candidates(info, want)
    return candidates[0] if candidates else None


async def youtube_download(update: Update, context: ContextTypes.DEFAULT_TYPE, want: str) -> None:
    usage = (
        "/mp3 <enlace o búsqueda>"
        if want == "audio"
        else "/mp4 [calidad] <enlace o búsqueda>   ej. /mp4 360 https://youtu.be/xxxx"
        "   (calidades: 144, 240, 360, 480, 720, 1080, 1440, 2160)"
    )
    raw = require_args(context, usage)
    target, height = split_quality(raw)
    if want == "audio":
        height = None
    message = update.effective_message
    fmt = "mp3" if want == "audio" else "mp4"
    label = f"{fmt.upper()} · {target[:70]}"
    problems: list[str] = []
    async with download_task(update, label) as job:
        link = await youtube_link(target)
        video_id = youtube_video_id(link) or youtube_video_id(target)
        job.stage = "Leyendo la ficha del vídeo"
        details: dict = {}
        if video_id:
            try:
                details = await youtube_details(video_id)
            except CommandError:
                details = {}
            if not details.get("thumbnail"):
                details["thumbnail"] = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            details["video_id"] = video_id
        video_thumb = await video_cover(details) if details and want == "video" else None

        def audio_parts(source_title: str) -> tuple[str, str, str]:
            artist_guess, title_guess = split_song_title(source_title)
            channel = str(details.get("channel") or "")
            extra = (
                f"▶️ YouTube: {channel}"
                if channel and fold(channel) != fold(artist_guess)
                else ""
            )
            return artist_guess, title_guess, extra

        job.stage = "Pidiendo los enlaces a YOSOYYO"
        try:
            info = await yoyo_json("/api/youtube/v2", {"url": link, "format": fmt})
            if not isinstance(info, dict):
                raise CommandError("La API no devolvió información de ese vídeo.")
            candidates = youtube_candidates(info, want, height)
            if not candidates:
                raise CommandError("No hay enlaces de descarga para ese vídeo. Prueba con otro.")
            full_title = str(details.get("title") or info.get("title") or target)
            job.stage = "Descargando y subiendo el archivo"
            for chosen in candidates[:5]:
                _dl_check(job)
                job.begin()
                extension = str(
                    chosen.get("extension") or ("m4a" if want == "audio" else "mp4")
                )
                resolution = _resolution(chosen.get("quality")) if want == "video" else 0
                if want == "audio":
                    artist_guess, title_guess, extra = audio_parts(full_title)
                    ok = await send_song(
                        message,
                        str(chosen["download"]),
                        artist_guess,
                        title_guess,
                        duration=_as_int(details.get("duration"))
                        or _as_int(info.get("duration")),
                        filename=f"youtube.{extension}",
                        job=job,
                        cancelled=_dl_stopped(job),
                        progress=job.track,
                        extra=extra,
                    )
                else:
                    ok = await send_media_safely(
                        message,
                        "video",
                        str(chosen["download"]),
                        f"youtube.{extension}",
                        video_caption(details or {"title": full_title}, resolution),
                        _dl_stopped(job),
                        job.track,
                        thumb=video_thumb,
                    )
                if ok:
                    return
            problems.append("los enlaces de YOSOYYO no descargaron")
        except DownloadCancelled:
            raise
        except CommandError as exc:
            problems.append(str(exc))
        _dl_check(job)
        if not video_id:
            raise CommandError(problems[-1] if problems else "No pude leer ese enlace.")
        job.stage = "Probando otro proveedor de descarga" + (
            " (este no deja elegir calidad)" if height else ""
        )
        try:
            media = await tubeapi_media(video_id, fmt, cancelled=_dl_stopped(job))
        except DownloadCancelled:
            raise
        except CommandError as exc:
            raise CommandError(
                f"{problems[-1]}\n{exc}" if problems else str(exc)
            )
        job.stage = "Subiendo el archivo a Telegram"
        job.begin()
        media_title = str(media.get("title") or details.get("title") or target)
        if want == "audio":
            artist_guess, title_guess, extra = audio_parts(media_title)
            ok = await send_song(
                message,
                media["url"],
                artist_guess,
                title_guess,
                duration=_as_int(media.get("duration")) or _as_int(details.get("duration")),
                filename="youtube.mp3",
                job=job,
                cancelled=_dl_stopped(job),
                progress=job.track,
                extra=extra,
            )
        else:
            ok = await send_media_safely(
                message,
                "video",
                media["url"],
                "youtube.mp4",
                video_caption(details or {"title": media_title}),
                _dl_stopped(job),
                job.track,
                thumb=video_thumb,
            )
        if ok:
            return
        raise CommandError(
            "No pude subir el archivo, pero este enlace directo sí funciona "
            f"(caduca en unos minutos):\n{media['url']}"
        )


async def cmd_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await youtube_download(update, context, "audio")


async def cmd_mp4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await youtube_download(update, context, "video")


# --------------------------------------------------------------------------
# TubeAPI / y2mate.gs: segundo proveedor para bajar audio o vídeo de YouTube
# Adaptado del scraper de Daps (xyrcodex):
# https://gist.github.com/XyrexxArchive/adfa044d8e065e94e725ffe862d450bb
# --------------------------------------------------------------------------
TUBEAPI_KEY = "66d0abdc85f3b52ac9df11d95ebce90a"
TUBEAPI_WORKER = "https://royal-forest-deb8.holy-breeze-fec5.workers.dev/"
TUBEAPI_HEADERS = {"Referer": "https://tubeapi.org/", "Origin": "https://tubeapi.org"}
YT_ID_PATTERN = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:embed/|live/|shorts/|v/)"
    r"|[?&]v=)([A-Za-z0-9_-]{11})"
)


def youtube_video_id(text: str) -> str | None:
    """Saca el ID de 11 caracteres de un enlace de YouTube."""
    clean = (text or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", clean):
        return clean
    match = YT_ID_PATTERN.search(clean)
    return match.group(1) if match else None


def _tubeapi_json(params: dict, timeout: int = 45, attempts: int = 3) -> dict:
    """GET al worker de TubeAPI con reintentos (a veces devuelve 500 pasajero)."""
    url = _build_url(TUBEAPI_WORKER, params)
    status, body = 0, b""
    data: object = None
    for attempt in range(1, attempts + 1):
        status, body, _ = _fetch_url(
            url, "application/json", timeout, extra_headers=TUBEAPI_HEADERS
        )
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            data = None
        if isinstance(data, dict) and status == 200:
            break
        if attempt < attempts:
            time.sleep(1.5)
    if not isinstance(data, dict):
        raise CommandError(f"TubeAPI respondió HTTP {status} y no era JSON.")
    if status != 200 or data.get("status") == "fail":
        detail = data.get("message") or data.get("error") or str(data)[:120]
        raise CommandError(f"TubeAPI falló (HTTP {status}): {detail}")
    return data


def tubeapi_media_blocking(
    video_id: str, fmt: str, poll_seconds: int = 4, max_polls: int = 30, cancelled=None
) -> dict:
    """Pide la conversión y espera hasta que TubeAPI devuelva el archivo final."""
    started = time.time()
    init = _tubeapi_json(
        {
            "v": video_id,
            "f": fmt,
            "e": "i",
            "api_key": TUBEAPI_KEY,
            "_": int(started * 1000),
        }
    )
    status_url = str(init.get("statusUrl") or "")
    if not status_url:
        raise CommandError("TubeAPI no devolvió la URL de progreso.")
    encoded = base64.b64encode(status_url.encode()).decode()
    title = str(init.get("title") or "YouTube")
    duration = int(init.get("duration") or 0)
    for _ in range(max_polls):
        if cancelled is not None and cancelled():
            raise DownloadCancelled("Descarga cancelada ⛔️")
        data = _tubeapi_json(
            {
                "u": encoded,
                "e": "p",
                "api_key": TUBEAPI_KEY,
                "_": int(time.time() * 1000),
            }
        )
        if data.get("status") == "completed" and data.get("downloadUrl"):
            return {
                "url": str(data["downloadUrl"]),
                "title": str(data.get("title") or title),
                "duration": int(data.get("duration") or duration),
                "seconds": int(time.time() - started),
            }
        time.sleep(poll_seconds)
    raise CommandError("TubeAPI tardó demasiado en convertir ese vídeo.")


async def tubeapi_media(video_id: str, fmt: str, cancelled=None) -> dict:
    check_provider("tubeapi")
    try:
        media = await asyncio.to_thread(
            tubeapi_media_blocking, video_id, fmt, 4, 30, cancelled
        )
    except DownloadCancelled:
        raise
    except (CommandError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        provider_fail("tubeapi", "TubeAPI", exc)
        raise
    provider_ok("tubeapi", "TubeAPI")
    return media


async def cmd_spotify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/spotify <búsqueda>   ej. /spotify a thousand years")
    result = await yoyo_json("/api/spotifysearch", {"q": query})
    tracks = (result.get("tracks") or []) if isinstance(result, dict) else []
    if not tracks:
        raise CommandError(f"No encontré canciones de «{query}» en Spotify.")
    lines = [f"🎧 Spotify: {query}", ""]
    for track in tracks[:5]:
        artists = track.get("artists") or []
        names = ", ".join(str(a.get("name")) for a in artists if isinstance(a, dict))
        duration = int(track.get("duration_ms") or 0)
        stamp = f"{duration // 60000}:{duration // 1000 % 60:02d}" if duration else "?"
        lines.append(f"• {track.get('name')} — {names or '?'} ({stamp})")
        if track.get("url"):
            lines.append(f"  🔗 {track.get('url')}")
    await update.effective_message.reply_text(truncate("\n".join(lines)))


async def cmd_spotifydl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/spotifydl <enlace de la canción>")
    result = await yoyo_json("/api/spotifydl", {"url": link}, timeout=240)
    download = result.get("dl") if isinstance(result, dict) else None
    if not download:
        raise CommandError(
            "No pude descargar esa canción de Spotify. Prueba con otro enlace."
        )
    title = str(result.get("title") or "Spotify")
    author = str(result.get("author") or "")
    ok = await send_song(
        update.effective_message,
        str(download),
        author,
        title,
        filename="spotify.mp3",
        extra="🟢 Spotify",
    )
    if not ok:
        raise CommandError(f"No pude enviar el audio. Enlace directo:\n{download}")


async def cmd_pinterest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(context, "/pinterest <búsqueda>   ej. /pinterest zhao lusi")
    items = await yoyo_json("/api/pinterest", {"q": query, "limite": 6})
    if not isinstance(items, list) or not items:
        raise CommandError(f"No encontré imágenes de «{query}» en Pinterest.")
    message = update.effective_message
    sent = 0
    for item in items[:4]:
        image = item.get("descarga") or item.get("url")
        if not image:
            continue
        caption = f"📌 {item.get('titulo') or query}"
        if item.get("autor"):
            caption += f"\n👤 {item.get('autor')}"
        try:
            await message.reply_photo(photo=image, caption=caption[:1000])
            sent += 1
        except TelegramError:
            continue
    if not sent:
        raise CommandError("Pinterest devolvió resultados, pero no pude enviar las imágenes.")


async def cmd_instagram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/instagram <enlace del post>")
    result = await yoyo_json("/api/instagram", {"url": link})
    media = (result.get("media") or []) if isinstance(result, dict) else []
    if not media:
        raise CommandError("No encontré foto o vídeo en ese enlace de Instagram.")
    message = update.effective_message
    title = str(result.get("title") or "Instagram")
    caption = f"📸 {title}"
    if result.get("username"):
        caption += f"\n👤 {result.get('username')}"
    sent = 0
    for item in media[:4]:
        url = item.get("url") if isinstance(item, dict) else None
        if not url:
            continue
        try:
            if str(item.get("type") or "").lower() == "video":
                await message.reply_video(video=url, caption=caption[:1000])
            else:
                await message.reply_photo(photo=url, caption=caption[:1000])
            sent += 1
        except TelegramError:
            continue
    if not sent:
        raise CommandError("Instagram devolvió contenido, pero Telegram no pudo abrirlo.")


async def cmd_facebook(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/facebook <enlace del vídeo o reel>")
    result = await yoyo_json("/api/facebook", {"url": link})
    if not isinstance(result, dict):
        raise CommandError("Facebook no devolvió datos de ese enlace.")
    links = result.get("links") or {}
    video = (links.get("HD") or links.get("SD")) if isinstance(links, dict) else None
    title = str(result.get("title") or "Facebook")
    if not video:
        raise CommandError("No encontré el vídeo en ese enlace de Facebook.")
    try:
        await update.effective_message.reply_video(video=video, caption=f"🎬 {title}"[:1000])
    except TelegramError:
        raise CommandError(f"No pude enviar el vídeo. Enlace directo:\n{video}")


# --------------------------------------------------------------------------
# TikTok: descargar sin marca de agua o buscar vídeos (tikwm.com)
# Adaptado del comando de Gawr Gura Bot (Node/WhatsApp) a python-telegram-bot.
# --------------------------------------------------------------------------
TIKTOK_API = "https://www.tikwm.com/api"
TIKTOK_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-419,es;q=0.9",
    "Referer": "https://www.tikwm.com/",
}
TIKTOK_GAP = 1.25  # su API aguanta más o menos una petición cada 1,25 s
TIKTOK_CACHE_SECONDS = 180
TIKTOK_SEARCH_COUNT = 20
TIKTOK_SEND_RESULTS = 3  # cuántos vídeos de la búsqueda mando
TIKTOK_URL = re.compile(
    r"^(?:https?://)?(?:www\.|vm\.|vt\.|m\.)?(?:tiktok\.com|douyin\.com)/", re.I
)

_TIKTOK_LOCK = asyncio.Lock()
_TIKTOK_LAST_CALL = 0.0
_TIKTOK_CACHE: dict[str, tuple[float, dict]] = {}


def _tiktok_cache_get(key: str) -> dict | None:
    hit = _TIKTOK_CACHE.get(key)
    if not hit:
        return None
    stamp, value = hit
    if time.time() - stamp > TIKTOK_CACHE_SECONDS:
        _TIKTOK_CACHE.pop(key, None)
        return None
    return value


def _tiktok_cache_put(key: str, value: dict) -> None:
    _TIKTOK_CACHE[key] = (time.time(), value)
    while len(_TIKTOK_CACHE) > 50:
        _TIKTOK_CACHE.pop(next(iter(_TIKTOK_CACHE)))


def _tiktok_clean(item: dict) -> dict:
    """Deja la respuesta de tikwm en algo cómodo de usar."""
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    music = item.get("music_info") if isinstance(item.get("music_info"), dict) else {}
    images = item.get("images") if isinstance(item.get("images"), list) else []
    video_id = str(item.get("id") or item.get("video_id") or "")
    unique = str(author.get("unique_id") or "")
    link = (
        f"https://www.tiktok.com/@{unique}/video/{video_id}"
        if unique and video_id
        else ""
    )
    return {
        "id": video_id,
        "title": re.sub(r"\s+", " ", str(item.get("title") or "")).strip(),
        "duration": _as_int(item.get("duration")),
        "video": str(item.get("play") or item.get("video") or ""),
        "images": [str(image) for image in images if image],
        "audio": str(item.get("music") or music.get("play") or ""),
        "cover": str(item.get("cover") or ""),
        "plays": _as_int(item.get("play_count")),
        "url": link,
        "author": str(author.get("nickname") or ""),
        "unique": unique,
    }


def _tiktok_get(url: str, xhr: bool = False) -> dict:
    """GET a tikwm que devuelve el JSON ya comprobado (lanza CommandError)."""
    headers = dict(TIKTOK_HEADERS)
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    status, body, _ = _fetch_url(
        url, "application/json, text/plain, */*", 30, extra_headers=headers
    )
    if status != 200:
        raise CommandError(f"TikTok respondió HTTP {status}.")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise CommandError("TikTok devolvió una respuesta que no entiendo.")
    if not isinstance(data, dict):
        raise CommandError("TikTok devolvió una respuesta que no entiendo.")
    if data.get("code") != 0:
        raise CommandError(str(data.get("msg") or "TikTok no aceptó esa petición."))
    return data


def _tiktok_is_limit(error: object) -> bool:
    return bool(re.search(r"api limit|rate limit|too many", str(error), re.I))


async def _tiktok_wait() -> None:
    """Respeta el hueco entre peticiones que pide la API de tikwm."""
    global _TIKTOK_LAST_CALL
    async with _TIKTOK_LOCK:
        wait = _TIKTOK_LAST_CALL + TIKTOK_GAP - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _TIKTOK_LAST_CALL = time.time()


async def _tiktok_call(url: str, xhr: bool = False) -> dict:
    """Una llamada con su ritmo, su reintento si hay límite y su salud apuntada."""
    check_provider("tiktok")
    data: dict = {}
    for attempt in range(2):
        await _tiktok_wait()
        try:
            data = await asyncio.to_thread(_tiktok_get, url, xhr)
            break
        except CommandError as exc:
            if attempt == 0 and _tiktok_is_limit(exc):
                await asyncio.sleep(TIKTOK_GAP + 0.25)
                continue
            provider_fail("tiktok", "TikTok", exc)
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            provider_fail("tiktok", "TikTok", exc)
            raise CommandError(f"No pude contactar con TikTok: {exc}")
    provider_ok("tiktok", "TikTok")
    return data


async def tiktok_video(link: str) -> dict:
    """Ficha del vídeo (sin marca de agua) a partir de su enlace."""
    link = link.strip()
    key = f"v|{link}"
    cached = _tiktok_cache_get(key)
    if cached is not None:
        return cached
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link.lstrip("/")
    data = await _tiktok_call(_build_url(f"{TIKTOK_API}/", {"url": link, "hd": "1"}))
    item = data.get("data")
    if not isinstance(item, dict) or not item:
        raise CommandError("Ese vídeo no existe, es privado o lo borraron.")
    if item.get("status") == "pending" or str(item.get("video_id") or "") == "0":
        raise CommandError("El vídeo aún se está procesando; inténtalo otra vez.")
    clean = _tiktok_clean(item)
    _tiktok_cache_put(key, clean)
    return clean


async def tiktok_search(keywords: str) -> list[dict]:
    """Busca vídeos en TikTok y devuelve las fichas ya limpias."""
    count = max(1, min(50, TIKTOK_SEARCH_COUNT))
    key = f"s|{keywords.strip().lower()}|{count}"
    cached = _tiktok_cache_get(key)
    if cached is not None:
        return cached.get("videos") or []
    data = await _tiktok_call(
        _build_url(
            f"{TIKTOK_API}/feed/search/",
            {
                "keywords": keywords.strip(),
                "count": str(count),
                "cursor": "0",
                "web": "1",
                "hd": "1",
            },
        ),
        xhr=True,
    )
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    videos = [
        _tiktok_clean(video)
        for video in payload.get("videos") or []
        if isinstance(video, dict)
    ]
    videos = [v for v in videos if v["video"] or v["url"] or v["images"]]
    _tiktok_cache_put(key, {"videos": videos})
    return videos


def tiktok_caption(item: dict) -> str:
    lines = [f"🎬 {item['title'] or 'Vídeo de TikTok'}"]
    who = item["author"] or item["unique"]
    if item["unique"]:
        who = (
            f"{who} (@{item['unique']})"
            if who and who != item["unique"]
            else f"@{item['unique']}"
        )
    if who:
        lines.append(f"👤 {who}")
    bits = []
    if item["duration"]:
        bits.append(pretty_duration(item["duration"]))
    if item["plays"]:
        bits.append(f"{item['plays']:,} reproducciones".replace(",", "."))
    if bits:
        lines.append("📊 " + " · ".join(bits))
    return truncate("\n".join(lines), 1000)


async def tiktok_send(message, item: dict, job: "DownloadJob") -> bool:
    """Manda el vídeo o el carrusel de fotos de un resultado de TikTok."""
    caption = tiktok_caption(item)
    if item["images"]:
        sent = 0
        for photo in item["images"][:10]:
            try:
                await message.reply_photo(
                    photo=photo, caption=caption if not sent else None
                )
                sent += 1
            except TelegramError:
                continue
        return sent > 0
    if not re.match(r"^https?://", item["video"] or "", re.I):
        return False
    filename = f"tiktok_{item['id'] or uuid.uuid4().hex[:8]}.mp4"
    return await send_media_safely(
        message,
        "video",
        item["video"],
        filename,
        caption,
        _dl_stopped(job),
        job.track,
    )


async def cmd_tiktok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = require_args(
        context,
        "/tiktok <enlace o búsqueda>   ej. /tiktok https://vm.tiktok.com/xxxx "
        "o /tiktok gatos graciosos",
    )
    message = update.effective_message
    if TIKTOK_URL.match(raw.strip()):
        async with download_task(update, "TikTok · vídeo") as job:
            item = await tiktok_video(raw)
            _dl_check(job)
            job.stage = "Descargando y subiendo el vídeo"
            if not await tiktok_send(message, item, job):
                if not item["video"]:
                    raise CommandError("No encontré el vídeo en ese enlace de TikTok.")
                raise CommandError(
                    "No pude subir el vídeo, pero este enlace directo sí funciona:\n"
                    + item["video"]
                )
        return
    async with download_task(update, f"TikTok · {raw[:60]}") as job:
        videos = await tiktok_search(raw)
        if not videos:
            raise CommandError(f"No encontré vídeos de «{raw}» en TikTok.")
        sent = 0
        for item in videos[:TIKTOK_SEND_RESULTS]:
            _dl_check(job)
            if not re.match(r"^https?://", item["video"] or "", re.I) and item["url"]:
                try:
                    item = await tiktok_video(item["url"])
                except CommandError:
                    continue
            job.begin()
            job.stage = "Descargando y subiendo el vídeo"
            if await tiktok_send(message, item, job):
                sent += 1
        if not sent:
            raise CommandError(
                f"Encontré {len(videos)} vídeos de «{raw}», pero no pude bajar ninguno."
            )


async def cmd_fbstalk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/fbstalk <enlace de vídeo o perfil>")
    result = await yoyo_json("/api/fbstalk", {"url": link})
    if not isinstance(result, dict):
        raise CommandError("Facebook no devolvió datos de ese enlace.")
    lines = [f"🔎 Facebook ({result.get('type') or 'contenido'})"]
    for field in ("title", "name"):
        if result.get(field):
            lines.append(f"📝 {result.get(field)}")
    author = result.get("author") or {}
    if isinstance(author, dict) and author.get("name"):
        lines.append(f"👤 {author.get('name')}")
        if author.get("url"):
            lines.append(f"  🔗 {author.get('url')}")
    if result.get("username"):
        lines.append(f"👤 @{result.get('username')}")
    stats = result.get("stats") or {}
    if isinstance(stats, dict):
        if stats.get("likes"):
            lines.append(f"👍 {stats.get('likes')} me gusta")
        if stats.get("views"):
            lines.append(f"👀 {stats.get('views')} vistas")
    profile = result.get("authorProfile") or {}
    if isinstance(profile, dict):
        followers = (profile.get("stats") or {}).get("followers")
        if followers:
            lines.append(f"👥 {followers} seguidores")
    if result.get("url"):
        lines.append(f"🔗 {result.get('url')}")
    if len(lines) == 1:
        raise CommandError("No encontré información en ese enlace.")
    await update.effective_message.reply_text(truncate("\n".join(lines)))


MEDIAFIRE_AUDIO = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}
MEDIAFIRE_VIDEO = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
MEDIAFIRE_FILE = re.compile(
    r"^(?:https?://)?(?:www\.)?mediafire\.com/(?:file|file_premium|view)/"
    r"([A-Za-z0-9]+)/([^?#]+)",
    re.I,
)


def mediafire_kind(mime: str, name: str) -> str:
    """Qué tipo de mensaje le toca según el MIME o la extensión del archivo."""
    mime = (mime or "").lower()
    extension = Path(name or "").suffix.lower()
    if mime.startswith("audio/") or extension in MEDIAFIRE_AUDIO:
        return "audio"
    if mime.startswith("video/") or extension in MEDIAFIRE_VIDEO:
        return "video"
    return "document"


def _mediafire_group(pattern: str, page: str) -> str:
    match = re.search(pattern, page)
    return html.unescape(match.group(1)).strip() if match else ""


def _mediafire_blocking(link: str) -> dict:
    """Lee la página del archivo y saca el enlace directo, su nombre y su peso.

    El enlace de la API de YOSOYYO solo vale desde su servidor (MediaFire lo
    ata a la IP que lo pidió y aquí da 403), así que lo saco yo de la página.
    """
    url = link.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    status, body, _ = _fetch_url(
        url,
        "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        40,
        extra_headers={
            "Accept-Language": "es-ES,es;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        },
    )
    if status != 200 or not body:
        raise CommandError(f"MediaFire respondió HTTP {status}.")
    page = body.decode("utf-8", "replace")
    lowered = page.lower()
    if (
        "file has been removed" in lowered
        or "file is unavailable" in lowered
        or "file you are trying to download is no longer available" in lowered
    ):
        raise CommandError("Ese archivo ya no está disponible en MediaFire.")
    found = (
        re.search(r'aria-label="Download file"[^>]*href="([^"]+)"', page)
        or re.search(r'id="downloadButton"[^>]*href="([^"]+)"', page)
        or re.search(r'href="(https?://download[^"]+)"', page)
        or re.search(r'href="(//download[^"]+)"', page)
    )
    if not found:
        raise CommandError(
            "No encontré el enlace de descarga en esa página de MediaFire "
            "(¿es un enlace de carpeta o el archivo está bloqueado?)."
        )
    download = html.unescape(found.group(1)).strip()
    if download.startswith("//"):
        download = "https:" + download
    name = _mediafire_group(r'<div class="filename">([^<]+)</div>', page)
    if not name:
        name = Path(unquote(url.split("?")[0].rstrip("/").split("/")[-1])).name
    size_text = _mediafire_group(r"Download \(([^)]+)\)", page)
    return {"download": download, "name": name or "mediafire", "size_text": size_text}


async def cmd_mediafire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(
        context,
        "/mediafire <enlace del archivo>   ej. "
        "/mediafire https://www.mediafire.com/file/xxxx/archivo.zip/file",
    )
    if not MEDIAFIRE_FILE.match(link.strip()):
        raise CommandError("Mándame un enlace de archivo de MediaFire (mediafire.com/file/…).")
    try:
        info = await asyncio.to_thread(_mediafire_blocking, link)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CommandError(f"No pude abrir MediaFire: {exc}")
    name = info["name"]
    kind = mediafire_kind("", name)
    caption_lines = [f"📦 {name}"]
    if info["size_text"]:
        caption_lines.append(f"📏 {info['size_text']}")
    caption = truncate("\n".join(caption_lines), 1000)
    message = update.effective_message
    async with download_task(update, f"MediaFire · {name[:60]}") as job:
        job.stage = "Descargando el archivo de MediaFire"
        ok = await send_media_safely(
            message, kind, info["download"], name, caption, _dl_stopped(job), job.track
        )
        if not ok:
            raise CommandError(
                "No pude descargar o enviar ese archivo (¿pasa de 45 MB?). "
                f"Enlace directo:\n{info['download']}"
            )


async def cmd_tourl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/tourl <url de una imagen, audio o vídeo>")
    if not re.match(r"^https?://", link, re.IGNORECASE):
        raise CommandError("Mándame una URL completa que empiece por http:// o https://")
    result = await yoyo_json("/api/tourl", method="POST", payload={"url": link}, timeout=240)
    results = (result.get("results") or []) if isinstance(result, dict) else []
    if not results:
        raise CommandError("Ningún servidor de subida aceptó ese archivo.")
    lines = ["☁️ Enlaces del archivo:"]
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        lines.append(f"• {item.get('host') or '?'}: {item.get('url')}")
        if item.get("expires"):
            lines.append(f"  ⏳ {item.get('expires')}")
    await update.effective_message.reply_text(truncate("\n".join(lines)))


async def cmd_captura(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    link = require_args(context, "/captura <url>   ej. /captura https://google.com")
    if not re.match(r"^https?://", link, re.IGNORECASE):
        link = "https://" + link
    blob = await yoyo_bytes("/api/ssweb", {"url": link}, timeout=150)
    await update.effective_message.reply_photo(photo=blob, caption=f"🖥 {link}"[:1000])


# --------------------------------------------------------------------------
# Respuestas de IA con animación: primero "pensando" y luego se escriben solas
# --------------------------------------------------------------------------
AI_THINKING_FRAMES = (
    "🤔 Pensando",
    "🤔 Pensando.",
    "🤔 Pensando..",
    "🤔 Pensando...",
)
AI_THINKING_SECONDS = 1.5  # cada cuánto cambia la animación de pensar
AI_TYPE_SECONDS = 0.5  # cada cuánto aparece un trozo nuevo de la respuesta
AI_TYPE_MAX_SECONDS = 25  # tope de la animación de escritura
AI_TEXT_LIMIT = 4000  # Telegram corta en 4096


async def _ai_edit(panel, text: str) -> bool:
    """Edita el mensaje del panel; si Telegram pide esperar, le hace caso."""
    try:
        await panel.edit_text(text)
        return True
    except RetryAfter as exc:
        await asyncio.sleep(float(getattr(exc, "retry_after", 3) or 3))
        return False
    except TelegramError:
        return False


async def _ai_thinking(panel) -> None:
    """Animación de pensando: los puntitos van y vuelven sin parar."""
    step = 0
    while True:
        await _ai_edit(panel, AI_THINKING_FRAMES[step % len(AI_THINKING_FRAMES)])
        step += 1
        await asyncio.sleep(AI_THINKING_SECONDS)


async def _ai_type(panel, text: str) -> None:
    """Escribe la respuesta letra a letra, acelerando para no tardar una vida."""
    started = time.time()
    shown = 0
    chunk = 1
    while shown < len(text):
        if time.time() - started > AI_TYPE_MAX_SECONDS:
            # Se acabó el tiempo de la animación: remate de golpe.
            shown = max(shown, len(text) - 3)
        shown = min(len(text), shown + chunk)
        if await _ai_edit(panel, text[:shown]):
            await asyncio.sleep(AI_TYPE_SECONDS)
        # Letra a letra al principio y cada vez más rápido en respuestas largas.
        chunk = min(30, 1 + shown // 10)


async def ai_reply(message, fetch, prefix: str = "🤖") -> str:
    """Pide la respuesta a la IA con “pensando…” y la escribe letra a letra."""
    panel = await message.reply_text(AI_THINKING_FRAMES[0])
    thinking = asyncio.create_task(_ai_thinking(panel))
    try:
        answer = str(await fetch())
    except BaseException:
        thinking.cancel()
        try:
            await panel.delete()
        except TelegramError:
            pass
        raise
    thinking.cancel()
    text = (f"{prefix} {answer}".strip() if prefix else answer.strip())[:AI_TEXT_LIMIT]
    if not text:
        try:
            await panel.delete()
        except TelegramError:
            pass
        raise CommandError("La IA no devolvió respuesta.")
    await _ai_type(panel, text)
    return answer


async def cmd_ia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = require_args(context, "/ia <pregunta>   ej. /ia explícame la fotosíntesis")

    async def fetch() -> str:
        result = await yoyo_json("/api/deepseek", {"prompt": prompt}, timeout=180)
        if not isinstance(result, dict):
            raise CommandError("La IA no devolvió respuesta.")
        answer = result.get("answer") or result.get("response")
        if not answer:
            raise CommandError(
                str(result.get("message") or "La IA no devolvió respuesta.")
            )
        return str(answer)

    await ai_reply(update.effective_message, fetch, "🤖")


async def cmd_tts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = require_args(context, "/tts <texto>   ej. /tts hola mundo")
    if len(text) > 500:
        raise CommandError("El texto es muy largo; mándame menos de 500 caracteres.")
    blob = await yoyo_bytes("/api/tts", {"text": text, "lang": "es"}, timeout=150)
    message = update.effective_message
    try:
        await message.reply_audio(audio=blob, filename="voz.mp3", title=text[:64])
    except TelegramError:
        await message.reply_document(document=blob, filename="voz.mp3", caption=text[:200])


async def cmd_brat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = require_args(context, "/brat <texto>")
    blob = await yoyo_bytes("/api/brat", {"text": text, "color": "verde"})
    await update.effective_message.reply_photo(photo=blob, caption=text[:200])


async def cmd_bratvid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = require_args(context, "/bratvid <texto>")
    blob = await yoyo_bytes(
        "/api/bratvid", {"text": text, "theme": "white", "format": "mp4"}, timeout=240
    )
    await update.effective_message.reply_video(
        video=blob, filename="brat.mp4", caption=text[:200]
    )


async def cmd_banner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = require_args(context, "/banner <texto>   ej. /banner Bienvenido Andrés")
    params = {
        "width": 1000,
        "height": 500,
        "backgroundUrl": "https://picsum.photos/1000/500",
        "profileUrl": "https://ui-avatars.com/api/?size=200&name=" + quote(text, safe=""),
        "profileSize": 200,
        "profileX": 500,
        "profileY": 200,
        "text1": text,
        "text1X": 500,
        "text1Y": 350,
    }
    blob = await yoyo_bytes("/api/image/welcome-banner", params, timeout=180)
    await update.effective_message.reply_photo(photo=blob, caption="🖼 Tu banner")


def _birthday_args(context: ContextTypes.DEFAULT_TYPE, usage: str) -> tuple[str, str, str]:
    raw = require_args(context, usage)
    parts = raw.split(maxsplit=2)
    if len(parts) < 3:
        raise CommandError(f"Uso: {usage}")
    name, age, date = parts
    if not age.isdigit():
        raise CommandError("La edad tiene que ser un número, ej. 25.")
    return name, age, date


async def cmd_cumple(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name, age, date = _birthday_args(
        context, "/cumple <nombre> <edad> <fecha>   ej. /cumple Juan 25 10/08/2026"
    )
    blob = await yoyo_bytes(
        "/api/birthday", {"name": name, "age": age, "date": date}, timeout=180
    )
    await update.effective_message.reply_photo(photo=blob, caption=f"🎂 {name}, {age} años")


async def cmd_nivel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = require_args(
        context, "/nivel <nombre> <nivel> <xp> <xp máximo>   ej. /nivel Andrés 50 7500 10000"
    )
    parts = raw.split()
    if len(parts) < 4:
        raise CommandError("Uso: /nivel <nombre> <nivel> <xp> <xp máximo>")
    name = " ".join(parts[:-3])
    level, xp, max_xp = parts[-3:]
    if not (level.isdigit() and xp.isdigit() and max_xp.isdigit()):
        raise CommandError("Nivel, XP y XP máximo tienen que ser números.")
    blob = await yoyo_bytes(
        "/api/levelcard",
        {"name": name, "level": level, "xp": xp, "maxXp": max_xp},
        timeout=180,
    )
    await update.effective_message.reply_photo(
        photo=blob, caption=f"🏅 {name} · nivel {level}"
    )


async def cmd_apistatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await yoyo_json("/api/auth/status")
    if not isinstance(result, dict):
        raise CommandError("No pude leer el estado de la API.")
    system = result.get("system") or {}
    user = result.get("user_data") or {}
    lines = [
        f"🧩 {result.get('api_name') or 'YOSOYYO API'} — {result.get('creator') or '?'}",
        f"🟢 En línea: {'sí' if system.get('online') else 'no'}",
        f"👤 Usuario: {user.get('username') or '?'}",
        f"📊 Peticiones: {user.get('daily_requests_used', 0)}/{user.get('daily_limit', 0)}",
        f"✅ Quedan: {user.get('remaining_requests', 0)}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


# --------------------------------------------------------------------------
# OMEGA TECH API: búsqueda de música en YouTube Music
# (https://api.omegatech.app/api/download/play)
# --------------------------------------------------------------------------
OMEGA_BASE = "https://api.omegatech.app"
OMEGA_TIMEOUT = 90


async def omega_json(
    path: str, params: dict | None = None, timeout: int = OMEGA_TIMEOUT
) -> object:
    """GET a la API de Omega Tech, que responde {success, data} o {success, error}."""
    url = _build_url(OMEGA_BASE + path, params)
    try:
        status, body, _ = await asyncio.to_thread(
            _fetch_url, url, "application/json", timeout
        )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CommandError(f"No pude conectar con Omega Tech: {exc}")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise CommandError("Omega Tech devolvió una respuesta que no entiendo.")
    if isinstance(data, dict) and data.get("success") is False:
        raise CommandError(
            str(data.get("error") or data.get("message") or f"Omega Tech falló (HTTP {status}).")
        )
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data


def _omega_link(item: dict) -> str:
    """Enlace reproducible de un resultado (vídeo normal, para poder usar /mp3)."""
    video = item.get("videoId")
    if video:
        return f"https://www.youtube.com/watch?v={video}"
    return str(item.get("url") or "")


async def cmd_ytmusic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = require_args(
        context, "/ytmusic <búsqueda>   ej. /ytmusic alone"
    )
    data = await cached(
        f"ytmusic:{fold(query)}",
        600,
        lambda: omega_json(
            "/api/download/play",
            {"action": "search", "query": query, "format": "mp3"},
            timeout=OMEGA_TIMEOUT,
        ),
    )
    if not isinstance(data, dict):
        raise CommandError("YouTube Music no devolvió resultados.")
    lines = [f"🎧 YouTube Music: {query}"]
    seen: set[str] = set()
    top = data.get("topResult")
    if isinstance(top, dict) and top.get("title") and top.get("videoId"):
        seen.add(str(top["videoId"]))
        subtitles = top.get("subtitles")
        extra = ""
        if isinstance(subtitles, list) and len(subtitles) > 1:
            extra = f" — {subtitles[1]}"
        lines.append(f"\n⭐ Mejor resultado: {top.get('title')}{extra}")
        link = _omega_link(top)
        if link:
            lines.append(f"   {link}")
    songs = data.get("songs") if isinstance(data.get("songs"), list) else []
    shown = 0
    buttons: list[list[InlineKeyboardButton]] = []
    if songs:
        lines.append("")
    for song in songs:
        if shown >= 8:
            break
        if not isinstance(song, dict) or not song.get("title"):
            continue
        video = str(song.get("videoId") or "")
        if video and video in seen:
            continue
        if video:
            seen.add(video)
        shown += 1
        artist = str(song.get("artist") or "").strip()
        lines.append(f"{shown}. {song.get('title')}" + (f" — {artist}" if artist else ""))
        link = _omega_link(song)
        if link:
            lines.append(f"   {link}")
        if video:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"⬇️ {shown}. {str(song.get('title'))[:24]}",
                        callback_data=f"dl:{video}",
                    )
                ]
            )
    if not shown:
        albums = [a for a in data.get("albums") or [] if isinstance(a, dict) and a.get("title")]
        if not albums:
            raise CommandError(f"YouTube Music no encontró canciones para «{query}».")
        lines.append("")
        for album in albums[:5]:
            artist = str(album.get("artist") or "").strip()
            lines.append(f"💿 {album.get('title')}" + (f" — {artist}" if artist else ""))
    lines.append("")
    lines.append("Toca ⬇️ para bajar el audio, o usa /mp3 <enlace>")
    await update.effective_message.reply_text(
        truncate("\n".join(lines)),
        reply_markup=InlineKeyboardMarkup(buttons[:8]) if buttons else None,
    )


# --------------------------------------------------------------------------
# Meme "Windows Media Player" (quote-windows)
# Adaptado del plugin de JavraaSaturn (JavraTools):
# https://code.javraxaturn.my.id/code/quote-windows
# --------------------------------------------------------------------------
QWIN_TEMPLATE_URL = "https://files.catbox.moe/7otzp2.jpg"
QWIN_FONT_URLS = (
    "https://cdn.jsdelivr.net/gh/rsms/inter@v3.19/docs/font-files/Inter-Bold.otf",
    "https://raw.githubusercontent.com/rsms/inter/v3.19/docs/font-files/Inter-Bold.otf",
)
QWIN_DIR = SELF_PATH.parent / "qwindows"
QWIN_BASE_SIZE = 148
QWIN_INK_W = 451
QWIN_BASELINE = 715
QWIN_CX = 351
QWIN_COLOR = (28, 29, 33)
QWIN_MAX_WIDTH = 500
QWIN_BOX_TOP = 390
QWIN_BOX_BOTTOM = 1010
QWIN_MIN_SIZE = 34
QWIN_MAX_LINES = 6
QWIN_MAX_CHARS = 300
QWIN_COOLDOWN = 5
_QWIN_LAST: dict[int, float] = {}
_QWIN_FONTS: dict[int, object] = {}


def _qwin_asset(name: str, urls: tuple[str, ...], minimum: int) -> Path:
    """Plantilla y fuente: se descargan una vez y quedan cacheadas en disco."""
    path = QWIN_DIR / name
    if path.exists() and path.stat().st_size >= minimum:
        return path
    QWIN_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for url in urls:
        try:
            status, body, _ = _fetch_url(url, "*/*", 45)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            problems.append(f"{url}: {exc}")
            continue
        if status == 200 and len(body) >= minimum:
            path.write_bytes(body)
            return path
        problems.append(f"{url}: HTTP {status}")
    raise CommandError(
        "No pude descargar los archivos del meme de Windows. "
        + (problems[-1] if problems else "sin detalles")
    )


def qwindows_image(text: str) -> bytes:
    """Render del meme (1123x1401) con el texto en el hueco blanco de la izquierda."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise CommandError(
            "Falta Pillow en el servidor. Ejecuta: pip install Pillow "
            "(ya está en requirements.txt)."
        )

    template = _qwin_asset("template.jpg", (QWIN_TEMPLATE_URL,), 10_000)
    font_path = _qwin_asset("Inter-Bold.otf", QWIN_FONT_URLS, 20_000)

    def load_font(px: int):
        cached = _QWIN_FONTS.get(px)
        if cached is None:
            cached = ImageFont.truetype(str(font_path), px)
            _QWIN_FONTS[px] = cached
        return cached

    def ink(font, value: str) -> float:
        box = font.getbbox(value)
        return max(0.0, float(box[2] - box[0]))

    def wrap(font, value: str) -> list[str]:
        lines: list[str] = []
        for paragraph in value.split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            line = ""
            for word in words:
                test = f"{line} {word}" if line else word
                if ink(font, test) <= QWIN_MAX_WIDTH:
                    line = test
                    continue
                if line:
                    lines.append(line)
                    line = ""
                if ink(font, word) <= QWIN_MAX_WIDTH:
                    line = word
                    continue
                piece = ""
                for char in word:
                    if piece and ink(font, piece + char) > QWIN_MAX_WIDTH:
                        lines.append(piece)
                        piece = char
                    else:
                        piece += char
                line = piece
            if line:
                lines.append(line)
        return lines

    def layout(value: str, base: int) -> tuple[int, list[str], int]:
        paragraphs = value.split("\n")
        for size in range(base, round(base * 0.5) - 1, -2):
            font = load_font(size)
            if all(ink(font, p.strip()) <= QWIN_MAX_WIDTH for p in paragraphs):
                line_height = round(size * 1.16)
                count = len(paragraphs)
                first_top = QWIN_BASELINE - ((count - 1) / 2) * line_height - size * 0.74
                last_bottom = QWIN_BASELINE + ((count - 1) / 2) * line_height + size * 0.22
                if (
                    count <= QWIN_MAX_LINES
                    and first_top >= QWIN_BOX_TOP
                    and last_bottom <= QWIN_BOX_BOTTOM
                ):
                    return size, [p.strip() for p in paragraphs], line_height
        for size in range(base, QWIN_MIN_SIZE - 1, -2):
            font = load_font(size)
            lines = wrap(font, value)
            line_height = round(size * 1.16)
            count = len(lines)
            if count > QWIN_MAX_LINES:
                continue
            first_top = QWIN_BASELINE - ((count - 1) / 2) * line_height - size * 0.74
            last_bottom = QWIN_BASELINE + ((count - 1) / 2) * line_height + size * 0.22
            if first_top >= QWIN_BOX_TOP and last_bottom <= QWIN_BOX_BOTTOM:
                return size, lines, line_height
        font = load_font(QWIN_MIN_SIZE)
        return (
            QWIN_MIN_SIZE,
            wrap(font, value)[:QWIN_MAX_LINES],
            round(QWIN_MIN_SIZE * 1.16),
        )

    image = Image.open(io.BytesIO(template.read_bytes())).convert("RGB")
    draw = ImageDraw.Draw(image)
    calibrated = max(
        0.8, min(1.15, QWIN_INK_W / ink(load_font(QWIN_BASE_SIZE), "whaty"))
    )
    size, lines, line_height = layout(text, round(QWIN_BASE_SIZE * calibrated))
    font = load_font(size)
    count = len(lines)
    for index, line in enumerate(lines):
        y = round(QWIN_BASELINE + (index - (count - 1) / 2) * line_height)
        draw.text((QWIN_CX, y), line, font=font, fill=QWIN_COLOR, anchor="ms")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def cmd_qwindows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        replied = getattr(message, "reply_to_message", None)
        if replied is not None:
            text = str(replied.text or replied.caption or "").strip()
    if not text or text.lower() in ("help", "ayuda", "?"):
        raise CommandError(
            "Uso: /qwindows <texto>   ej. /qwindows Hola mundo\n"
            "También puedes responder a un mensaje de texto y escribir /qwindows."
        )
    if len(text) > QWIN_MAX_CHARS:
        raise CommandError(
            f"El texto es muy largo: {len(text)} caracteres (máximo {QWIN_MAX_CHARS})."
        )
    user = update.effective_user
    uid = int(getattr(user, "id", 0) or 0)
    wait = QWIN_COOLDOWN - (time.time() - _QWIN_LAST.get(uid, 0.0))
    if wait > 0:
        raise CommandError(f"⏳ Espera {int(wait) + 1} segundos antes de otro meme.")
    _QWIN_LAST[uid] = time.time()
    status = await message.reply_text("🪟 Preparando el meme…")
    try:
        blob = await asyncio.to_thread(qwindows_image, text)
    finally:
        try:
            await status.delete()
        except TelegramError:
            pass
    caption = truncate(text, 200)
    try:
        await message.reply_photo(photo=blob, caption=caption)
    except TelegramError:
        await message.reply_document(
            document=blob, filename="qwindows.png", caption=caption
        )


# --------------------------------------------------------------------------
# DeepAI Chat (deepai.org/chat)
# Adaptado del scraper de Vamz Spectre (VamxAPI):
# https://code.vamxapi.my.id/snippet/raw/deepai-chat
# --------------------------------------------------------------------------
DEEPAI_URL = "https://api.deepai.org/hacking_is_a_serious_crime"
DEEPAI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def deepai_key() -> str:
    """Clave de prueba tipo tryit-<rand>-<hash>, como la que genera la web."""
    rand = random.randint(10**11, 10**12 - 1)
    return f"tryit-{rand}-{hashlib.md5(f'{DEEPAI_UA}{rand}'.encode()).hexdigest()}"


def _deepai_chat_blocking(prompt: str, timeout: int = 90, language: str = "es") -> str:
    """POST multipart al chat de DeepAI; devuelve el texto de la respuesta."""
    boundary = f"----botpy{uuid.uuid4().hex}"
    fields = {
        "chat_style": "chat",
        "language": language,
        "chatHistory": json.dumps(
            [{"role": "user", "content": prompt}], ensure_ascii=False
        ),
        "model": "standard",
        "session_uuid": str(uuid.uuid4()),
        "hacker_is_stinky": "very_stinky",
    }
    body = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
        for name, value in fields.items()
    ) + f"--{boundary}--\r\n"
    request = Request(
        DEEPAI_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "api-key": deepai_key(),
            "User-Agent": DEEPAI_UA,
            "Origin": "https://deepai.org",
            "Referer": "https://deepai.org/chat",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "text/plain,*/*",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace").strip()
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:200]
        finally:
            exc.close()
        raise CommandError(f"DeepAI respondió HTTP {exc.code}. {detail}".strip())
    except (URLError, TimeoutError, OSError) as exc:
        raise CommandError(f"No pude contactar con DeepAI: {exc}")
    if not text:
        raise CommandError("DeepAI no devolvió ninguna respuesta.")
    return text


async def deepai_chat(prompt: str) -> str:
    check_provider("deepai")
    try:
        answer = await asyncio.to_thread(_deepai_chat_blocking, prompt)
    except (CommandError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        provider_fail("deepai", "DeepAI", exc)
        raise
    provider_ok("deepai", "DeepAI")
    return answer


async def cmd_deepai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = require_args(
        context, "/deepai <pregunta>   ej. /deepai explícame los agujeros negros"
    )
    await ai_reply(
        update.effective_message, lambda: deepai_chat(prompt), "🌀"
    )


# --------------------------------------------------------------------------
# Buscador de comandos
# --------------------------------------------------------------------------
def find_commands(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    """Busca comandos por nombre y descripción, sin tildes ni mayúsculas."""
    needle = fold(query)
    if not needle:
        return []
    scored: list[tuple[int, tuple[str, str, str]]] = []
    for item in COMMANDS:
        command, _, description = item
        name = fold(command)
        haystack = f"{name} {fold(description)}"
        if needle == name:
            score = 100
        elif name.startswith(needle):
            score = 80
        elif needle in name:
            score = 60
        elif needle in haystack:
            score = 40
        elif all(word in haystack for word in needle.split()):
            score = 20
        elif any(
            word.startswith(needle) or (len(word) >= 4 and needle.startswith(word))
            for word in haystack.split()
        ):
            score = 15
        else:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        raise CommandError("Uso: /buscar <texto>   ej. /buscar clima")
    matches = find_commands(query)
    if not matches:
        raise CommandError(f"No encontré comandos que hablen de «{query}». Abre /menu.")
    lines = [f"🔎 Resultados para «{query}»", ""]
    lines.extend(_command_line(command) for command, _, _ in matches)
    lines += ["", "Abre /menu para verlos por categorías."]
    await update.effective_message.reply_text(truncate("\n".join(lines)))


# --------------------------------------------------------------------------
# Salud de las APIs y métricas
# --------------------------------------------------------------------------
def provider_report() -> str:
    lines = ["🩺 SALUD DE LAS APIS", ""]
    if not _PROVIDERS:
        lines.append("Todavía sin datos: usa algún comando con API y vuelve a mirar.")
    for state in _PROVIDERS.values():
        wait = state["open_until"] - time.time()
        if wait > 0:
            status = f"🔴 en cuarentena ({int(wait) + 1} s)"
        elif state["fails"]:
            status = f"🟡 {state['fails']} fallo(s) seguidos"
        else:
            status = "🟢 bien"
        last_ok = (
            datetime.fromtimestamp(state["last_ok"]).strftime("%H:%M:%S")
            if state["last_ok"]
            else "—"
        )
        lines.append(f"{status} · {state['label']} · última OK: {last_ok}")
        if state["fails"] and state["last_error"]:
            lines.append(f"   ↳ {state['last_error']}")
    total = _CACHE_STATS["hits"] + _CACHE_STATS["misses"]
    rate = (100 * _CACHE_STATS["hits"] / total) if total else 0
    lines += [
        "",
        f"🗄 Caché: {len(_CACHE)} entradas · {_CACHE_STATS['hits']}/{total} aciertos ({rate:.0f}%)",
        f"⏱ Activo desde hace {uptime_text()}",
        f"🧩 Comandos: {len(COMMANDS)}",
    ]
    return "\n".join(lines)


async def cmd_salud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(truncate(provider_report()))


def usage_report(limit: int = 5) -> str:
    usage = _DATA["usage"]
    if not usage:
        return "📊 Uso: todavía sin datos"
    top = sorted(usage.items(), key=lambda pair: int(pair[1]), reverse=True)[:limit]
    total = sum(int(value) for value in usage.values())
    lines = [f"📊 Comandos usados: {total}", ""]
    lines.extend(f"{index}. /{name} — {count}" for index, (name, count) in enumerate(top, 1))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Recordatorios
# --------------------------------------------------------------------------
REMINDER_MAX_SECONDS = 30 * 86400


def reminders_of(chat_id: object) -> list[dict]:
    return [item for item in _DATA["reminders"] if str(item.get("chat_id")) == str(chat_id)]


async def cmd_recordar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    chat_id = getattr(chat, "id", None) or getattr(message, "chat_id", 0)
    raw = " ".join(context.args).strip() if context.args else ""
    if not raw:
        items = sorted(reminders_of(chat_id), key=lambda item: item.get("due", 0))
        if not items:
            raise CommandError(
                "No tienes recordatorios pendientes.\n"
                "Uso: /recordar 10m llamar a mamá\nTambién: /recordar 09:30 despertar"
            )
        rows = [
            [InlineKeyboardButton(f"❌ {item['text'][:30]}", callback_data=f"rm:{item['id']}")]
            for item in items[:8]
        ]
        lines = ["⏰ TUS RECORDATORIOS", ""]
        for item in items[:10]:
            lines.append(
                f"• {str(item.get('text'))[:60]} — en "
                f"{pretty_duration(float(item.get('due', 0)) - time.time())}"
            )
        lines += ["", "Toca uno para cancelarlo."]
        await message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return
    seconds, text = parse_when(raw)
    if seconds is None:
        raise CommandError(
            "No entendí cuándo. Usa 10m, 2h, 1d o una hora como 09:30.\n"
            "Ejemplo: /recordar 15m sacar la ropa"
        )
    if not text:
        raise CommandError("¿Qué te recuerdo? Ejemplo: /recordar 10m llamar a mamá")
    if seconds > REMINDER_MAX_SECONDS:
        raise CommandError("Puedo recordártelo como máximo dentro de 30 días.")
    if len(text) > 300:
        raise CommandError("El recordatorio es muy largo (máximo 300 caracteres).")
    reminder = {
        "id": uuid.uuid4().hex[:10],
        "chat_id": chat_id,
        "text": text,
        "due": time.time() + seconds,
        "user": getattr(update.effective_user, "first_name", "") or "",
    }
    _DATA["reminders"].append(reminder)
    save_data(force=True)
    when = datetime.fromtimestamp(reminder["due"]).strftime("%d/%m %H:%M")
    await message.reply_text(f"⏰ Listo: te aviso el {when} ({pretty_duration(seconds)}).\n📝 {text}")


async def on_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    reminder_id = (query.data or "")[3:]
    before = len(_DATA["reminders"])
    _DATA["reminders"] = [
        item for item in _DATA["reminders"] if item.get("id") != reminder_id
    ]
    removed = before - len(_DATA["reminders"])
    if removed:
        save_data(force=True)
    await query.answer(
        "Recordatorio cancelado ✅" if removed else "Ese recordatorio ya no existe."
    )


# --------------------------------------------------------------------------
# Conversión de archivos
# --------------------------------------------------------------------------
IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp"}
MEDIA_FORMATS = {"mp3", "ogg", "m4a", "wav", "mp4", "gif", "webm"}


def convert_image(blob: bytes, target: str) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(blob))
    if target in ("jpg", "jpeg"):
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG" if target in ("jpg", "jpeg") else target.upper())
    return buffer.getvalue()


def convert_media(blob: bytes, target: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CommandError(
            "Este hosting no tiene ffmpeg, así que solo puedo convertir imágenes "
            "(jpg, png, webp)."
        )
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "entrada"
        output = Path(folder) / f"salida.{target}"
        source.write_bytes(blob)
        command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(source)]
        if target in ("mp3", "ogg", "m4a", "wav"):
            command.append("-vn")
        command.append(str(output))
        result = subprocess.run(command, capture_output=True, timeout=300)
        if result.returncode != 0 or not output.exists():
            detail = result.stderr.decode("utf-8", "replace")[-200:].strip()
            raise CommandError(f"ffmpeg no pudo convertir el archivo. {detail}")
        return output.read_bytes()


async def cmd_convertir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    replied = getattr(message, "reply_to_message", None)
    target = context.args[0].lower().lstrip(".") if context.args else ""
    if replied is None or not (
        replied.photo
        or replied.document
        or replied.audio
        or replied.video
        or replied.voice
    ):
        raise CommandError(
            "Responde a una imagen, audio o vídeo con /convertir <formato>.\n"
            "Formatos: jpg, png, webp, mp3, ogg, m4a, wav, mp4, gif."
        )
    if target not in IMAGE_FORMATS | MEDIA_FORMATS:
        raise CommandError(
            "Formato no soportado. Usa jpg, png, webp, mp3, ogg, m4a, wav, mp4 o gif."
        )
    if replied.photo:
        media = replied.photo[-1]
    else:
        media = replied.document or replied.audio or replied.video or replied.voice
    if media is None:
        raise CommandError("No pude leer el archivo de ese mensaje.")
    blob = bytes(await (await media.get_file()).download_as_bytearray())
    if len(blob) > 20 * 1024 * 1024:
        raise CommandError("El archivo pasa de 20 MB: Telegram no me deja bajarlo así.")
    status = await message.reply_text("🔄 Convirtiendo…")
    try:
        if target in IMAGE_FORMATS:
            result = await asyncio.to_thread(convert_image, blob, target)
        else:
            result = await asyncio.to_thread(convert_media, blob, target)
    finally:
        try:
            await status.delete()
        except TelegramError:
            pass
    filename = f"convertido.{target}"
    if target in IMAGE_FORMATS:
        await message.reply_document(document=result, filename=filename)
    elif target in ("mp3", "ogg", "m4a", "wav"):
        try:
            await message.reply_audio(audio=result, filename=filename)
        except TelegramError:
            await message.reply_document(document=result, filename=filename)
    else:
        try:
            await message.reply_video(video=result, filename=filename)
        except TelegramError:
            await message.reply_document(document=result, filename=filename)


# --------------------------------------------------------------------------
# Resumen de un enlace
# --------------------------------------------------------------------------
async def summarize_url(url: str) -> str:
    page = await http_text(url)
    text = re.sub(r"\s+", " ", strip_html(page)).strip()
    if len(text) < 200:
        raise CommandError("No pude sacar texto de ese enlace (¿necesita iniciar sesión?).")
    answer = await deepai_chat(
        "Resume este texto en español en 5 puntos cortos, con viñetas:\n\n" + text[:6000]
    )
    return truncate(f"📝 Resumen de {url}\n\n{answer}", 3000)


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = require_args(
        context, "/resumen <url>   ej. /resumen https://es.wikipedia.org/wiki/Python"
    )
    if not re.match(r"^https?://", url, re.I):
        raise CommandError("Mándame un enlace que empiece por http:// o https://")
    await ai_reply(update.effective_message, lambda: summarize_url(url), "")


# --------------------------------------------------------------------------
# Botón de descarga de audio en /ytmusic y modo inline
# --------------------------------------------------------------------------
async def on_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    video_id = (query.data or "")[3:]
    if not youtube_video_id(video_id):
        await query.answer("Ese enlace no me sirve.", show_alert=True)
        return
    await query.answer("⬇️ Bajando el audio…")
    message = update.effective_message
    try:
        async with download_task(update, "MP3 · audio de /ytmusic") as job:
            job.stage = "Leyendo la ficha del vídeo"
            details: dict = {}
            try:
                details = await youtube_details(video_id)
            except CommandError:
                details = {}
            artist_guess, title_guess = split_song_title(
                str(details.get("title") or "")
            )
            job.stage = "Pidiendo el audio a TubeAPI"
            media = await tubeapi_media(video_id, "mp3", cancelled=_dl_stopped(job))
            job.stage = "Subiendo el audio a Telegram"
            job.begin()
            if not await send_song(
                message,
                media["url"],
                artist_guess,
                title_guess or str(media.get("title") or ""),
                duration=_as_int(media.get("duration")) or _as_int(details.get("duration")),
                filename="youtube.mp3",
                job=job,
                cancelled=_dl_stopped(job),
                progress=job.track,
            ):
                raise CommandError(
                    f"No pude subir el audio. Enlace directo:\n{media['url']}"
                )
    except DownloadCancelled:
        return
    except CommandError as exc:
        await message.reply_text(f"⚠️ {exc}")


async def on_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Modo inline: @bot gato / @bot perro en cualquier chat."""
    inline = update.inline_query
    if inline is None:
        return
    lowered = fold(inline.query or "")
    results: list = []
    if lowered.startswith(("gato", "cat")):
        image = f"https://cataas.com/cat?width=800&t={random.randint(1, 10**9)}"
        results.append(
            InlineQueryResultPhoto(
                id="gato", photo_url=image, thumbnail_url=image, caption="🐱"
            )
        )
    if lowered.startswith(("perro", "dog")):
        data = await http_json("https://dog.ceo/api/breeds/image/random")
        image = (data or {}).get("message") if isinstance(data, dict) else None
        if image:
            results.append(
                InlineQueryResultPhoto(
                    id="perro", photo_url=str(image), thumbnail_url=str(image), caption="🐶"
                )
            )
    if not results:
        results.append(
            InlineQueryResultArticle(
                id="ayuda",
                title="Escribe «gato» o «perro»",
                description="Mando la foto aquí mismo, sin abrir el chat",
                input_message_content=InputTextMessageContent(
                    "🐾 Escribe mi nombre y luego «gato» o «perro»"
                ),
            )
        )
    await inline.answer(results, cache_time=5)


# --------------------------------------------------------------------------
# Errores
# --------------------------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Convierte cualquier fallo en un mensaje claro para el usuario."""
    error = context.error
    message = getattr(update, "effective_message", None)
    try:
        if isinstance(error, DownloadCancelled):
            # El mensaje de progreso ya avisa de la cancelación.
            return
        if isinstance(error, CommandError):
            if message is not None:
                await message.reply_text(f"⚠️ {error}")
            return
        if isinstance(error, (HTTPError, URLError, TimeoutError, OSError, ValueError)):
            print(f"Fallo de red en un comando: {error!r}")
            if message is not None:
                await message.reply_text(
                    "⚠️ No pude conectar con el servicio. Inténtalo de nuevo en un minuto."
                )
            return
        print(f"Error inesperado en un comando: {error!r}")
        if OWNER_ID:
            command = getattr(message, "text", "") or ""
            _ALERTS.append(f"💥 Error en {command[:60] or 'un comando'}: {error!r}"[:400])
        if message is not None:
            await message.reply_text("⚠️ Algo falló con ese comando. Prueba otra vez.")
    except TelegramError as exc:
        print(f"No se pudo avisar del error: {exc}")


# --------------------------------------------------------------------------
# Validación del token
# --------------------------------------------------------------------------
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
    global LAST_UPDATE_CHECK
    LAST_UPDATE_CHECK = time.time()
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
        await asyncio.sleep(max(1, next_check_in()))
        if check_and_update(verbose=False):
            restart_with_new_version()


async def on_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instala la versión nueva al tocar el botón de /version."""
    query = update.callback_query
    if query is None:
        return
    message = update.effective_message
    await query.answer("⬆️ Comprobando GitHub…")
    try:
        changed = await asyncio.to_thread(check_and_update, False)
    except Exception as exc:  # aviso claro si algo revienta
        await message.reply_text(f"⚠️ No pude actualizar: {exc}")
        return
    if not changed:
        await message.reply_text(
            "ℹ️ No hay nada que instalar: ya estás en la última versión "
            f"(v{VERSION}).\n⏳ Próximo auto-update en "
            f"{human_seconds(next_check_in())}."
        )
        return
    await message.reply_text("✅ Versión nueva descargada. Reiniciando el bot…")
    await asyncio.sleep(1)
    restart_with_new_version()


async def on_startup(application: Application) -> None:
    """Publica los comandos, carga el estado y arranca las tareas de fondo."""
    load_data()
    await register_commands(application)
    application.create_task(auto_update_loop())
    application.create_task(maintenance_loop(application))


async def register_commands(application: Application) -> None:
    """Publica el menú de comandos en Telegram (se ejecuta al arrancar)."""
    try:
        await application.bot.set_my_commands(
            [BotCommand(command, description) for command, _, description in COMMANDS]
        )
        print("Comandos registrados en el menú de Telegram.")
    except TelegramError as exc:
        print(f"No se pudieron registrar los comandos en el menú: {exc}")


HANDLERS = {
    "start": cmd_start,
    "menu": cmd_menu,
    "help": cmd_help,
    "ping": cmd_ping,
    "info": cmd_info,
    "buscar": cmd_buscar,
    "salud": cmd_salud,
    "recordar": cmd_recordar,
    "convertir": cmd_convertir,
    "resumen": cmd_resumen,
    "estado": cmd_estado,
    "version": cmd_version,
    "calc": cmd_calc,
    "clima": cmd_clima,
    "noticias": cmd_noticias,
    "wiki": cmd_wiki,
    "traducir": cmd_traducir,
    "cripto": cmd_cripto,
    "divisa": cmd_divisa,
    "sismos": cmd_sismos,
    "pokemon": cmd_pokemon,
    "perro": cmd_perro,
    "gato": cmd_gato,
    "chiste": cmd_chiste,
    "github": cmd_github,
    "ip": cmd_ip,
    "dns": cmd_dns,
    "whois": cmd_whois,
    "yt": cmd_yt,
    "qr": cmd_qr,
    "acortar": cmd_acortar,
    "dado": cmd_dado,
    "moneda": cmd_moneda,
    "nasa": cmd_nasa,
    "receta": cmd_receta,
    "trago": cmd_trago,
    "serie": cmd_serie,
    "anime": cmd_anime,
    "libro": cmd_libro,
    "aire": cmd_aire,
    "pais": cmd_pais,
    "stack": cmd_stack,
    "radio": cmd_radio,
    "mc": cmd_mc,
    "carta": cmd_carta,
    "trivia": cmd_trivia,
    "wayback": cmd_wayback,
    "publicar": cmd_publicar,
    "sol": cmd_sol,
    "accion": cmd_accion,
    "gif": cmd_gif,
    "pelicula": cmd_pelicula,
    "claves": cmd_claves,
    "iss": cmd_iss,
    "arte": cmd_arte,
    "poema": cmd_poema,
    "consejo": cmd_consejo,
    "nombre": cmd_nombre,
    "feriados": cmd_feriados,
    "cerveza": cmd_cerveza,
    "producto": cmd_producto,
    "musica": cmd_musica,
    "letra": cmd_letra,
    "color": cmd_color,
    "aburrido": cmd_aburrido,
    "zorro": cmd_zorro,
    "dato": cmd_dato,
    "tcg": cmd_tcg,
    "f1": cmd_f1,
    "mar": cmd_mar,
    "http": cmd_http,
    "universidad": cmd_universidad,
    "deporte": cmd_deporte,
    "covid": cmd_covid,
    "app": cmd_app,
    "mp3": cmd_mp3,
    "mp4": cmd_mp4,
    "spotify": cmd_spotify,
    "spotifydl": cmd_spotifydl,
    "ytmusic": cmd_ytmusic,
    "pinterest": cmd_pinterest,
    "instagram": cmd_instagram,
    "facebook": cmd_facebook,
    "tiktok": cmd_tiktok,
    "fbstalk": cmd_fbstalk,
    "mediafire": cmd_mediafire,
    "tourl": cmd_tourl,
    "captura": cmd_captura,
    "ia": cmd_ia,
    "deepai": cmd_deepai,
    "tts": cmd_tts,
    "brat": cmd_brat,
    "bratvid": cmd_bratvid,
    "banner": cmd_banner,
    "qwindows": cmd_qwindows,
    "cumple": cmd_cumple,
    "nivel": cmd_nivel,
    "apistatus": cmd_apistatus,
    "echo": cmd_echo,
}


async def maintenance_loop(application: Application) -> None:
    """Entrega recordatorios, avisa al dueño de los fallos y guarda el estado."""
    while True:
        now = time.time()
        pending = [r for r in _DATA["reminders"] if float(r.get("due", 0)) <= now]
        for reminder in pending:
            try:
                await application.bot.send_message(
                    reminder["chat_id"], f"⏰ Recordatorio: {reminder.get('text', '')}"
                )
            except TelegramError as exc:
                print(f"No pude enviar un recordatorio: {exc}")
            try:
                _DATA["reminders"].remove(reminder)
            except ValueError:
                pass
            save_data(force=True)
        while _ALERTS:
            alert = _ALERTS[0]
            print(f"AVISO: {alert}")
            if OWNER_ID:
                try:
                    await application.bot.send_message(OWNER_ID, alert)
                except TelegramError as exc:
                    print(f"No pude avisar al dueño: {exc}")
            _ALERTS.pop(0)
        save_data()
        await asyncio.sleep(15)


def _counted(command: str, handler):
    """Envuelve un comando para llevar la cuenta de cuántas veces se usa."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        bump_usage(command)
        await handler(update, context)

    return wrapper


def register_handlers(application: Application) -> None:
    for command, handler in HANDLERS.items():
        application.add_handler(CommandHandler(command, _counted(command, handler)))
    application.add_handler(CallbackQueryHandler(on_menu_callback, pattern=r"^m:"))
    application.add_handler(CallbackQueryHandler(on_update_callback, pattern=r"^upd:"))
    application.add_handler(CallbackQueryHandler(on_reminder_callback, pattern=r"^rm:"))
    application.add_handler(CallbackQueryHandler(on_download_callback, pattern=r"^dl:"))
    application.add_handler(InlineQueryHandler(on_inline))


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
    application.add_error_handler(on_error)

    print(f"Bot v{VERSION} arrancado con {len(COMMANDS)} comandos. Pulsa Ctrl+C para detenerlo.")
    application.run_polling()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot detenido.")
