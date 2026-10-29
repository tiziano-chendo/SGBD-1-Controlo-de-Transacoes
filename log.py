"""Aplicação Log: as N linhas mais recentes de LogOperations com eventos I, U e D."""

import tkinter as tk
from tkinter import ttk

from comum import BarraEstado, Refrescamento, SeletorNivel, criar_grelha, iniciar_aplicacao, preencher_grelha

SQL_LOG = ("SELECT TOP ({n}) NumReg, EventType, Objecto, Valor, Referencia, UserID, TerminalD, "
           "TerminalName, DCriacao FROM LogOperations WHERE EventType IN ('I', 'U', 'D') ORDER BY NumReg DESC")


class JanelaLog:
    def __init__(self, raiz, sessao, nivel, ao_logout=None):
        topo = ttk.Frame(raiz, padding=(8, 8, 8, 4))
        topo.pack(fill="x")
        self.barra = BarraEstado(raiz, sessao, nivel, ao_logout)
        self.barra.pack(fill="x", side="bottom")
        SeletorNivel(topo, sessao, nivel, self._nivel_mudou).pack(side="left", anchor="n")
        ttk.Label(topo, text="N =").pack(side="left", padx=(24, 4), anchor="n")
        self.var_n = tk.StringVar(value="20")
        ttk.Spinbox(topo, from_=1, to=1000, textvariable=self.var_n, width=6).pack(side="left", anchor="n")
        self.refresh = Refrescamento(topo, sessao, self._tarefa, self._mostrar, intervalo_ms=2000)
        self.refresh.pack(side="left", padx=(24, 0), fill="x", expand=True)

        quadro, self.grelha = criar_grelha(raiz, [("NumReg", "NumReg", 70, "e"),
                                                  ("EventType", "Tipo", 45, "center"),
                                                  ("Objecto", "Objecto", 90, "w"),
                                                  ("Valor", "Valor", 220, "w"),
                                                  ("Referencia", "Referência", 180, "w"),
                                                  ("UserID", "UserID", 80, "w"),
                                                  ("TerminalD", "TerminalD", 80, "e"),
                                                  ("TerminalName", "TerminalName", 140, "w"),
                                                  ("DCriacao", "DCriacao", 180, "w")], altura=18)
        quadro.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.refresh.iniciar_automatico()

    def _nivel_mudou(self, nivel):
        self.barra.mostrar_nivel(nivel)
        self.refresh.refrescar()  # mostra logo os dados vistos com o novo nível

    def _tarefa(self):
        try:
            n = min(1000, max(1, int(self.var_n.get())))
        except ValueError:
            n = 20
        sql = SQL_LOG.format(n=n)

        def ler(cursor):
            cursor.execute(sql)
            return cursor.fetchall()

        return ler

    def _mostrar(self, registos):
        preencher_grelha(self.grelha, registos)


if __name__ == "__main__":
    iniciar_aplicacao("TP1 · Log", "TP1-Log", JanelaLog, tamanho="1160x560")
