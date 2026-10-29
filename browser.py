"""Aplicação Browser: encomendas (EncID decrescente) e linhas da encomenda selecionada.

Tem refresh manual e automático (timer). Cada SELECT corre em autocommit, ou
seja, numa transação própria: os trincos de leitura duram só a consulta, mesmo
em REPEATABLE READ ou SERIALIZABLE.
"""

from tkinter import ttk

from comum import BarraEstado, Refrescamento, SeletorNivel, criar_grelha, iniciar_aplicacao, preencher_grelha

SQL_ENCOMENDAS = "SELECT EncID, ClienteID, Nome, Morada FROM Encomenda ORDER BY EncID DESC"
SQL_LINHAS = "SELECT EncId, ProdutoID, Designacao, Preco, Qtd FROM EncLinha WHERE EncId = %s ORDER BY ProdutoID"


def ler_linhas(cursor, enc_id):
    cursor.execute(SQL_LINHAS, (enc_id,))
    return cursor.fetchall()


class JanelaBrowser:
    def __init__(self, raiz, sessao, nivel, ao_logout=None):
        self.sessao = sessao
        self.enc_linhas = None  # EncID cujas linhas estão na grelha de baixo

        topo = ttk.Frame(raiz, padding=(8, 8, 8, 4))
        topo.pack(fill="x")
        self.barra = BarraEstado(raiz, sessao, nivel, ao_logout)
        self.barra.pack(fill="x", side="bottom")
        SeletorNivel(topo, sessao, nivel, self._nivel_mudou).pack(side="left", anchor="n")
        self.refresh = Refrescamento(topo, sessao, self._tarefa_refresh, self._mostrar)
        self.refresh.pack(side="left", padx=(24, 0), fill="x", expand=True)

        paineis = ttk.PanedWindow(raiz, orient="vertical")
        paineis.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        caixa_enc = ttk.LabelFrame(paineis, text="Encomendas", padding=4)
        quadro, self.grelha_enc = criar_grelha(caixa_enc, [("EncID", "EncID", 70, "e"),
                                                           ("ClienteID", "ClienteID", 80, "e"),
                                                           ("Nome", "Nome", 240, "w"),
                                                           ("Morada", "Morada", 240, "w")])
        quadro.pack(fill="both", expand=True)
        self.caixa_lin = ttk.LabelFrame(paineis, text="Linhas da encomenda selecionada", padding=4)
        quadro, self.grelha_lin = criar_grelha(self.caixa_lin, [("EncId", "EncId", 70, "e"),
                                                                ("ProdutoID", "ProdutoID", 80, "e"),
                                                                ("Designacao", "Designação", 240, "w"),
                                                                ("Preco", "Preço", 100, "e"),
                                                                ("Qtd", "Qtd", 100, "e")], altura=5)
        quadro.pack(fill="both", expand=True)
        paineis.add(caixa_enc, weight=3)
        paineis.add(self.caixa_lin, weight=2)

        self.grelha_enc.bind("<<TreeviewSelect>>", self._selecionou)
        self.refresh.refrescar()

    def _nivel_mudou(self, nivel):
        self.barra.mostrar_nivel(nivel)
        self.refresh.refrescar()  # mostra logo os dados vistos com o novo nível

    def _enc_selecionada(self):
        selecao = self.grelha_enc.selection()
        return int(selecao[0]) if selecao else None

    def _tarefa_refresh(self):
        enc_id = self._enc_selecionada()

        def refrescar(cursor):
            cursor.execute(SQL_ENCOMENDAS)
            encomendas = cursor.fetchall()
            linhas = ler_linhas(cursor, enc_id) if enc_id is not None else []
            return encomendas, enc_id, linhas

        return refrescar

    def _mostrar(self, resultado):
        encomendas, enc_id, linhas = resultado
        preencher_grelha(self.grelha_enc, encomendas)
        if enc_id is not None and not self.grelha_enc.exists(str(enc_id)):
            enc_id, linhas = None, []  # a encomenda selecionada foi apagada entretanto
        self._mostrar_linhas(enc_id, linhas)

    def _selecionou(self, _evento):
        enc_id = self._enc_selecionada()
        if enc_id is None or enc_id == self.enc_linhas:
            return
        self.sessao.executar(lambda cursor: ler_linhas(cursor, enc_id),
                             lambda linhas: self._mostrar_linhas(enc_id, linhas))

    def _mostrar_linhas(self, enc_id, linhas):
        self.enc_linhas = enc_id
        titulo = f"Linhas da encomenda {enc_id}" if enc_id is not None else "Linhas da encomenda selecionada"
        self.caixa_lin.configure(text=titulo)
        preencher_grelha(self.grelha_lin, linhas, coluna_id=1)


if __name__ == "__main__":
    iniciar_aplicacao("TP1 · Browser", "TP1-Browser", JanelaBrowser, tamanho="900x620")
