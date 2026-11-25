#!/usr/bin/env python3
"""Punto de entrada para servidores que arrancan main.py.

Muchos hostings (Railway, Render, Replit, Fly.io, paneles con "start
command") buscan un main.py como punto de entrada. Este archivo solo
llama al bot que vive en bot.py, que sigue siendo el único que tiene
la lógica.

Uso:
    python main.py
    # o: python main.py (el hosting lo ejecuta solo)
"""

from __future__ import annotations

import sys

import bot


def main() -> None:
    # En un servidor sin consola interactiva no se puede pedir el token:
    # mejor avisar claro que quedarse esperando una entrada que no llega.
    if bot.read_saved_token() is None and not sys.stdin.isatty():
        print("No hay token guardado y este servidor no tiene consola interactiva.")
        print(f"Crea {bot.TOKEN_FILE.name} junto a main.py con el token dentro,")
        print("o ejecuta el bot una vez desde una terminal para guardarlo.")
        raise SystemExit(1)

    bot.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot detenido.")
