"""Szybki test połączenia z Oracle – dane logowania brane z pliku .env."""
import oracledb
from config import ORA

con = oracledb.connect(user=ORA["user"], password=ORA["password"],
                       dsn=ORA["dsn"], tcp_connect_timeout=3)
cur = con.cursor()
cur.execute("SELECT nazwa FROM typy_produktow")
print(cur.fetchall())
con.close()
