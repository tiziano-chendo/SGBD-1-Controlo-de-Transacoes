# TP1 — Controlo de Transações: preparação para a apresentação

O essencial do enunciado, as decisões do nosso trabalho e o que observámos nos testes, com as perguntas que o professor provavelmente vai fazer.

---

## 1. O trabalho em 30 segundos

- **Objetivo do enunciado:** perceber, na prática, os **níveis de isolamento**, os **tipos de trincos** e os **deadlocks**, e saber explicar o que acontece aos dados quando várias transações correm ao mesmo tempo.
- **O que construímos:** quatro aplicações (Edit, Browser, Log, Log Tempo) em Python/Tkinter sobre SQL Server.
- **Como testámos:**
  - três computadores contra a mesma base de dados (`MEI_TRAB`, num contentor Docker);
  - o `doWork.sql` a gerar alterações em simultâneo;
  - um monitor que registou ligações, transações, bloqueios e deadlocks.
- **O que mostrámos:** *lost update*, leitura suja, leitura não repetível, fantasmas, bloqueios de leitura e de escrita, deadlock, conflito em SNAPSHOT e rollback automático quando a rede caiu.

---

## 2. Conceitos que temos de dominar

### 2.1 Transação e ACID

| Propriedade | Significado | Como o SQL Server a garante |
|---|---|---|
| **A**tomicidade | tudo ou nada | log de transações: um `ROLLBACK` desfaz as alterações a partir do log |
| **C**onsistência | as regras (PK, FK, CHECK) continuam válidas | restrições e triggers |
| **I**solamento | transações concorrentes não se veem a meio | trincos (*locks*) ou versões das linhas (SNAPSHOT) |
| **D**urabilidade | o que foi confirmado não se perde | *Write-Ahead Logging*: o log é escrito em disco antes do `COMMIT` terminar |

### 2.2 Fenómenos de concorrência (e onde os vimos)

| Fenómeno | O que é | Onde o vimos |
|---|---|---|
| **Leitura suja** (*dirty read*) | ler dados que outra transação alterou e ainda não confirmou | Bruno em READ UNCOMMITTED leu o 8888 do Tiziano antes do COMMIT |
| **Leitura não repetível** | a mesma transação lê o mesmo dado duas vezes e obtém valores diferentes | Tiziano em READ COMMITTED leu 2.00, e o Bruno confirmou 3.00 com a transação do Tiziano ainda aberta. Um Reler mostraria 3.00, mas **não chegámos a registar essa segunda leitura**: convém mostrá-la na demonstração |
| **Fantasma** (*phantom*) | aparecem linhas novas que satisfazem a mesma condição | teste automático: em REPEATABLE READ, outra sessão inseriu uma linha na encomenda e o Reler passou de 2 para 3 linhas |
| ***Lost update*** | duas transações leem, ambas escrevem, e a última apaga a outra sem aviso | em READ COMMITTED, a quantidade confirmada pelo Bruno foi apagada pela do Tiziano 0,4 s depois |

### 2.3 Níveis de isolamento no SQL Server

| Nível | Leitura suja | Não repetível | Fantasma | Como funciona |
|---|---|---|---|---|
| READ UNCOMMITTED | possível | possível | possível | as leituras não pedem trincos S |
| **READ COMMITTED** (o nível por omissão) | evita | possível | possível | trinco S **só durante a leitura** |
| REPEATABLE READ | evita | evita | possível | trinco S **até ao fim da transação** |
| SERIALIZABLE | evita | evita | evita | S até ao fim e **trincos de intervalo** (*key-range*) |
| SNAPSHOT | evita | evita | evita | lê versões confirmadas no início da transação, sem trincos S; conflito de escrita dá o erro **3960** |

**A ideia mais importante:** o nível de isolamento só muda o comportamento das **leituras**. Em qualquer nível, as **escritas** pedem um trinco **X**, que dura até ao fim da transação. Por isso, em READ UNCOMMITTED um UPDATE continua a ficar à espera de outro UPDATE.

**SNAPSHOT e RCSI não são a mesma coisa:**
- O **SNAPSHOT** dá uma vista da base de dados no início da **transação** e deteta conflitos (erro 3960). Tem de ser ativado com `ALLOW_SNAPSHOT_ISOLATION ON`.
- O **RCSI** (`READ_COMMITTED_SNAPSHOT ON`) transforma o READ COMMITTED: cada **instrução** lê a última versão confirmada, sem bloquear. Não deteta conflitos de escrita.

