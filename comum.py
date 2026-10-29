"""Código partilhado pelas quatro aplicações do TP1 (Controlo de Transações).

Cada aplicação abre a sua própria ligação ao SQL Server (uma sessão, com SPID
próprio) e executa todas as instruções numa thread dedicada. Assim a janela
continua a responder quando uma consulta fica bloqueada à espera de um trinco,
e a barra de estado mostra há quanto tempo a sessão está à espera.
"""

import json
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tkinter import messagebox, ttk

import pymssql

# Prefixo das referências que o Edit escreve em LogOperations (ex.: G1-20261022143005123)
GRUPO = "G1"

NIVEIS_ISOLAMENTO = (
    "READ UNCOMMITTED",
    "READ COMMITTED",
    "REPEATABLE READ",
    "SNAPSHOT",
    "SERIALIZABLE",
)

# Códigos de sys.dm_exec_sessions.transaction_isolation_level
_NIVEL_POR_CODIGO = {
    1: "READ UNCOMMITTED",
    2: "READ COMMITTED",
    3: "REPEATABLE READ",
    4: "SERIALIZABLE",
    5: "SNAPSHOT",
}

ERRO_DEADLOCK = 1205
ERRO_LOCK_TIMEOUT = 1222
ERRO_SNAPSHOT_DESATIVADO = 3952
ERRO_CONFLITO_SNAPSHOT = 3960

_REPETIR = "Para continuar, repita a operação (no Edit, abra a encomenda de novo para ver os valores atuais)."

_EXPLICACOES = {
    ERRO_DEADLOCK: "Esta sessão foi escolhida como vítima de um deadlock. "
                   "O SQL Server desfez a transação.\n" + _REPETIR,
    ERRO_LOCK_TIMEOUT: "Esgotou-se o tempo de espera por um trinco (LOCK_TIMEOUT).",
    ERRO_SNAPSHOT_DESATIVADO: "O nível SNAPSHOT não está ativo nesta base de dados. Ative-o com:\n"
                              "ALTER DATABASE <nome da base de dados> SET ALLOW_SNAPSHOT_ISOLATION ON",
    ERRO_CONFLITO_SNAPSHOT: "Conflito de atualização em SNAPSHOT: outra transação alterou e "
                            "confirmou estes dados depois de esta ter começado. "
                            "O SQL Server desfez a transação.\n" + _REPETIR,
}

_FICHEIRO_CONFIG = Path.home() / ".mei_trab_login.json"


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _args_erro(exc):
    """(número, mensagem) de um erro do pymssql; os erros de ligação vêm dentro de outro tuplo."""
    args = exc.args
    if len(args) == 1 and isinstance(args[0], tuple):
        args = args[0]
    return args


def numero_erro(exc):
    """Número do erro do SQL Server (ex.: 1205), ou None se não for um erro do servidor."""
    args = _args_erro(exc)
    if args and isinstance(args[0], int):
        return args[0]
    return None


def descrever_erro(exc):
    """Texto para o utilizador: número do erro, mensagem do servidor e, se houver, explicação."""
    args = _args_erro(exc)
    numero = numero_erro(exc)
    if len(args) > 1:
        texto = args[1]
    elif args:
        texto = args[0]
    else:
        texto = repr(exc)
    if isinstance(texto, bytes):
        texto = texto.decode("utf-8", errors="replace")
    texto = str(texto)
    if numero is None:
        return texto.strip()
    # O pymssql junta mensagens internas do DB-Lib à mensagem do servidor
    servidor, _, db_lib = texto.partition("DB-Lib error message")
    servidor = servidor.strip()
    if not servidor:
        linhas = [linha.strip() for linha in db_lib.splitlines()[1:]]
        detalhe = "\n".join(dict.fromkeys(linha for linha in linhas
                                          if linha and not linha.startswith("DB-Lib error message")))
        return ("Não foi possível contactar o servidor. Confirme o Host Name (IP,porta), "
                "se o contentor está a correr e se a firewall deixa passar a ligação.\n\n" + detalhe)
    explicacao = _EXPLICACOES.get(numero)
    return f"Erro {numero}: {servidor}" + (f"\n\n{explicacao}" if explicacao else "")


