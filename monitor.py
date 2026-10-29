"""Monitor da atividade no SQL Server durante os testes do TP1.

Consulta o servidor a cada 2 segundos e regista, com data e hora:
  - sessões que se ligam e desligam (computador, aplicação, login, nível de isolamento)
  - transações que ficam abertas e quando terminam
  - bloqueios: quem espera por quem, em que recurso e durante quanto tempo
  - edições feitas no Edit (registos 'O' em LogOperations)
  - deadlocks (sessão system_health), com o grafo guardado em .xdl (abre no SSMS)

Os eventos aparecem no ecrã e ficam em eventos.log, nesta pasta.

Correr:  python monitor.py      (Ctrl+C para parar)
A password do sa é lida da variável SA_PW ou, se não existir, do contentor Docker mei_trab.
"""

import os
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pymssql

PASTA = Path(__file__).resolve().parent
FICHEIRO_EVENTOS = PASTA / "eventos.log"
SERVIDOR, PORTA, BASE = "192.168.100.14", "14330", "MEI_TRAB"
INTERVALO = 2             # segundos entre consultas às sessões
INTERVALO_DEADLOCKS = 10  # segundos entre leituras do histórico de deadlocks
ESTE_PC = socket.gethostname().upper()

NIVEIS = {0: "?", 1: "READ UNCOMMITTED", 2: "READ COMMITTED", 3: "REPEATABLE READ",
          4: "SERIALIZABLE", 5: "SNAPSHOT"}

SQL_SESSOES = """
SELECT s.session_id, s.login_time, s.login_name, s.host_name, s.program_name, c.client_net_address,
       s.transaction_isolation_level, s.open_transaction_count,
       r.blocking_session_id, r.wait_type, r.wait_resource
FROM sys.dm_exec_sessions s
JOIN sys.dm_exec_connections c ON c.session_id = s.session_id
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
WHERE s.is_user_process = 1
  AND s.database_id = DB_ID()  -- só sessões que estão a usar a MEI_TRAB
  AND s.session_id <> @@SPID
  AND s.program_name NOT IN ('SQLServerCEIP', 'TP1-Monitor')
  AND s.program_name NOT LIKE '%IntelliSense%'
"""

# Cada edição deixa dois registos 'O' com a mesma referência: o primeiro é o início, o segundo o fim
SQL_EDICOES = """
SELECT O.NumReg, O.Objecto, O.Valor, O.Referencia, O.UserID, O.TerminalName,
       (SELECT TOP 1 A.Valor FROM LogOperations A
        WHERE A.EventType = 'O' AND A.Referencia = O.Referencia AND A.NumReg < O.NumReg
        ORDER BY A.NumReg) AS ValorInicio
FROM LogOperations O
WHERE O.EventType = 'O' AND O.NumReg > %s
ORDER BY O.NumReg
"""

SQL_DEADLOCKS = """
SELECT CAST(t.target_data AS nvarchar(max))
FROM sys.dm_xe_session_targets t
JOIN sys.dm_xe_sessions s ON s.address = t.event_session_address
WHERE s.name = 'system_health' AND t.target_name = 'ring_buffer'
"""


def registar(tipo, texto):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {tipo:<10} {texto}"
    print(linha, flush=True)
    with open(FICHEIRO_EVENTOS, "a", encoding="utf-8") as ficheiro:
        ficheiro.write(linha + "\n")


def password_sa():
    if os.environ.get("SA_PW"):
        return os.environ["SA_PW"]
    saida = subprocess.run(["docker", "inspect", "mei_trab", "--format",
                            "{{range .Config.Env}}{{println .}}{{end}}"],
                           capture_output=True, text=True, check=True).stdout
    for linha in saida.splitlines():
        if linha.startswith("MSSQL_SA_PASSWORD="):
            return linha.split("=", 1)[1]
    raise SystemExit("Não encontrei a password do sa (defina a variável SA_PW).")


def ligar(password):
    conn = pymssql.connect(server=SERVIDOR, port=PORTA, user="sa", password=password, database=BASE,
                           autocommit=True, login_timeout=5, appname="TP1-Monitor")
    cursor = conn.cursor()
    # O monitor nunca pode ficar bloqueado nem bloquear os testes
    cursor.execute("SET NOCOUNT ON; SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED; "
                   "SET LOCK_TIMEOUT 1000; SET TEXTSIZE 2147483647;")
    return conn


def texto_erro(exc):
    args = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], tuple) else exc.args
    texto = args[1] if len(args) > 1 else args[0] if args else exc
    if isinstance(texto, bytes):
        texto = texto.decode("utf-8", errors="replace")
    return str(texto).split("DB-Lib error message")[0].strip() or str(texto).strip()