### 2.4 Trincos

**Modos:**
- **S** (partilhado): para ler.
- **X** (exclusivo): para escrever.
- **U** (atualização): o UPDATE usa-o enquanto procura a linha e só depois o converte em X. Evita que duas sessões, com S na mesma linha, fiquem as duas presas a tentar subir para X.
- **IS, IX e SIX** (intenção): ficam na tabela e na página e indicam que há trincos S ou X mais abaixo. Assim o servidor não precisa de verificar linha a linha antes de dar um trinco à tabela inteira.
- **RangeS-S, RangeI-N**: trincos de intervalo, só no SERIALIZABLE.
- **Sch-S, Sch-M**: trincos de esquema.

**Compatibilidade:**

| Pedido \ Concedido | S | U | X |
|---|---|---|---|
| **S** | ✅ | ✅ | ❌ |
| **U** | ✅ | ❌ | ❌ |
| **X** | ❌ | ❌ | ❌ |

**Granularidade:** linha (`KEY`, porque todas as nossas tabelas têm chave primária agrupada), página (`PAGE`) e tabela (`OBJECT`). Com cerca de **5 000 trincos** no mesmo objeto, o SQL Server tenta fazer *lock escalation* para um trinco na tabela inteira.

**Exemplo real** (`sys.dm_tran_locks`) de um Edit depois de Aplicar uma quantidade:
- `KEY` em `EncLinha`: **X** (a linha alterada);
- `KEY` em `LogOperations`: **X** (a linha inserida pelo trigger);
- `PAGE` e `OBJECT`: **IX** (trincos de intenção).

### 2.5 Bloqueio ≠ deadlock

| | Bloqueio | Deadlock |
|---|---|---|
| O que é | A espera por B | A espera por B **e** B espera por A (ciclo) |
| Como acaba | quando B termina | o SQL Server **escolhe uma vítima** e desfaz a transação dela (erro **1205**) |
| Quanto tempo | pode durar para sempre (vimos 8 minutos) | o *lock monitor* procura ciclos a cada **5 s**; medimos ~5 s |

- **Escolha da vítima:** a sessão com menor `DEADLOCK_PRIORITY`. Em caso de empate, a que tem menos trabalho para desfazer (menos log).
- **Como evitar deadlocks:**
  - aceder às tabelas sempre pela mesma ordem;
  - transações curtas;
  - `UPDLOCK` na leitura (pede logo o trinco U);
  - SNAPSHOT;
  - repetir a transação quando aparece o erro 1205.

### 2.6 Recuperação

- **Log de transações (WAL):** cada alteração é escrita primeiro no log, e o `COMMIT` só termina quando o log está em disco.
- **ROLLBACK:** usa o log para desfazer as alterações.
- **Arranque depois de uma falha:** o SQL Server faz três fases:
  - **análise** do log;
  - **redo**, para refazer o que estava confirmado;
  - **undo**, para desfazer o que não estava.
- **A ligação cai a meio de uma transação:** o servidor desfaz a transação. Aconteceu-nos às 17:10, quando a rede caiu.
- **Os números IDENTITY não voltam atrás num rollback.** Os buracos no `NumReg` mostram transações desfeitas que tinham escrito no log.

---

## 3. As decisões do nosso trabalho (e como as justificar)

**Porque é que o Edit mantém a transação aberta enquanto o utilizador edita?**
O enunciado pede-o: início da transação, leitura, alterações e fim da transação. Só assim se veem os trincos, os bloqueios e os deadlocks. Num sistema real seria um anti-padrão, porque prende trincos durante o tempo de reflexão do utilizador. Em produção usar-se-ia **concorrência otimista**: ler sem trincos e, ao gravar, verificar se os dados mudaram (com uma coluna `rowversion`, que aqui não podíamos acrescentar porque a estrutura das tabelas é fixa).

**Porque é que os registos `'O'` do Edit estão fora da transação?**
Para medir o tempo de edição mesmo quando o utilizador descarta. Dentro da transação, o ROLLBACK apagava-os.