def separar_host(texto):
    """'10.0.0.5,14330' ou '10.0.0.5:14330' -> ('10.0.0.5', '14330'); sem porta usa 1433."""
    texto = texto.strip()
    for separador in (",", ":"):
        if separador in texto:
            host, porta = texto.rsplit(separador, 1)
            return host.strip(), porta.strip()
    return texto, "1433"


def formatar(valor):
    """Valor de uma coluna como texto para as grelhas."""
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S.") + f"{valor.microsecond // 1000:03d}"
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    return str(valor).rstrip()


def _ler_config():
    try:
        return json.loads(_FICHEIRO_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_config(dados):
    try:
        _FICHEIRO_CONFIG.write_text(json.dumps(dados, indent=2), encoding="utf-8")
    except OSError:
        pass  # só se perde o preenchimento automático do login


def _aplicar_nivel(cursor, nivel):
    """SET TRANSACTION ISOLATION LEVEL e devolve o nível que o servidor confirma para a sessão."""
    if nivel not in NIVEIS_ISOLAMENTO:
        raise ValueError(f"Nível de isolamento desconhecido: {nivel}")
    cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {nivel}")
    cursor.execute("SELECT transaction_isolation_level FROM sys.dm_exec_sessions WHERE session_id = @@SPID")
    return _NIVEL_POR_CODIGO.get(cursor.fetchone()[0], "?")


# ---------------------------------------------------------------------------
# Sessão com o SQL Server
# ---------------------------------------------------------------------------

class SessaoBD:
    """Uma ligação ao SQL Server servida por uma thread própria.

    As tarefas (funções que recebem um cursor) correm por ordem na thread da
    sessão. O resultado, ou a exceção, volta à thread do Tkinter através de uma
    fila e é entregue às funções indicadas em `executar`.

    A ligação usa autocommit: cada instrução é uma transação, exceto entre um
    BEGIN TRAN e o COMMIT/ROLLBACK explícitos que as aplicações enviam.
    """

    def __init__(self, raiz, nome_app):
        self.raiz = raiz
        self.nome_app = nome_app
        self.dados = None
        self.conn = None
        self.spid = None
        self.ocupada_desde = None  # instante (time.monotonic) em que a tarefa atual começou
        self._tarefas = queue.Queue()
        self._resultados = queue.Queue()
        self._terminada = False
        self._descartada = False  # depois de um logout, os resultados pendentes são ignorados
        threading.Thread(target=self._ciclo, daemon=True).start()
        self._recolher()

    # -- chamadas a partir da thread do Tkinter --

    def executar(self, tarefa, ao_terminar=None, ao_falhar=None):
        """Agenda tarefa(cursor). ao_terminar(resultado) e ao_falhar(exceção) correm na thread do Tkinter."""
        self._tarefas.put((tarefa, ao_terminar, ao_falhar or self._mostrar_erro))

    def ligar(self, dados, ao_terminar, ao_falhar):
        self.dados = dados
        self.executar(self._abrir_ligacao, ao_terminar, ao_falhar)

    def mudar_nivel(self, nivel, ao_terminar=None, ao_falhar=None):
        self.executar(lambda cursor: _aplicar_nivel(cursor, nivel), ao_terminar, ao_falhar)

    def descricao(self, nivel):
        d = self.dados
        return f"{d['utilizador']}@{d['host']} · {d['base']} · SPID {self.spid} · {nivel}"

    def fechar(self, descartar=False):
        """Termina a thread da sessão e fecha a ligação depois das tarefas já agendadas.

        Com descartar=True, os resultados que ainda cheguem já não são entregues à janela.
        """
        self._descartada = descartar
        self._tarefas.put(None)

    # -- thread da sessão --

    def _ciclo(self):
        while True:
            pedido = self._tarefas.get()
            if pedido is None:
                break
            tarefa, ao_terminar, ao_falhar = pedido
            self.ocupada_desde = time.monotonic()
            try:
                cursor = self.conn.cursor() if self.conn else None
                self._resultados.put((ao_terminar, tarefa(cursor)))
            except Exception as exc:  # qualquer falha é entregue à janela
                self._resultados.put((ao_falhar, exc))
            finally:
                self.ocupada_desde = None
        if self.conn:
            try:
                self.conn.close()
            except pymssql.Error:
                pass
        self._terminada = True

    def _abrir_ligacao(self, _cursor):
        host, porta = separar_host(self.dados["host"])
        self.conn = pymssql.connect(
            server=host,
            port=porta,
            user=self.dados["utilizador"],
            password=self.dados["password"],
            database=self.dados["base"],
            autocommit=True,
            login_timeout=10,
            appname=self.nome_app,  # aparece em sys.dm_exec_sessions.program_name
        )
        cursor = self.conn.cursor()
        cursor.execute("SET NOCOUNT ON")
        cursor.execute("SELECT @@SPID")
        self.spid = cursor.fetchone()[0]
        return _aplicar_nivel(cursor, self.dados["nivel"])

    # -- entrega dos resultados na thread do Tkinter --

    def _recolher(self):
        try:
            while not self._resultados.empty():
                funcao, valor = self._resultados.get_nowait()
                if funcao and not self._descartada:
                    funcao(valor)
        finally:
            if not (self._terminada and self._resultados.empty()):
                try:
                    self.raiz.after(30, self._recolher)
                except tk.TclError:
                    pass  # a janela já foi fechada

    def _mostrar_erro(self, exc):
        messagebox.showerror(self.nome_app, descrever_erro(exc), parent=self.raiz)


# ---------------------------------------------------------------------------
# Componentes da interface
# ---------------------------------------------------------------------------

class SeletorNivel(ttk.Frame):
    """Caixa para mudar o nível de isolamento da sessão (SET TRANSACTION ISOLATION LEVEL).

    A sessão executa um pedido de cada vez: se estiver bloqueada numa consulta, a mudança
    de nível fica na fila e só é aplicada quando essa consulta terminar. Nesse caso aparece
    um aviso ao lado da caixa até o servidor confirmar o novo nível.
    """

    def __init__(self, master, sessao, nivel_inicial, ao_mudar):
        super().__init__(master)
        self.sessao = sessao
        self.ao_mudar = ao_mudar
        self.atual = nivel_inicial
        self.pedidos = 0  # mudanças de nível ainda não confirmadas pelo servidor
        ttk.Label(self, text="Nível de isolamento:").pack(side="left")
        self.var = tk.StringVar(value=nivel_inicial)
        self.caixa = ttk.Combobox(self, textvariable=self.var, values=NIVEIS_ISOLAMENTO,
                                  state="readonly", width=18)
        self.caixa.pack(side="left", padx=(4, 0))
        self.caixa.bind("<<ComboboxSelected>>", self._escolhido)
        self.aviso = ttk.Label(self, foreground="#b45309")
        self.aviso.pack(side="left", padx=(6, 0))

    def ativar(self, sim):
        self.caixa.configure(state="readonly" if sim else "disabled")

    def _escolhido(self, _evento):
        if self.var.get() == self.atual and not self.pedidos:
            return
        self.pedidos += 1
        if self.sessao.ocupada_desde is not None:
            self.aviso.configure(text="só muda quando a consulta em curso terminar")
        self.sessao.mudar_nivel(self.var.get(), self._confirmado, self._falhou)

    def _confirmado(self, nivel):
        self.pedidos -= 1
        self.atual = nivel
        if not self.pedidos:  # com vários pedidos na fila, a caixa mostra o último escolhido
            self.var.set(nivel)
            self.aviso.configure(text="")
        self.ao_mudar(nivel)

    def _falhou(self, exc):
        self.pedidos -= 1
        if not self.pedidos:
            self.var.set(self.atual)
            self.aviso.configure(text="")
        messagebox.showerror("Nível de isolamento", descrever_erro(exc), parent=self)


class BarraEstado(ttk.Frame):
    """Linha no fundo da janela: dados da sessão, aviso quando o servidor demora a responder
    e, se ao_logout for indicado, o botão para terminar a sessão."""

    def __init__(self, master, sessao, nivel, ao_logout=None):
        super().__init__(master, padding=(8, 3))
        self.sessao = sessao
        self.info = ttk.Label(self, text=sessao.descricao(nivel))
        self.info.pack(side="left")
        if ao_logout:
            ttk.Button(self, text="Logout", command=ao_logout).pack(side="right", padx=(8, 0))
        self.espera = ttk.Label(self, foreground="#b45309")
        self.espera.pack(side="right")
        # O ciclo é agendado na janela de topo, que sobrevive ao logout; quando esta barra
        # deixa de existir, o ciclo termina sozinho
        self._topo = self.winfo_toplevel()
        self._atualizar()

    def mostrar_nivel(self, nivel):
        self.info.configure(text=self.sessao.descricao(nivel))

    def _atualizar(self):
        if not self.winfo_exists():
            return
        inicio = self.sessao.ocupada_desde
        decorrido = time.monotonic() - inicio if inicio else 0.0
        self.espera.configure(
            text=f"À espera do servidor há {decorrido:.1f} s (possível bloqueio)" if decorrido > 0.5 else "")
        self._topo.after(200, self._atualizar)


class Refrescamento(ttk.Frame):
    """Botão de refresh e timer automático, com medições para o estudo da taxa de refrescamento.

    criar_tarefa() corre na thread do Tkinter e devolve a função a executar na
    sessão; mostrar(resultado) atualiza a janela com o que essa função devolveu.
    Um tick do timer que chega com o refresh anterior ainda em curso é saltado
    (e contado), para os pedidos não se acumularem na sessão.
    """

    def __init__(self, master, sessao, criar_tarefa, mostrar, intervalo_ms=1000):
        super().__init__(master)
        self.sessao = sessao
        self.criar_tarefa = criar_tarefa
        self.mostrar = mostrar
        self.em_curso = False
        self._inicio = 0.0
        self._duracao = None
        self._hora = None
        self._ultimo_tick = None
        self._intervalos = deque(maxlen=50)
        self._refreshes = 0
        self._saltados = 0
        self._agendado = None
        self._topo = self.winfo_toplevel()  # o timer é agendado aqui (ver BarraEstado)

        controlos = ttk.Frame(self)
        controlos.pack(fill="x")
        ttk.Button(controlos, text="Refresh", command=self.refrescar).pack(side="left")
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(controlos, text="Automático a cada", variable=self.auto,
                        command=self._mudou_auto).pack(side="left", padx=(12, 4))
        self.intervalo = tk.StringVar(value=str(intervalo_ms))
        ttk.Spinbox(controlos, from_=1, to=600000, increment=100, textvariable=self.intervalo,
                    width=8).pack(side="left")
        ttk.Label(controlos, text="ms").pack(side="left", padx=(2, 0))
        self.estado = ttk.Label(self, foreground="#555")
        self.estado.pack(fill="x", pady=(2, 0))

    def iniciar_automatico(self):
        self.auto.set(True)
        self._mudou_auto()

    def refrescar(self):
        if self.em_curso:
            self._saltados += 1
            return
        self.em_curso = True
        self._inicio = time.perf_counter()
        self.sessao.executar(self.criar_tarefa(), self._terminou, self._falhou)

    def _terminou(self, resultado):
        self.em_curso = False
        self._refreshes += 1
        self._duracao = time.perf_counter() - self._inicio
        self._hora = datetime.now()
        self.mostrar(resultado)
        self._mostrar_medicoes()

    def _falhou(self, exc):
        self.em_curso = False
        if self.auto.get():
            self.auto.set(False)
            self._mudou_auto()
        messagebox.showerror("Refresh", descrever_erro(exc), parent=self)

    def _mudou_auto(self):
        if self._agendado:
            self._topo.after_cancel(self._agendado)
            self._agendado = None
        if self.auto.get():
            self._ultimo_tick = None
            self._intervalos.clear()
            self._refreshes = 0
            self._saltados = 0
            self._tick()

    def _tick(self):
        if not self.winfo_exists():  # a janela foi fechada (logout)
            self._agendado = None
            return
        self._agendado = self._topo.after(self._intervalo_ms(), self._tick)
        agora = time.perf_counter()
        if self._ultimo_tick is not None:
            self._intervalos.append(agora - self._ultimo_tick)
        self._ultimo_tick = agora
        self.refrescar()

    def _intervalo_ms(self):
        try:
            return max(1, int(self.intervalo.get()))
        except ValueError:
            return 1000

    def _mostrar_medicoes(self):
        partes = [f"Último refresh {self._hora:%H:%M:%S}.{self._hora.microsecond // 1000:03d}",
                  f"consulta {self._duracao * 1000:.0f} ms"]
        if self.auto.get():
            if self._intervalos:
                media = sum(self._intervalos) / len(self._intervalos)
                partes.append(f"intervalo real médio {media * 1000:.1f} ms")
            partes.append(f"refreshes {self._refreshes} · ticks saltados {self._saltados}")
        self.estado.configure(text=" · ".join(partes))


def criar_grelha(master, colunas, altura=10):
    """Treeview com barra de deslocamento. colunas: lista de (id, título, largura, alinhamento)."""
    quadro = ttk.Frame(master)
    grelha = ttk.Treeview(quadro, columns=[c[0] for c in colunas], show="headings",
                          selectmode="browse", height=altura)
    for ident, titulo, largura, alinhamento in colunas:
        grelha.heading(ident, text=titulo)
        grelha.column(ident, width=largura, anchor=alinhamento, stretch=True)
    barra = ttk.Scrollbar(quadro, orient="vertical", command=grelha.yview)
    grelha.configure(yscrollcommand=barra.set)
    grelha.pack(side="left", fill="both", expand=True)
    barra.pack(side="right", fill="y")
    return quadro, grelha


def preencher_grelha(grelha, linhas, coluna_id=0):
    """Substitui as linhas da grelha, mantendo a seleção (identificada pela coluna coluna_id)."""
    selecionada = grelha.selection()
    grelha.delete(*grelha.get_children())
    for linha in linhas:
        chave = formatar(linha[coluna_id])
        valores = [formatar(v) for v in linha]
        if grelha.exists(chave):  # chave repetida: insere com identificador automático
            grelha.insert("", "end", values=valores)
        else:
            grelha.insert("", "end", iid=chave, values=valores)
    if selecionada and grelha.exists(selecionada[0]):
        grelha.selection_set(selecionada[0])


# ---------------------------------------------------------------------------
# Login e arranque
# ---------------------------------------------------------------------------

class DialogoLogin(tk.Toplevel):
    """Janela de login (Host Name, Database Name, User Name, Password) e escolha do nível de isolamento."""

    def __init__(self, raiz, titulo, nome_app, ao_ligar):
        super().__init__(raiz)
        self.raiz = raiz
        self.nome_app = nome_app
        self.ao_ligar = ao_ligar
        self.sessao = None
        self.title(f"{titulo} — Login")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

        config = _ler_config()
        self.vars = {
            "host": tk.StringVar(value=config.get("host", "localhost,14330")),
            "base": tk.StringVar(value=config.get("base", "MEI_TRAB")),
            "utilizador": tk.StringVar(value=config.get("utilizador", "")),
            "password": tk.StringVar(),
            "nivel": tk.StringVar(value=config.get("nivel", "READ COMMITTED")),
        }

        corpo = ttk.Frame(self, padding=16)
        corpo.pack(fill="both", expand=True)
        entradas = {}
        campos = (("Host Name", "host", ""), ("Database Name", "base", ""),
                  ("User Name", "utilizador", ""), ("Password", "password", "*"))
        for linha, (rotulo, chave, mascara) in enumerate(campos):
            ttk.Label(corpo, text=rotulo).grid(row=linha, column=0, sticky="w", pady=3)
            entradas[chave] = ttk.Entry(corpo, textvariable=self.vars[chave], width=30, show=mascara)
            entradas[chave].grid(row=linha, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Label(corpo, text="Nível de isolamento").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Combobox(corpo, textvariable=self.vars["nivel"], values=NIVEIS_ISOLAMENTO,
                     state="readonly", width=28).grid(row=4, column=1, sticky="ew", pady=3, padx=(8, 0))
        ttk.Label(corpo, text="Host Name no formato IP,porta (ex.: 192.168.1.10,14330)",
                  foreground="#666").grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.aviso = ttk.Label(corpo, foreground="#b45309")
        self.aviso.grid(row=6, column=0, columnspan=2, sticky="w")
        botoes = ttk.Frame(corpo)
        botoes.grid(row=7, column=0, columnspan=2, pady=(12, 0))
        self.botao_ok = ttk.Button(botoes, text="Ok", command=self._ligar)
        self.botao_ok.pack(side="left", padx=4)
        ttk.Button(botoes, text="Cancel", command=self._cancelar).pack(side="left", padx=4)

        self.bind("<Return>", lambda _e: self._ligar())
        self.bind("<Escape>", lambda _e: self._cancelar())
        (entradas["password"] if self.vars["utilizador"].get() else entradas["utilizador"]).focus_set()

    def _ligar(self):
        if self.sessao:
            return  # já está a ligar
        dados = {chave: var.get() if chave == "password" else var.get().strip()
                 for chave, var in self.vars.items()}
        if not (dados["host"] and dados["base"] and dados["utilizador"]):
            self.aviso.configure(text="Preencha o Host Name, a Database Name e o User Name.")
            return
        self.aviso.configure(text="A ligar…")
        self.botao_ok.configure(state="disabled")
        self.sessao = SessaoBD(self.raiz, self.nome_app)
        self.sessao.ligar(dados, lambda nivel: self._ligada(dados, nivel), self._falhou)

    def _ligada(self, dados, nivel):
        _guardar_config({chave: dados[chave] for chave in ("host", "base", "utilizador", "nivel")})
        sessao = self.sessao
        self.destroy()
        self.ao_ligar(sessao, nivel)

    def _falhou(self, exc):
        self.sessao.fechar()
        self.sessao = None
        self.botao_ok.configure(state="normal")
        self.aviso.configure(text="")
        messagebox.showerror("Login", descrever_erro(exc), parent=self)

    def _cancelar(self):
        if self.sessao:
            self.sessao.fechar()
        self.raiz.destroy()


def _preparar_tk():
    """Nitidez em ecrãs de alta resolução (Windows) e um tema ttk decente em Linux."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    raiz = tk.Tk()
    estilo = ttk.Style(raiz)
    if "vista" not in estilo.theme_names():
        estilo.theme_use("clam")
    return raiz


class Aplicacao:
    """Ciclo de vida de uma aplicação: login, janela de trabalho e logout (volta ao login).

    criar_janela(raiz, sessao, nivel, ao_logout) cria a janela de trabalho dentro da raiz.
    """

    def __init__(self, titulo, nome_app, criar_janela, tamanho="900x600"):
        self.titulo = titulo
        self.nome_app = nome_app
        self.criar_janela = criar_janela
        self.raiz = _preparar_tk()
        self.raiz.withdraw()
        self.raiz.title(titulo)
        self.raiz.geometry(tamanho)
        self.sessao = None
        self.janela = None
        self.dialogo = None
        self._mostrar_login()

    def _mostrar_login(self):
        self.dialogo = DialogoLogin(self.raiz, self.titulo, self.nome_app, self._ligada)

    def _ligada(self, sessao, nivel):
        self.dialogo = None
        self.sessao = sessao
        self.janela = self.criar_janela(self.raiz, sessao, nivel, self.logout)
        self.raiz.deiconify()

    def logout(self):
        """Termina a sessão e volta ao login.

        A janela trata antes do que for seu (o Edit faz ROLLBACK de uma transação aberta).
        """
        sessao = self.sessao
        if sessao is None:
            return
        inicio = sessao.ocupada_desde
        if inicio is not None and time.monotonic() - inicio > 0.5:
            messagebox.showwarning(
                self.titulo, "A sessão está à espera do servidor (possível bloqueio) e ainda não pode "
                             "terminar.\nEspere que a consulta termine, ou feche a aplicação: a ligação é "
                             "cortada e o SQL Server desfaz a transação aberta.", parent=self.raiz)
            return
        # Uma tarefa vazia no fim da fila garante que as tarefas já agendadas terminaram
        sessao.executar(lambda _cursor: None, lambda _: self._terminar_sessao(sessao))

    def _terminar_sessao(self, sessao):
        if sessao is not self.sessao:
            return
        sessao.fechar(descartar=True)
        self.sessao = None
        self.janela = None
        for widget in list(self.raiz.winfo_children()):
            widget.destroy()
        self.raiz.protocol("WM_DELETE_WINDOW", self.raiz.destroy)
        self.raiz.withdraw()
        self._mostrar_login()


def iniciar_aplicacao(titulo, nome_app, criar_janela, tamanho="900x600"):
    """Mostra o login e, depois de ligado, a janela de trabalho; o botão Logout volta ao login."""
    Aplicacao(titulo, nome_app, criar_janela, tamanho).raiz.mainloop()
