#!/usr/bin/env python3
"""Bot de Telegram en un solo archivo.

La primera vez que se ejecuta pide el token por consola y lo guarda en
token.txt (junto a este archivo). En arranques posteriores lo lee solo.

Además se actualiza solo: descarga bot.py desde GitHub, y si hay una
versión nueva la escribe y se reinicia.

Todos los comandos usan APIs públicas gratuitas que no piden key
(Open-Meteo, CoinGecko, exchangerate-api, ip-api, Google DNS, RDAP,
Wikipedia, MyMemory, USGS, PokeAPI, Dog CEO, Cataas, JokeAPI, GitHub,
TinyURL, api.qrserver.com) o hacen scraping ligero de RSS y YouTube.
No se usa ninguna credencial de terceros.

Uso:
    pip install "python-telegram-bot>=21"
    python bot.py
"""

from __future__ import annotations

import ast
import asyncio
import html
import json
import operator
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import telegram
from telegram import Bot, BotCommand, Update
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

SELF_PATH = Path(__file__).resolve()
TOKEN_FILE = SELF_PATH.with_name("token.txt")
TOKEN_PATTERN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

VERSION = "2.0"
START_TIME = time.time()

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
    ("help", "", "lista de comandos"),
    ("ping", "", "comprueba que sigo vivo"),
    ("info", "", "tus datos de Telegram"),
    ("estado", "", "estado y versión del bot"),
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


def truncate(text: str, limit: int = 3900) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def money(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:,.6f}"


def require_args(context: ContextTypes.DEFAULT_TYPE, usage: str) -> str:
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        raise CommandError(f"Uso: {usage}")
    return text


# --------------------------------------------------------------------------
# Comandos básicos
# --------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user and user.first_name else "amigo"
    await update.effective_message.reply_text(
        f"¡Hola, {name}! 👋\n"
        f"Soy tu bot de Telegram y tengo {len(COMMANDS)} comandos: clima, noticias, "
        "Wikipedia, traducción, cripto, divisas, DNS, QR, YouTube y más.\n"
        "Escribe /help para verlos todos."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["Comandos disponibles:"]
    for command, args, description in COMMANDS:
        lines.append(f"/{command}{args} — {description}")
    await update.effective_message.reply_text(truncate("\n".join(lines)))


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("🏓 pong")


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


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    seconds = int(time.time() - START_TIME)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    uptime = f"{days}d {hours}h {minutes}m {secs}s" if days else f"{hours}h {minutes}m {secs}s"
    await update.effective_message.reply_text(
        f"🤖 Bot v{VERSION}\n"
        f"⏱ Encendido desde hace {uptime}\n"
        f"🧩 Comandos: {len(COMMANDS)}\n"
        f"🐍 Python {sys.version.split()[0]}\n"
        f"📦 python-telegram-bot {telegram.__version__}\n"
        f"🔄 Auto-update cada {UPDATE_INTERVAL_SECONDS // 60} min"
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


async def weather_report(city: str) -> str:
    geo = await http_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": city, "count": 1, "language": "es", "format": "json"},
    )
    results = (geo or {}).get("results") or []
    if not results:
        raise CommandError(f"No encontré la ciudad «{city}». Prueba con otro nombre.")
    place = results[0]
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
    await update.effective_message.reply_text(truncate(await weather_report(city)))


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
    await update.effective_message.reply_text(truncate(await wiki_report(query)))


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
async def pokemon_report(name: str) -> str:
    data = await http_json(
        f"https://pokeapi.co/api/v2/pokemon/{quote(name.strip().lower(), safe='')}"
    )
    if not isinstance(data, dict) or "name" not in data:
        raise CommandError(f"No encontré el Pokémon «{name}».")
    types = ", ".join(
        entry.get("type", {}).get("name", "?") for entry in data.get("types") or []
    )
    stats = {entry["stat"]["name"]: entry["base_stat"] for entry in data.get("stats") or []}
    height = (data.get("height") or 0) / 10
    weight = (data.get("weight") or 0) / 10
    artwork = ((data.get("sprites") or {}).get("other") or {}).get("official-artwork") or {}
    image = artwork.get("front_default") or (data.get("sprites") or {}).get("front_default")
    lines = [
        f"⚡ #{data.get('id')} {str(data.get('name')).capitalize()}",
        f"🧬 Tipo: {types or '?'}",
        f"📏 {height:.1f} m  •  ⚖️ {weight:.1f} kg",
        f"❤️ PS {stats.get('hp', '?')}  •  ⚔️ ATQ {stats.get('attack', '?')}  •  "
        f"🛡 DEF {stats.get('defense', '?')}  •  💨 VEL {stats.get('speed', '?')}",
    ]
    if image:
        lines.append(f"🖼 {image}")
    return "\n".join(lines)


async def cmd_pokemon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = require_args(context, "/pokemon <nombre>   ej. /pokemon pikachu")
    await update.effective_message.reply_text(truncate(await pokemon_report(name)))


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
# Errores
# --------------------------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Convierte cualquier fallo en un mensaje claro para el usuario."""
    error = context.error
    message = getattr(update, "effective_message", None)
    try:
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


HANDLERS = {
    "start": cmd_start,
    "help": cmd_help,
    "ping": cmd_ping,
    "info": cmd_info,
    "estado": cmd_estado,
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
    "echo": cmd_echo,
}


def register_handlers(application: Application) -> None:
    for command, handler in HANDLERS.items():
        application.add_handler(CommandHandler(command, handler))


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
