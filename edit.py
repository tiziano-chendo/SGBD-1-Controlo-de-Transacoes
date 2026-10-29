"""Aplicação Edit: alterar a morada e as quantidades de uma encomenda.

A transação fica aberta enquanto o utilizador edita, para se poderem observar
trincos, bloqueios e deadlocks entre sessões concorrentes. Sequência (enunciado, 4.1):

  1-2. registo 'O' inicial em LogOperations (fora da transação)
  3-4. BEGIN TRAN + leitura do cabeçalho e das linhas
  5.   Aplicar: UPDATE das alterações (a transação continua aberta)
  6.   Guardar = COMMIT (aplica antes o que estiver pendente) · Descartar = ROLLBACK
  7.   registo 'O' final, com a mesma referência
"""

import time
import tkinter as tk
from datetime import datetime
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk

from comum import (GRUPO, BarraEstado, SeletorNivel, criar_grelha, descrever_erro, formatar,
                   iniciar_aplicacao, preencher_grelha)

SQL_REGISTO_O = ("INSERT INTO LogOperations (EventType, Objecto, Valor, Referencia) "
                 "VALUES ('O', %s, CONVERT(varchar(23), GETDATE(), 126), %s)")
SQL_CABECALHO = "SELECT EncID, ClienteID, Nome, Morada FROM Encomenda WHERE EncID = %s"
SQL_LINHAS = "SELECT ProdutoID, Designacao, Preco, Qtd FROM EncLinha WHERE EncId = %s ORDER BY ProdutoID"
# @@ROWCOUNT logo a seguir ao UPDATE diz se a linha ainda existia
SQL_MUDAR_MORADA = ("DECLARE @n int; UPDATE Encomenda SET Morada = %s WHERE EncID = %s; "
                    "SET @n = @@ROWCOUNT; SELECT @n")
SQL_MUDAR_QTD = ("DECLARE @n int; UPDATE EncLinha SET Qtd = %s WHERE EncId = %s AND ProdutoID = %s; "
                 "SET @n = @@ROWCOUNT; SELECT @n")

MORADA_MAX = 30                  # Morada nvarchar(30) NOT NULL
QTD_MAX = Decimal("9999999.99")  # acima disto o trigger TR_EncLinha_D falha ao converter Qtd para nvarchar(10)


class TransacaoPerdida(Exception):
    pass


def nova_referencia(spid):
    """Ex.: G1-20261022143005123-S55. O SPID distingue edições iniciadas no mesmo milissegundo."""
    agora = datetime.now()
    return f"{GRUPO}-{agora:%Y%m%d%H%M%S}{agora.microsecond // 1000:03d}-S{spid}"


def ler_encomenda(cursor, enc_id):
    cursor.execute(SQL_CABECALHO, (enc_id,))
    cabecalho = cursor.fetchone()
    cursor.execute(SQL_LINHAS, (enc_id,))
    return cabecalho, cursor.fetchall()


def aplicar_alteracoes(cursor, enc_id, morada, qtds):
    """Envia os UPDATE pendentes e devolve o que já não existia na base de dados."""
    em_falta = []
    if morada is not None:
        cursor.execute(SQL_MUDAR_MORADA, (morada, enc_id))
        if cursor.fetchone()[0] == 0:
            em_falta.append(f"encomenda {enc_id}")
    for produto, qtd in qtds.items():
        cursor.execute(SQL_MUDAR_QTD, (qtd, enc_id, produto))
        if cursor.fetchone()[0] == 0:
            em_falta.append(f"linha do produto {produto}")
    return em_falta


def transacoes_abertas(cursor):
    cursor.execute("SELECT @@TRANCOUNT")
    return cursor.fetchone()[0]


