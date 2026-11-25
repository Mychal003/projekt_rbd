"""Połączenia z obiema bazami i bezpieczne wykonywanie zapytań.

Zasada: aplikacja łączy się z każdą bazą OSOBNO, z krótkim timeoutem,
i nigdy nie zakłada, że obie działają. Awaria jednego serwera wyłącza
tylko część danych.

Zapytania piszemy z parametrami „?” (styl pyodbc). Dla Oracle znaki „?”
są automatycznie zamieniane na :1, :2, ... (styl oracledb).
"""
import re
import time
from contextlib import closing, contextmanager

import oracledb
import pandas as pd
import pyodbc

from config import MSSQL, ORA

SKLEPY = ["WRO", "WAW"]
NAZWA = {"WRO": "Wrocław", "WAW": "Warszawa"}
TIMEOUT = 3  # sekundy


class Niedostepny(Exception):
    """Nie udało się połączyć z bazą sklepu."""


# ---------------------------------------------------------------- połączenia
def polacz_oracle(autocommit=False):
    con = oracledb.connect(user=ORA["user"], password=ORA["password"],
                           dsn=ORA["dsn"], tcp_connect_timeout=TIMEOUT)
    con.autocommit = autocommit
    return con


def polacz_mssql(autocommit=False):
    cs = (f"DRIVER={{{MSSQL['driver']}}};"
          f"SERVER={MSSQL['host']},{MSSQL['port']};DATABASE={MSSQL['database']};"
          f"UID={MSSQL['user']};PWD={MSSQL['password']};"
          "Encrypt=yes;TrustServerCertificate=yes;")
    return pyodbc.connect(cs, timeout=TIMEOUT, autocommit=autocommit)


def polacz(sklep, autocommit=False):
    try:
        return polacz_oracle(autocommit) if sklep == "WRO" else polacz_mssql(autocommit)
    except Exception as e:
        raise Niedostepny(f"Sklep {NAZWA[sklep]} niedostępny ({e})") from e


def sql_dla(sklep, sql):
    """Zamienia parametry ? na :1, :2, ... dla Oracle."""
    if sklep != "WRO":
        return sql
    licznik = iter(range(1, 1000))
    return re.sub(r"\?", lambda _: f":{next(licznik)}", sql)


def _execute(cur, sklep, sql, params):
    if params:
        cur.execute(sql_dla(sklep, sql), list(params))
    else:
        cur.execute(sql_dla(sklep, sql))


def _ramka(cur):
    kolumny = [d[0].lower() for d in cur.description]
    return pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=kolumny)


# ---------------------------------------------------------------- odczyt
def zapytanie(sklep, sql, params=()):
    """Zwraca (DataFrame, None) albo (None, komunikat błędu)."""
    try:
        with closing(polacz(sklep)) as con:
            cur = con.cursor()
            _execute(cur, sklep, sql, params)
            return _ramka(cur), None
    except Niedostepny as e:
        return None, str(e)
    except Exception as e:
        return None, f"Błąd zapytania w sklepie {NAZWA[sklep]}: {e}"


def status(sklep):
    """(True/False, opis) – prosty test połączenia."""
    try:
        with closing(polacz(sklep)) as con:
            cur = con.cursor()
            cur.execute("SELECT 1 FROM DUAL" if sklep == "WRO" else "SELECT 1")
            cur.fetchone()
        return True, "połączono"
    except Exception:
        return False, "brak połączenia"


# ---------------------------------------------------------------- zapis
class Kursor:
    """Kursor, który przyjmuje SQL z parametrami ? dla obu baz."""

    def __init__(self, sklep, cur):
        self.sklep, self.cur = sklep, cur

    def execute(self, sql, *params):
        _execute(self.cur, self.sklep, sql, params)
        return self

    @property
    def rowcount(self):
        return self.cur.rowcount

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def nowe_zamowienie(self, wartosc):
        """Wstawia zrealizowane zamówienie i zwraca jego ID."""
        if self.sklep == "WRO":
            nowe_id = self.cur.var(oracledb.NUMBER)
            self.cur.execute(
                "INSERT INTO zamowienia (status, wartosc) VALUES ('ZREALIZOWANE', :1) "
                "RETURNING id_zamowienia INTO :2", [wartosc, nowe_id])
            return int(nowe_id.getvalue()[0])
        self.cur.execute(
            "INSERT INTO zamowienia (status, wartosc) OUTPUT INSERTED.id_zamowienia "
            "VALUES ('ZREALIZOWANE', ?)", [wartosc])
        return int(self.cur.fetchone()[0])


@contextmanager
def transakcja(sklep):
    """Jedna LOKALNA transakcja w bazie jednego sklepu (commit albo rollback)."""
    con = polacz(sklep)
    try:
        yield Kursor(sklep, con.cursor())
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def procedura(sklep, sql, params=()):
    """Wywołanie procedury w trybie autocommit.

    Bez transakcji po stronie klienta – inaczej SQL Server próbowałby
    zrobić transakcję rozproszoną z Oracle (MSDTC). Zwraca DataFrame
    z pierwszym zestawem wyników albo None.
    """
    with closing(polacz(sklep, autocommit=True)) as con:
        cur = con.cursor()
        _execute(cur, sklep, sql, params)
        while cur.description is None:
            if not cur.nextset():
                return None
        return _ramka(cur)


def pomiar(sklep, sql, powtorzen=5):
    """Średni czas zapytania w ms (po jednym przebiegu rozgrzewkowym) i liczba wierszy."""
    with closing(polacz(sklep, autocommit=True)) as con:
        cur = con.cursor()
        cur.execute(sql)
        wiersze = len(cur.fetchall())
        czasy = []
        for _ in range(powtorzen):
            start = time.perf_counter()
            cur.execute(sql)
            cur.fetchall()
            czasy.append((time.perf_counter() - start) * 1000)
    return sum(czasy) / len(czasy), wiersze
