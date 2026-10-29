"""Aplicação Log Tempo: tempo de edição de cada encomenda, a partir dos registos 'O' do Edit.

Cada edição deixa dois registos 'O' com a mesma referência (início e fim).
Generaliza a consulta do enunciado: mostra todas as edições, e as que ainda
não têm registo final aparecem como "em edição".
"""

import tkinter as tk
from tkinter import ttk

from comum import BarraEstado, Refrescamento, SeletorNivel, criar_grelha, iniciar_aplicacao, preencher_grelha

# Valor guarda a hora do servidor em ISO 8601 (CONVERT estilo 126), que o DATEDIFF converte sem ambiguidade
SQL_TEMPOS = """
SELECT I.UserID, I.Objecto AS EncId, DATEDIFF(SECOND, I.Valor, F.Valor) AS Tempo,
       I.Referencia, I.Valor AS Inicio
FROM LogOperations I
LEFT JOIN LogOperations F
       ON F.EventType = 'O' AND F.Referencia = I.Referencia AND F.NumReg > I.NumReg
WHERE I.EventType = 'O'
  AND NOT EXISTS (SELECT 1 FROM LogOperations A
                  WHERE A.EventType = 'O' AND A.Referencia = I.Referencia AND A.NumReg < I.NumReg)
ORDER BY I.NumReg DESC
"""


class JanelaLogTempo:
    def __init__(self, raiz, sessao, nivel, ao_logout=None):
        topo = ttk.Frame(raiz, padding=(8, 8, 8, 4))
        topo.pack(fill="x")
        self.barra = BarraEstado(raiz, sessao, nivel, ao_logout)
        self.barra.pack(fill="x", side="bottom")
        SeletorNivel(topo, sessao, nivel, self._nivel_mudou).pack(side="left", anchor="n")
        self.refresh = Refrescamento(topo, sessao, lambda: self._ler, self._mostrar, intervalo_ms=2000)
        self.refresh.pack(side="left", padx=(24, 0), fill="x", expand=True)

        quadro, self.grelha = criar_grelha(raiz, [("UserId", "UserId", 100, "w"),
                                                  ("EncId", "EncId", 70, "e"),
                                                  ("Tempo", "Tempo (s)", 90, "e"),
                                                  ("Referencia", "Referência", 200, "w"),
                                                  ("Inicio", "Início", 180, "w")])
        quadro.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.total = tk.StringVar()
        ttk.Label(raiz, textvariable=self.total, padding=(8, 0)).pack(fill="x")

        self.refresh.iniciar_automatico()

    def _nivel_mudou(self, nivel):
        self.barra.mostrar_nivel(nivel)
        self.refresh.refrescar()  # mostra logo os dados vistos com o novo nível

    @staticmethod
    def _ler(cursor):
        cursor.execute(SQL_TEMPOS)
        return cursor.fetchall()

    def _mostrar(self, edicoes):
        linhas = [(utilizador, enc, "em edição" if tempo is None else tempo, referencia, inicio)
                  for utilizador, enc, tempo, referencia, inicio in edicoes]
        preencher_grelha(self.grelha, linhas, coluna_id=3)
        em_curso = sum(1 for linha in edicoes if linha[2] is None)
        self.total.set(f"{len(edicoes)} edições · {em_curso} em curso")


if __name__ == "__main__":
    iniciar_aplicacao("TP1 · Log Tempo", "TP1-LogTempo", JanelaLogTempo, tamanho="760x480")