def ler_sessoes(cursor):
    cursor.execute(SQL_SESSOES)
    sessoes = {}
    for (spid, inicio, login, host, app, ip, nivel, transacoes,
         bloqueador, espera, recurso) in cursor.fetchall():
        sessoes[spid] = {"spid": spid, "inicio": inicio, "login": login, "host": (host or "?").strip(),
                         "app": (app or "?").strip(), "ip": ip, "nivel": nivel, "transacoes": transacoes,
                         "bloqueador": bloqueador if bloqueador and bloqueador > 0 and bloqueador != spid else 0,
                         "espera": espera, "recurso": recurso}
    return sessoes


def descrever(sessao):
    onde = sessao["host"] + (" (este PC)" if sessao["host"].upper() == ESTE_PC else "")
    return f"SPID {sessao['spid']} · {sessao['login']}@{onde} · {sessao['app']}"


def origem_bloqueio(sessoes, spid):
    """Segue a cadeia de espera até à sessão que não espera por ninguém (quem tem o trinco).

    blocking_session_id pode apontar para outra sessão que também está na fila de espera
    do mesmo recurso; a origem é a primeira sessão da cadeia que não está bloqueada.
    """
    vistos = set()
    while spid in sessoes and sessoes[spid]["bloqueador"] and spid not in vistos:
        vistos.add(spid)
        spid = sessoes[spid]["bloqueador"]
    return spid


def ler_deadlocks(cursor):
    cursor.execute(SQL_DEADLOCKS)
    linha = cursor.fetchone()
    if not linha or not linha[0]:
        return []
    deadlocks = []
    for evento in ET.fromstring(linha[0]).iter("event"):
        if evento.get("name") == "xml_deadlock_report":
            deadlock = evento.find(".//deadlock")
            if deadlock is not None:
                deadlocks.append((evento.get("timestamp"), deadlock))
    return deadlocks


