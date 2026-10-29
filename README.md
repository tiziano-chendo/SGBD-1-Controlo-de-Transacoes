# Aplicações do TP1 — Controlo de Transações

Quatro aplicações em Python + Tkinter sobre a base de dados `MEI_TRAB` (SQL Server).

| Ficheiro | Aplicação |
|---|---|
| `edit.py` | Edit: altera a morada e as quantidades de uma encomenda dentro de uma transação |
| `browser.py` | Browser: encomendas e linhas, com refresh manual e por timer |
| `log_tempo.py` | Log Tempo: tempo de cada edição (registos `'O'`) |
| `log.py` | Log: as N linhas mais recentes de `LogOperations` (I/U/D) |
| `comum.py` | Código partilhado (login, sessão, nível de isolamento, refresh) |

## Instalação

**Windows**

```powershell
python -m pip install -r requirements.txt
```

**Linux (Ubuntu/Debian)**

```bash
sudo apt install python3-tk python3-venv
python3 -m venv ~/venv-tp1
~/venv-tp1/bin/pip install -r requirements.txt
```

Em Linux, corram as aplicações com `~/venv-tp1/bin/python` em vez de `python`.

## Correr

Na pasta `apps`:

```
python edit.py
python browser.py
python log_tempo.py
python log.py
```

Cada aplicação pede o login ao arrancar:

| Campo | Valor |
|---|---|
| Host Name | `IP,14330` (ex.: `192.168.1.10,14330`; no PC do servidor: `localhost,14330`) |
| Database Name | `MEI_TRAB` |
| User Name / Password | `aluno1`, `aluno2` ou `aluno3` |
| Nível de isolamento | o nível inicial; pode ser mudado depois na janela |

Os últimos valores (sem a password) ficam guardados em `~/.mei_trab_login.json`.

## Barra de estado

No fundo de cada janela aparecem o utilizador, a base de dados, o **SPID** da sessão e o nível de isolamento confirmado pelo servidor.

Quando uma instrução demora mais de 0,5 s, aparece **"À espera do servidor há X s (possível bloqueio)"**. É assim que se vê uma sessão bloqueada por um trinco de outra. A janela continua a responder, porque as instruções correm numa thread à parte.

O botão **Logout**, à direita da barra, termina a sessão e volta à janela de login (por exemplo, para entrar com outro utilizador ou noutro servidor):
- No Edit, se houver uma transação aberta, pede confirmação e faz ROLLBACK antes de sair.
- Se a sessão estiver bloqueada à espera do servidor, o logout só é possível quando a consulta terminar. Em alternativa, fecha-se a aplicação: a ligação é cortada e o SQL Server desfaz a transação aberta.

## Edit

| Botão | Instrução | Passo do enunciado (4.1) |
|---|---|---|
| Abrir | registo `'O'` + `BEGIN TRAN` + `SELECT` | 2, 3, 4 |
| Aplicar | `UPDATE` das alterações; a transação continua aberta | 5 |
| Reler | `SELECT` dentro da mesma transação (útil para leituras não repetíveis e fantasmas) | — |
| Guardar | aplica o que estiver pendente + `COMMIT` + registo `'O'` final | 5, 6, 7 |
| Descartar | `ROLLBACK` + registo `'O'` final | 6, 7 |

- **Aplicar sem Guardar** mantém os trincos exclusivos (X) enquanto o utilizador decide. É a situação de "encomenda não confirmada" pedida nos testes.
- **Nível de isolamento:** só pode ser mudado sem transação aberta.
- **Validação local:** a morada tem de ter de 1 a 30 caracteres. A quantidade vai de 0 a 9 999 999,99, porque acima disso o trigger `TR_EncLinha_D` falha.
- **Transação desfeita pelo servidor:** se o servidor a desfizer (deadlock `1205`, conflito em SNAPSHOT `3960`), a aplicação explica o que aconteceu e escreve o registo `'O'` final.
- **Referência:** tem o formato `G1-AAAAMMDDhhmmssmmm-S<SPID>`. O prefixo do grupo está em `GRUPO`, no `comum.py`.

## Browser, Log e Log Tempo

- **Refresh** faz uma leitura imediata; **Automático a cada … ms** liga o timer.
- **Medições:** por baixo dos controlos aparecem a duração da consulta, o intervalo real médio entre ticks e os ticks saltados (que chegaram com o refresh anterior ainda em curso). É o material para o estudo da taxa de refrescamento.
  - Medido no portátil do servidor: com o timer a 1 ms, o intervalo real médio ficou em ~15 ms, a resolução do relógio do Windows.
- **Transações das leituras:** cada `SELECT` corre em autocommit, ou seja, numa transação própria. Em REPEATABLE READ e SERIALIZABLE, os trincos de leitura duram só a consulta.
- **Mudar de nível:** a aplicação faz logo um refresh com o nível novo. Se a sessão estiver bloqueada numa consulta, a mudança fica na fila e só é aplicada quando essa consulta terminar. Enquanto isso, aparece o aviso "só muda quando a consulta em curso terminar".

## Monitorizar (no SSMS, como `sa`)

```sql
-- Sessões das aplicações e quem bloqueia quem
SELECT s.session_id, s.program_name, s.host_name, s.login_name,
       r.blocking_session_id, r.wait_type, r.wait_time
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
WHERE s.program_name LIKE 'TP1-%';

-- Trincos ativos na MEI_TRAB
SELECT request_session_id, resource_type, resource_description, request_mode, request_status
FROM sys.dm_tran_locks
WHERE resource_database_id = DB_ID('MEI_TRAB')
ORDER BY request_session_id;
```

O `program_name` é `TP1-Edit`, `TP1-Browser`, `TP1-Log` ou `TP1-LogTempo`, e o `session_id` é o SPID que aparece na barra de estado.

## SNAPSHOT

Para usar o nível SNAPSHOT, a base de dados tem de o permitir:

```sql
ALTER DATABASE MEI_TRAB SET ALLOW_SNAPSHOT_ISOLATION ON;
```

Sem isto, a primeira leitura dá o erro 3952, e a aplicação explica o motivo.