**Qual é o nível "apropriado" para o Edit?**
Depende do que se quer evitar:
- **READ COMMITTED** permite *lost update* (vimo-lo duas vezes).
- **REPEATABLE READ** evita o *lost update*, mas troca-o por **deadlocks** quando dois utilizadores gravam a mesma linha.
- **SNAPSHOT** não bloqueia leituras e deteta o conflito (erro 3960): o segundo a gravar é avisado.
- **READ COMMITTED com `UPDLOCK` na leitura** põe os editores em fila, sem deadlock.

A nossa escolha: **SNAPSHOT** ou **READ COMMITTED + `UPDLOCK`**. O SNAPSHOT foi confirmado na prática no servidor da aula: o segundo a gravar recebeu o erro 3960, em vez de apagar a alteração do primeiro (ver secção 4).

**Porque é que o Browser lê em autocommit?**
Cada refresh é uma transação curta. Assim, em REPEATABLE READ e SERIALIZABLE, o Browser **não prende trincos entre refreshes**: só se distingue do READ COMMITTED durante a própria consulta.

**Porque é que as janelas não congelam quando estão bloqueadas?**
Cada aplicação tem uma ligação própria e corre as instruções numa thread à parte. A barra de estado mostra "À espera do servidor há X s" e o SPID.

**Como sabemos que o nível de isolamento foi mesmo aplicado?**
Depois do `SET TRANSACTION ISOLATION LEVEL`, a aplicação lê o nível da própria sessão em `sys.dm_exec_sessions` e mostra-o na barra de estado.

**Porque é que a referência do Edit tem o SPID (`G1-…-S58`)?**
Nos testes, duas edições abertas no mesmo milissegundo geraram referências iguais, o que estragava o Log Tempo. O SPID torna-as únicas.

**Porque é que o Log Tempo usa a hora do servidor?**
O relógio do PC do Tiziano estava **~9 s atrasado** em relação ao servidor. A duração das edições tem de ser calculada com um único relógio.

**Porque é que cada pessoa tem o seu login (`aluno1` a `aluno3`)?**
A coluna `UserID` usa `USER_NAME()`, e qualquer login de administrador (como o `sa`) aparece como `dbo`. O `TerminalName` (`HOST_NAME()`) identifica o computador.

**É possível ter um timer de 1 ms? (pergunta do enunciado)**
Pode configurar-se, mas medimos **~15 ms** reais, que é a resolução do relógio do Windows. Além disso, cada refresh demora ~40 ms, por isso a maioria dos ticks chega com o anterior ainda em curso. Abaixo da duração da consulta só se gera carga.

**Que taxa de refresh recomendamos?**
**1–2 s.** Com o doWork a fazer 30 alterações em 15 s, o Browser viu 29 a 0,5 s, 7 a 2 s e 2 a 5 s. O atraso máximo dos dados é o intervalo mais a duração da consulta. Refresh mais rápido significa mais carga e, em READ COMMITTED, mais bloqueios.

---

## 4. O que observámos (provas para mostrar)

**Testes reais com três computadores** (22/10, `monitorizacao/eventos.log`):

| Hora | O que aconteceu |
|---|---|
| 16:49:42 | ***Lost update*** em READ COMMITTED: o Bruno confirmou uma quantidade e, 0,4 s depois, o Tiziano escreveu por cima (ficou 10.00) |
| 16:50:21 → 16:51:06 | **Bloqueio de escrita** (`LCK_M_X`, ~45 s): o Bruno esperou pela linha que o Tiziano tinha alterado e não confirmado |
| 16:51:06 → 16:52:09 | **Segundo *lost update***, mesmo com o bloqueio pelo meio: o bloqueio só ordenou as escritas |
| 16:51:48 → 16:52:09 | **Bloqueio de leitura** (`LCK_M_S`, ~20 s): abrir uma encomenda não confirmada em READ COMMITTED |
| 16:53:56 | **Leitura suja** em READ UNCOMMITTED: o Bruno leu o 8888 do Tiziano sem esperar |
| 17:10:14 | **Queda de rede**: a transação aberta do Bruno foi desfeita automaticamente |
| ~18:10 | Um Edit com uma alteração por confirmar bloqueou **três sessões**, incluindo o Browser do próprio utilizador. A que esperou mais tempo esteve ~8 minutos à espera |