def registar_deadlock(instante_utc, deadlock):
    vitimas = {v.get("id") for v in deadlock.findall("./victim-list/victimProcess")}
    processos = deadlock.findall("./process-list/process")

    def resumo(p):
        return (f"SPID {p.get('spid')} ({p.get('loginname')}@{p.get('hostname')} · {p.get('clientapp')} · "
                f"{p.get('isolationlevel')})")

    try:
        instante = datetime.fromisoformat(instante_utc.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        instante = datetime.now()
    ficheiro = PASTA / f"deadlock_{instante:%Y%m%d_%H%M%S}.xdl"
    ET.ElementTree(deadlock).write(ficheiro, encoding="utf-8")
    vitima = "; ".join(resumo(p) for p in processos if p.get("id") in vitimas)
    outros = "; ".join(resumo(p) for p in processos if p.get("id") not in vitimas)
    registar("DEADLOCK", f"vítima: {vitima} | outros: {outros} | grafo em {ficheiro.name}")


def duracao_edicao(inicio, fim):
    try:
        segundos = (datetime.fromisoformat(fim) - datetime.fromisoformat(inicio)).total_seconds()
        return f"{segundos:.0f} s"
    except (TypeError, ValueError):
        return "?"


class Monitor:
    def __init__(self):
        self.sessoes = {}
        self.transacao_desde = {}  # SPID -> instante em que a transação foi vista aberta
        self.bloqueio_desde = {}   # SPID -> (instante, SPID que bloqueia)
        self.ultimo_numreg = None
        self.deadlocks_vistos = None
        self.proximos_deadlocks = 0.0
        self.primeira = True

    def ciclo(self, cursor):
        atuais = ler_sessoes(cursor)
        self._comparar_sessoes(atuais)
        self._edicoes(cursor)
        if time.monotonic() >= self.proximos_deadlocks:
            self._deadlocks(cursor)
            self.proximos_deadlocks = time.monotonic() + INTERVALO_DEADLOCKS
        self.primeira = False

    def _esquecer(self, spid):
        self.transacao_desde.pop(spid, None)
        self.bloqueio_desde.pop(spid, None)

    def _comparar_sessoes(self, atuais):
        agora = time.monotonic()
        for spid in set(self.sessoes) - set(atuais):
            antiga = self.sessoes[spid]
            nota = " · tinha uma transação aberta, que o SQL Server desfez" if spid in self.transacao_desde else ""
            registar("DESLIGOU", descrever(antiga) + nota)
            self._esquecer(spid)

        for spid, s in atuais.items():
            antiga = self.sessoes.get(spid)
            if antiga is not None and antiga["inicio"] != s["inicio"]:  # SPID reutilizado por outra ligação
                registar("DESLIGOU", descrever(antiga))
                self._esquecer(spid)
                antiga = None
            if antiga is None:
                registar("JÁ LIGADA" if self.primeira else "LIGOU",
                         f"{descrever(s)} · {NIVEIS.get(s['nivel'], '?')} · IP {s['ip']}")
            elif antiga["nivel"] != s["nivel"]:
                registar("NÍVEL", f"{descrever(s)} mudou para {NIVEIS.get(s['nivel'], '?')}")

            if s["transacoes"] > 0 and spid not in self.transacao_desde:
                self.transacao_desde[spid] = agora
                registar("TRANSAÇÃO", f"{descrever(s)} abriu uma transação ({NIVEIS.get(s['nivel'], '?')})")
            elif s["transacoes"] == 0 and spid in self.transacao_desde:
                aberta = agora - self.transacao_desde.pop(spid)
                registar("TRANSAÇÃO", f"{descrever(s)} terminou a transação (aberta ~{aberta:.0f} s)")

            bloqueador = s["bloqueador"]
            anterior = self.bloqueio_desde.get(spid)
            if bloqueador and (anterior is None or anterior[1] != bloqueador):
                self.bloqueio_desde[spid] = (agora, bloqueador)
                quem = atuais.get(bloqueador)
                por = f"SPID {bloqueador}" + (f" ({quem['login']}@{quem['host']} · {quem['app']})" if quem else "")
                texto = f"{descrever(s)} está à espera de {por} · {s['espera']} em {s['recurso']}"
                origem = origem_bloqueio(atuais, bloqueador)
                if origem != bloqueador and origem in atuais:
                    texto += f" · origem do bloqueio: {descrever(atuais[origem])}"
                registar("BLOQUEIO", texto)
            elif not bloqueador and anterior is not None:
                self.bloqueio_desde.pop(spid)
                registar("FIM BLOQ.", f"{descrever(s)} deixou de esperar por SPID {anterior[1]} "
                                      f"(esperou ~{agora - anterior[0]:.0f} s)")
        self.sessoes = atuais

    def _edicoes(self, cursor):
        if self.ultimo_numreg is None:
            cursor.execute("SELECT ISNULL(MAX(NumReg), 0) FROM LogOperations")
            self.ultimo_numreg = cursor.fetchone()[0]
            return
        cursor.execute(SQL_EDICOES, (self.ultimo_numreg,))
        for numreg, enc, valor, referencia, utilizador, terminal, valor_inicio in cursor.fetchall():
            self.ultimo_numreg = max(self.ultimo_numreg, numreg)
            quem = f"{utilizador}@{(terminal or '?').strip()}"
            if valor_inicio is None:
                registar("EDIÇÃO", f"{quem} abriu a encomenda {enc} no Edit (referência {referencia})")
            else:
                registar("EDIÇÃO", f"{quem} terminou a edição da encomenda {enc} "
                                   f"({duracao_edicao(valor_inicio, valor)}, referência {referencia})")

    def _deadlocks(self, cursor):
        deadlocks = ler_deadlocks(cursor)
        if self.deadlocks_vistos is None:  # os deadlocks anteriores ao arranque não são reportados
            self.deadlocks_vistos = {instante for instante, _ in deadlocks}
            return
        for instante, deadlock in deadlocks:
            if instante not in self.deadlocks_vistos:
                self.deadlocks_vistos.add(instante)
                bases = {p.get("currentdbname") for p in deadlock.findall("./process-list/process")}
                if BASE in bases:  # ignora deadlocks noutras bases de dados do servidor
                    registar_deadlock(instante, deadlock)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    password = password_sa()
    monitor = Monitor()
    conn = None
    sem_ligacao = False
    registar("MONITOR", f"a vigiar {BASE} em {SERVIDOR},{PORTA} (este PC: {ESTE_PC})")
    while True:
        try:
            if conn is None:
                conn = ligar(password)
                if sem_ligacao:
                    registar("LIGAÇÃO", "ligação ao servidor recuperada")
                    sem_ligacao = False
            monitor.ciclo(conn.cursor())
            time.sleep(INTERVALO)
        except pymssql.Error as exc:
            if not sem_ligacao:
                registar("ERRO", f"falha a consultar o servidor: {texto_erro(exc)}")
                sem_ligacao = True
            try:
                if conn is not None:
                    conn.close()
            except pymssql.Error:
                pass
            conn = None
            time.sleep(5)
        except KeyboardInterrupt:
            registar("MONITOR", "parado")
            break


if __name__ == "__main__":
    main()
