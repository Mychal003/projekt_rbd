"""Konfiguracja stanowiska – czytana z pliku .env w katalogu projektu (.env NIE trafia do Gita).

Ten sam kod działa w obu sklepach. O tym, którym sklepem jest laptop, decyduje MOJ_SKLEP w .env:
  WRO – Wrocław: baza Oracle, prowadzi katalog sieci,
  WAW – Warszawa: baza SQL Server, kopia katalogu, raporty.
Wzory: .env.wro.przyklad (laptop Wrocław) i .env.waw.przyklad (laptop Warszawa).

Brak pliku albo błędne ustawienie zatrzymuje aplikację z czytelnym komunikatem,
zamiast po cichu uruchomić ją jako niewłaściwy sklep.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

KATALOG_PROJEKTU = Path(__file__).resolve().parent.parent
PLIK_ENV = KATALOG_PROJEKTU / ".env"


class BladKonfiguracji(RuntimeError):
    """Plik .env nie istnieje albo ma błędne ustawienia."""


if not PLIK_ENV.exists():
    raise BladKonfiguracji(
        f"Brak pliku {PLIK_ENV}. Skopiuj .env.wro.przyklad (laptop Wrocław) albo "
        f".env.waw.przyklad (laptop Warszawa) jako .env i uzupełnij hasła.")

# override=True: wartości z .env wygrywają ze zmiennymi ustawionymi w systemie
load_dotenv(PLIK_ENV, override=True)

MOJ_SKLEP = os.getenv("MOJ_SKLEP", "").strip().upper()
if MOJ_SKLEP not in ("WRO", "WAW"):
    raise BladKonfiguracji(f"W pliku {PLIK_ENV} ustaw MOJ_SKLEP=WRO albo MOJ_SKLEP=WAW "
                           f"(teraz: {os.getenv('MOJ_SKLEP')!r}).")

ORA = {
    "user": os.getenv("ORA_USER", "sklep_wro"),
    "password": os.getenv("ORA_PASSWORD", ""),
    "dsn": os.getenv("ORA_DSN", ""),           # host:port/usługa, np. 127.0.0.1:1521/FREEPDB1
}

MSSQL = {
    "driver": os.getenv("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server"),
    "host": os.getenv("MSSQL_HOST", ""),
    "port": os.getenv("MSSQL_PORT", "1433"),
    "database": os.getenv("MSSQL_DATABASE", "sklep_waw"),
    "user": os.getenv("MSSQL_USER", "aplikacja_waw"),
    "password": os.getenv("MSSQL_PASSWORD", ""),
}

_brak = [nazwa for nazwa, wartosc in (("ORA_DSN", ORA["dsn"]), ("ORA_PASSWORD", ORA["password"]),
                                      ("MSSQL_HOST", MSSQL["host"]), ("MSSQL_PASSWORD", MSSQL["password"]))
         if not wartosc]
if _brak:
    raise BladKonfiguracji(f"W pliku {PLIK_ENV} brakuje: {', '.join(_brak)}.")