class JanelaEdit:
    def __init__(self, raiz, sessao, nivel, ao_logout=None):
        self.raiz = raiz
        self.sessao = sessao
        self.enc_id = None
        self.referencia = None
        self.registo_pendente = None  # referência cujo registo 'O' final ainda não foi escrito
        self.commit_feito = False     # o COMMIT da transação atual já foi executado
        self.aberta_desde = None      # instante do BEGIN TRAN; None = sem transação
        self.ocupada = False          # há uma tarefa desta janela a correr na sessão
        self.ao_logout = ao_logout
        self.depois_de_terminar = None  # sair ou fazer logout quando a transação terminar
        self.linhas = []              # (ProdutoID, Designacao, Preco) como foram lidas
        self.morada_bd = None         # morada na base de dados (vista por esta transação)
        self.qtds_bd = {}             # ProdutoID -> Qtd na base de dados
        self.qtds_novas = {}          # ProdutoID -> Qtd alterada no ecrã, ainda por aplicar
        self.mensagem = "Sem transação aberta."

        raiz.protocol("WM_DELETE_WINDOW", self._ao_fechar)

        topo = ttk.Frame(raiz, padding=(8, 8, 8, 4))
        topo.pack(fill="x")
        self.barra = BarraEstado(raiz, sessao, nivel, self._logout if ao_logout else None)
        self.barra.pack(fill="x", side="bottom")
        self.seletor = SeletorNivel(topo, sessao, nivel, self.barra.mostrar_nivel)
        self.seletor.pack(side="left")

        abrir = ttk.Frame(raiz, padding=(8, 4))
        abrir.pack(fill="x")
        ttk.Label(abrir, text="EncId:").pack(side="left")
        self.var_enc = tk.StringVar()
        self.entrada_enc = ttk.Entry(abrir, textvariable=self.var_enc, width=10)
        self.entrada_enc.pack(side="left", padx=(4, 8))
        self.entrada_enc.bind("<Return>", lambda _e: self._abrir())
        self.btn_abrir = ttk.Button(abrir, text="Abrir (BEGIN TRAN + SELECT)", command=self._abrir)
        self.btn_abrir.pack(side="left")

        cab = ttk.LabelFrame(raiz, text="Encomenda", padding=8)
        cab.pack(fill="x", padx=8, pady=4)
        self.lbl_cab = {}
        for coluna, (rotulo, chave) in enumerate((("EncID", "enc"), ("ClienteID", "cliente"), ("Nome", "nome"))):
            ttk.Label(cab, text=f"{rotulo}:").grid(row=0, column=2 * coluna, sticky="w")
            self.lbl_cab[chave] = ttk.Label(cab, text="—", width=18)
            self.lbl_cab[chave].grid(row=0, column=2 * coluna + 1, sticky="w", padx=(4, 16))
        ttk.Label(cab, text="Morada:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_morada = tk.StringVar()
        self.entrada_morada = ttk.Entry(cab, textvariable=self.var_morada, width=36)
        self.entrada_morada.grid(row=1, column=1, columnspan=3, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Label(cab, text=f"(máx. {MORADA_MAX} caracteres)", foreground="#666").grid(
            row=1, column=4, columnspan=2, sticky="w", pady=(6, 0))

        lin = ttk.LabelFrame(raiz, text="Linhas  (* = quantidade alterada, ainda por aplicar)", padding=8)
        lin.pack(fill="both", expand=True, padx=8, pady=4)
        quadro, self.grelha = criar_grelha(lin, [("ProdutoID", "ProdutoID", 90, "e"),
                                                 ("Designacao", "Designação", 260, "w"),
                                                 ("Preco", "Preço", 100, "e"),
                                                 ("Qtd", "Qtd", 110, "e")], altura=6)
        quadro.pack(fill="both", expand=True)
        self.grelha.bind("<<TreeviewSelect>>", self._linha_selecionada)
        qtd = ttk.Frame(lin)
        qtd.pack(fill="x", pady=(6, 0))
        ttk.Label(qtd, text="Nova quantidade da linha selecionada:").pack(side="left")
        self.var_qtd = tk.StringVar()
        self.entrada_qtd = ttk.Entry(qtd, textvariable=self.var_qtd, width=12)
        self.entrada_qtd.pack(side="left", padx=4)
        self.entrada_qtd.bind("<Return>", lambda _e: self._mudar_qtd())
        self.btn_qtd = ttk.Button(qtd, text="Mudar", command=self._mudar_qtd)
        self.btn_qtd.pack(side="left")

        acoes = ttk.Frame(raiz, padding=(8, 4))
        acoes.pack(fill="x")
        self.btn_reler = ttk.Button(acoes, text="Reler (SELECT)", command=self._reler)
        self.btn_aplicar = ttk.Button(acoes, text="Aplicar (UPDATE)", command=self._aplicar)
        self.btn_guardar = ttk.Button(acoes, text="Guardar (COMMIT)", command=lambda: self._terminar(True))
        self.btn_descartar = ttk.Button(acoes, text="Descartar (ROLLBACK)", command=lambda: self._terminar(False))
        for botao in (self.btn_reler, self.btn_aplicar, self.btn_guardar, self.btn_descartar):
            botao.pack(side="left", padx=(0, 6))
        self.lbl_transacao = ttk.Label(raiz, padding=(8, 2), foreground="#1d4ed8")
        self.lbl_transacao.pack(fill="x")

        self._atualizar_controlos()
        self._relogio()
        self.entrada_enc.focus_set()

    # -- passos da transação --

    def _abrir(self):
        if self.aberta_desde is not None or self.ocupada:
            return
        try:
            enc_id = int(self.var_enc.get())
            if enc_id < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Edit", "Indique um EncId válido (inteiro ≥ 1).", parent=self.raiz)
            return
        self.enc_id = enc_id
        self.referencia = referencia = nova_referencia(self.sessao.spid)
        self.commit_feito = False

        def iniciar(cursor):
            cursor.execute(SQL_REGISTO_O, (str(enc_id), referencia))  # 2) fora da transação
            self.registo_pendente = referencia
            cursor.execute("BEGIN TRAN")                                # 3)

        self._ocupar()
        self.sessao.executar(iniciar, self._iniciada, self._falhou)

    def _iniciada(self, _):
        self.aberta_desde = time.monotonic()
        enc_id = self.enc_id
        self.sessao.executar(lambda cursor: ler_encomenda(cursor, enc_id),  # 4)
                             lambda dados: self._lida(dados, primeira=True), self._falhou)

    def _reler(self):
        morada, qtds = self._pendentes()
        if (morada is not None or qtds) and not messagebox.askyesno(
                "Edit", "As alterações ainda não aplicadas perdem-se. Reler mesmo assim?", parent=self.raiz):
            return
        enc_id = self.enc_id
        self._ocupar()
        self.sessao.executar(lambda cursor: ler_encomenda(cursor, enc_id),
                             lambda dados: self._lida(dados, primeira=False), self._falhou)

    def _lida(self, dados, primeira):
        self._libertar()
        cabecalho, linhas = dados
        if cabecalho is None:
            if primeira:
                messagebox.showinfo("Edit", f"A encomenda {self.enc_id} não existe "
                                            "(ou não é visível neste nível de isolamento).", parent=self.raiz)
                self._terminar(False)
                return
            messagebox.showwarning("Edit", f"A encomenda {self.enc_id} já não existe: foi apagada por "
                                           "outra transação. A transação continua aberta.", parent=self.raiz)
        self._mostrar(cabecalho, linhas)

    def _aplicar(self):
        morada, qtds = self._pendentes()
        if morada is None and not qtds:
            messagebox.showinfo("Edit", "Não há alterações por aplicar.", parent=self.raiz)
            return
        if not self._morada_valida(morada):
            return
        enc_id = self.enc_id
        self._ocupar()
        self.sessao.executar(lambda cursor: aplicar_alteracoes(cursor, enc_id, morada, qtds),  # 5)
                             lambda em_falta: self._aplicadas(morada, qtds, em_falta), self._falhou)

    def _aplicadas(self, morada, qtds, em_falta):
        self._libertar()
        if morada is not None:
            self.morada_bd = morada
        for produto, qtd in qtds.items():
            self.qtds_bd[produto] = qtd
            self.qtds_novas.pop(produto, None)
        self._mostrar_linhas()
        self._avisar_em_falta(em_falta)

    def _terminar(self, confirmar):
        morada, qtds = self._pendentes() if confirmar else (None, {})
        if not self._morada_valida(morada):
            return
        enc_id, referencia = self.enc_id, self.referencia

        def terminar(cursor):
            em_falta = []
            if confirmar:
                if transacoes_abertas(cursor) == 0:
                    raise TransacaoPerdida("A transação já não estava aberta (o SQL Server desfê-la). "
                                           "Nada foi guardado.")
                em_falta = aplicar_alteracoes(cursor, enc_id, morada, qtds)
                cursor.execute("COMMIT TRAN")                             # 6)
                self.commit_feito = True
            else:
                cursor.execute("IF @@TRANCOUNT > 0 ROLLBACK TRAN")        # 6)
            cursor.execute(SQL_REGISTO_O, (str(enc_id), referencia))     # 7) fora da transação
            self.registo_pendente = None
            return em_falta

        self._ocupar()
        self.sessao.executar(terminar, lambda em_falta: self._terminada(confirmar, em_falta), self._falhou)

    def _terminada(self, confirmada, em_falta):
        self._libertar()
        hora = datetime.now().strftime("%H:%M:%S")
        acao = "guardada (COMMIT)" if confirmada else "descartada (ROLLBACK)"
        self._limpar(f"Encomenda {self.enc_id}: transação {acao} às {hora}.")
        self._avisar_em_falta(em_falta)
        self._seguir()

    # -- erros --

    def _falhou(self, exc):
        # Primeiro pergunta ao servidor se a transação sobreviveu ao erro; depois mostra o erro
        self.sessao.executar(transacoes_abertas, self._sincronizar, self._falhou_sincronizar)
        messagebox.showerror("Edit", descrever_erro(exc), parent=self.raiz)

    def _sincronizar(self, abertas):
        self._libertar()
        if abertas > 0:
            if self.aberta_desde is None:
                self.aberta_desde = time.monotonic()
            self.depois_de_terminar = None  # a transação continua aberta: não sai nem faz logout
            self._atualizar_controlos()
            return
        if self.registo_pendente:
            # A transação terminou (desfeita pelo servidor por deadlock, conflito em SNAPSHOT, ...,
            # ou confirmada mas sem o registo final): fecha o registo 'O' no log
            enc_id, referencia = self.enc_id, self.registo_pendente
            self.registo_pendente = None
            self.sessao.executar(lambda cursor: cursor.execute(SQL_REGISTO_O, (str(enc_id), referencia)))
        if self.commit_feito:
            self._limpar(f"Encomenda {self.enc_id}: transação guardada (COMMIT).")
        else:
            self._limpar(f"Encomenda {self.enc_id}: a transação terminou sem ser guardada.")
        self._seguir()

    def _falhou_sincronizar(self, exc):
        self._libertar()
        messagebox.showerror("Edit", "Não foi possível confirmar o estado da transação.\n\n"
                             + descrever_erro(exc), parent=self.raiz)

    # -- edição no ecrã --

    def _mostrar(self, cabecalho, linhas):
        if cabecalho is None:
            for label in self.lbl_cab.values():
                label.configure(text="—")
            self.morada_bd = None
            self.var_morada.set("")
        else:
            enc, cliente, nome, morada = cabecalho
            self.lbl_cab["enc"].configure(text=str(enc))
            self.lbl_cab["cliente"].configure(text=str(cliente))
            self.lbl_cab["nome"].configure(text=nome)
            self.morada_bd = morada
            self.var_morada.set(morada)
        self.linhas = [(produto, designacao, preco) for produto, designacao, preco, _ in linhas]
        self.qtds_bd = {produto: qtd for produto, _, _, qtd in linhas}
        self.qtds_novas = {}
        self.var_qtd.set("")
        self._mostrar_linhas()

    def _mostrar_linhas(self):
        linhas = []
        for produto, designacao, preco in self.linhas:
            if produto in self.qtds_novas:
                qtd = f"{formatar(self.qtds_novas[produto])} *"
            else:
                qtd = formatar(self.qtds_bd[produto])
            linhas.append((produto, designacao, preco, qtd))
        preencher_grelha(self.grelha, linhas)
        self._atualizar_controlos()

    def _linha_selecionada(self, _evento):
        selecao = self.grelha.selection()
        if selecao:
            produto = int(selecao[0])
            self.var_qtd.set(formatar(self.qtds_novas.get(produto, self.qtds_bd.get(produto))))

    def _mudar_qtd(self):
        selecao = self.grelha.selection()
        if not selecao:
            messagebox.showinfo("Edit", "Selecione primeiro uma linha.", parent=self.raiz)
            return
        qtd = self._ler_qtd(self.var_qtd.get())
        if qtd is None:
            return
        produto = int(selecao[0])
        if qtd == self.qtds_bd[produto]:
            self.qtds_novas.pop(produto, None)
        else:
            self.qtds_novas[produto] = qtd
        self._mostrar_linhas()

    def _ler_qtd(self, texto):
        try:
            qtd = Decimal(texto.strip().replace(",", "."))
            if not qtd.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            messagebox.showwarning("Edit", "Quantidade inválida.", parent=self.raiz)
            return None
        if qtd != qtd.quantize(Decimal("0.01")):
            messagebox.showwarning("Edit", "A quantidade só pode ter duas casas decimais.", parent=self.raiz)
            return None
        if not Decimal(0) <= qtd <= QTD_MAX:
            messagebox.showwarning("Edit", f"A quantidade tem de estar entre 0 e {QTD_MAX}.", parent=self.raiz)
            return None
        return qtd.quantize(Decimal("0.01"))

    def _pendentes(self):
        """(nova morada ou None, {ProdutoID: nova Qtd}) ainda não enviados para a base de dados."""
        if self.morada_bd is None:
            return None, {}
        morada = self.var_morada.get()
        return (morada if morada != self.morada_bd else None), dict(self.qtds_novas)

    def _morada_valida(self, morada):
        if morada is None:
            return True
        if not morada.strip():
            messagebox.showwarning("Edit", "A morada não pode ficar vazia.", parent=self.raiz)
            return False
        if len(morada) > MORADA_MAX:
            messagebox.showwarning("Edit", f"A morada tem {len(morada)} caracteres; o máximo é {MORADA_MAX}.",
                                   parent=self.raiz)
            return False
        return True

    def _avisar_em_falta(self, em_falta):
        if em_falta:
            messagebox.showwarning("Edit", "O UPDATE não encontrou: " + ", ".join(em_falta)
                                   + ".\nForam apagadas por outra transação?", parent=self.raiz)

    # -- estado da janela --

    def _ocupar(self):
        self.ocupada = True
        self._atualizar_controlos()

    def _libertar(self):
        self.ocupada = False
        self._atualizar_controlos()

    def _limpar(self, mensagem):
        self.aberta_desde = None
        self.mensagem = mensagem
        self._mostrar(None, [])

    def _atualizar_controlos(self):
        aberta = self.aberta_desde is not None
        livre = not self.ocupada
        com_dados = aberta and livre and self.morada_bd is not None

        def estado(sim):
            return "normal" if sim else "disabled"

        self.btn_abrir.configure(state=estado(livre and not aberta))
        self.entrada_enc.configure(state=estado(not aberta and livre))
        for botao in (self.btn_reler, self.btn_guardar, self.btn_descartar):
            botao.configure(state=estado(aberta and livre))
        self.btn_aplicar.configure(state=estado(com_dados))
        for widget in (self.entrada_morada, self.entrada_qtd, self.btn_qtd):
            widget.configure(state=estado(com_dados))
        self.seletor.ativar(livre and not aberta)

    def _relogio(self):
        if not self.lbl_transacao.winfo_exists():  # a janela foi fechada (logout)
            return
        if self.aberta_desde is not None:
            morada, qtds = self._pendentes()
            n = len(qtds) + (morada is not None)
            texto = (f"Transação aberta há {time.monotonic() - self.aberta_desde:.0f} s · "
                     f"encomenda {self.enc_id} · referência {self.referencia}")
            if n:
                texto += f" · {n} alteração(ões) por aplicar"
        else:
            texto = self.mensagem
        self.lbl_transacao.configure(text=texto)
        self.raiz.after(500, self._relogio)

    def _ao_fechar(self):
        if self.ocupada:
            if messagebox.askyesno("Edit", "A sessão está à espera do servidor. Fechar mesmo assim?\n"
                                   "A ligação é cortada e o SQL Server desfaz a transação aberta.",
                                   parent=self.raiz):
                self.raiz.destroy()
            return
        if self.aberta_desde is None:
            self.raiz.destroy()
            return
        if messagebox.askyesno("Edit", "Há uma transação aberta. Descartar (ROLLBACK) e sair?", parent=self.raiz):
            self.depois_de_terminar = self.raiz.destroy
            self._terminar(False)

    def _logout(self):
        if self.ocupada:
            messagebox.showinfo("Edit", "Espere que a operação em curso termine antes de terminar a sessão.",
                                parent=self.raiz)
            return
        if self.aberta_desde is None:
            self.ao_logout()
            return
        if messagebox.askyesno("Edit", "Há uma transação aberta. Descartar (ROLLBACK) e terminar a sessão?",
                               parent=self.raiz):
            self.depois_de_terminar = self.ao_logout
            self._terminar(False)

    def _seguir(self):
        """Executa o que ficou à espera do fim da transação: fechar a janela ou fazer logout."""
        acao, self.depois_de_terminar = self.depois_de_terminar, None
        if acao:
            acao()


if __name__ == "__main__":
    iniciar_aplicacao("TP1 · Edit", "TP1-Edit", JanelaEdit, tamanho="760x600")