**No servidor da aula** (base de dados `SGBD_PL1_01`, com SNAPSHOT ativo):
- Um Edit em **SNAPSHOT** tentou gravar uma linha da `EncLinha` que outra transação tinha **alterado e confirmado** depois de ele começar.
- O SQL Server devolveu o erro **3960** (*update conflict*) e **abortou a transação**, em vez de deixar acontecer o *lost update*.
- A aplicação explicou o erro, fechou o registo `'O'` e indicou que bastava abrir a encomenda de novo para repetir a alteração sobre os valores atuais.

Comparem com o READ COMMITTED: no mesmo cenário (16:49:42), a segunda gravação apagou a primeira **sem nenhum aviso**. É a diferença entre controlo de concorrência pessimista (trincos) e otimista (versões): no SNAPSHOT ninguém espera, mas quem confirma em segundo lugar é avisado e tem de repetir.

**Testes automáticos** (`relatorio_testes.md`):
- **Deadlock** em REPEATABLE READ entre dois Edit, detetado em ~5 s, com uma vítima.
- **Fantasmas** em REPEATABLE READ; em SERIALIZABLE, o INSERT da outra sessão ficou bloqueado.
- **SNAPSHOT:** erro 3952 quando está desligado e conflito 3960 quando está ligado.
- **Edit contra o doWork:** o doWork ficou bloqueado em READ COMMITTED e não ficou em READ UNCOMMITTED.
- **Dois doWork sem pausa:** 71 colisões no `MAX+1` em READ COMMITTED; deadlock em SERIALIZABLE, que **terminou** o doWork vítima.

**Pormenor que convém saber explicar:** quando várias sessões esperam pelo mesmo recurso, o `blocking_session_id` pode apontar para **outra sessão que também está à espera** (a fila é por ordem de chegada). A sessão com o trinco está no fim da cadeia, e o nosso monitor segue essa cadeia.

---

## 5. Perguntas prováveis (com resposta curta)

1. **Porque é que o Browser em READ COMMITTED ficou bloqueado?**
   Precisa de um trinco S em cada linha que lê, e uma delas tinha um trinco X do Edit, que só é libertado no COMMIT ou ROLLBACK.

2. **Em READ UNCOMMITTED nunca há esperas?**
   Há. As leituras não esperam, mas as **escritas** pedem sempre um trinco X.

3. **Qual é a diferença entre REPEATABLE READ e SERIALIZABLE?**
   Os fantasmas. O SERIALIZABLE usa trincos de intervalo, que impedem inserções no intervalo lido.

4. **Que nível evita o *lost update*?**
   O REPEATABLE READ (resolve-o com bloqueio ou deadlock) e o SNAPSHOT (com o erro 3960, que vimos no servidor da aula). Também se evita com `UPDLOCK` ou com concorrência otimista. O READ COMMITTED **não** o evita.

5. **Para que serve o trinco U?**
   Evita deadlocks de conversão: só uma sessão de cada vez pode estar "a caminho" de X na mesma linha.

6. **Porque aparece IX na tabela se só alterei uma linha?**
   É um trinco de intenção. Avisa que há trincos X mais abaixo e impede que outra sessão tranque a tabela inteira.

7. **Como é que o SQL Server deteta e resolve um deadlock?**
   O *lock monitor* procura ciclos de espera a cada 5 s, escolhe uma vítima (prioridade e custo do rollback), desfaz a transação dela e devolve o erro 1205.

8. **Porque é que um deadlock "mata" o doWork?**
   O erro 1205 aborta o batch inteiro, e o ciclo `WHILE` do doWork termina. Pode evitar-se com `SET DEADLOCK_PRIORITY HIGH` ou com `TRY/CATCH` e nova tentativa.

9. **Porque é que a app Log fica bloqueada se ninguém está a editar o log?**
   Os triggers inserem em `LogOperations` **dentro da transação** de quem altera os dados. Essas linhas ficam com trinco X até ao fim da transação.

10. **Porque há números em falta no `NumReg`?**
    O IDENTITY não volta atrás: uma transação desfeita "gasta" números.

11. **O que acontece se a aplicação ou a rede falharem a meio de uma transação?**
    O servidor deteta que a ligação caiu e faz ROLLBACK. Nada fica gravado a meio (atomicidade).

12. **Porque é que o doWork nunca altera nem apaga a encomenda mais recente?**
    A fórmula aleatória `ROUND((@Upper - @Lower - 1) * RAND() + @Lower, 0)` só dá valores de 1 a N−1. A correta seria `FLOOR((@Upper - @Lower + 1) * RAND()) + @Lower`.

13. **Para que serve o ciclo de 20 tentativas do `INSERIR_ENCOMENDA`?**
    O `MAX(EncId)+1` tem uma condição de corrida: duas sessões calculam o mesmo número e uma recebe o erro 2627. A solução robusta seria `IDENTITY` ou `SEQUENCE`.

14. **O tratamento de erros do doWork está correto?**
    É frágil. O `@@ERROR` depois do `EXEC` só vê a última instrução do procedimento, e o `ROLLBACK` dentro do procedimento causa os erros 266 e 3903. Seria melhor `TRY/CATCH` com `SET XACT_ABORT ON`.

15. **Que nível escolheriam para cada aplicação?**

    | Aplicação | Nível | Porquê |
    |---|---|---|
    | Edit | SNAPSHOT ou READ COMMITTED + `UPDLOCK` | ver a pergunta 4 |
    | Browser | SNAPSHOT ou RCSI | dados confirmados sem bloquear (READ UNCOMMITTED só se uma leitura suja for aceitável) |
    | Log | igual ao Browser | idem |
    | doWork | READ COMMITTED | — |

16. **Como descobriram quem bloqueava quem?**
    Com `sys.dm_exec_requests` (`blocking_session_id`, `wait_type`) e `sys.dm_tran_locks` (modo e estado dos trincos), e com o monitor que regista tudo com hora. Para saber a linha exata: `%%lockres%%`.

17. **O que é o WAL e porque é importante?**
    O log é escrito em disco antes dos dados. Permite desfazer transações (rollback) e recuperar depois de uma falha (redo/undo).

18. **Há escalonamento de trincos nos vossos testes?**
    Não: cada transação toca em poucas linhas, muito abaixo dos ~5 000 trincos.

---

## 6. Sugestões de melhoria (o enunciado pede-as)

**Nos scripts do professor:**
- `TRY/CATCH` e `XACT_ABORT` no doWork;
- corrigir a fórmula aleatória;
- `IDENTITY` ou `SEQUENCE` em vez de `MAX+1`;
- triggers com `INSERT … SELECT FROM inserted` em vez de cursores;
- corrigir as variáveis dos triggers de delete: `@Nome nvarchar(30)` corta nomes de 50 caracteres, e a conversão de `Qtd` para `nvarchar(10)` dá overflow acima de 10 milhões;
- os blocos `on_error` nunca são executados.

**Nas aplicações:**
- concorrência otimista no Edit;
- notificações (*push*) em vez de *polling* no Browser (`SqlDependency` / Service Broker);
- `LOCK_TIMEOUT` configurável.

**No enunciado:**
- a coluna chama-se `TerminalD` e não `TerminalId`;
- a query do Log Tempo está presa a uma referência fixa;
- o formato da data em `Valor` deve ser ISO 8601, para o `DATEDIFF` não depender das definições de idioma.

---

## 7. Demonstração ao vivo (roteiro de ~5 minutos)

Preparação: SSMS com as consultas de monitorização abertas (como `sa`), e dois Edit e um Browser em computadores diferentes.

1. **Bloqueio:** o Edit A muda a morada e carrega em **Aplicar**. O Browser (READ COMMITTED) faz refresh e fica "À espera do servidor". No SSMS mostra-se o `blocking_session_id` e o trinco X em `sys.dm_tran_locks`. O Edit A faz **Guardar**, e o Browser desbloqueia.
2. **Leitura suja:** repete-se com o Browser em **READ UNCOMMITTED**: não bloqueia e mostra a morada por confirmar. Depois o Edit A faz **Descartar**.
3. ***Lost update*:** os Edit A e B abrem a mesma encomenda em READ COMMITTED. O A muda a quantidade e guarda; o B muda a mesma quantidade e guarda. A alteração do A desaparece.
4. **Deadlock:** repete-se o passo 3 em **REPEATABLE READ**, carregando primeiro em Aplicar nos dois. Ao fim de ~5 s, um deles recebe o erro 1205.
