USE MEI_TRAB

DECLARE @startTime DateTime2(0) = GetDate();

-- Timeout after 15 min
DECLARE @endTime DateTime2(0) = DateAdd(MINUTE, 15, @startTime);

DECLARE @Random INT, @Upper INT, @Lower INT

-- Limites para a geração de números aleatórios
Set @Lower = 1;   -- Menor valor
Set @Upper = 100; -- Maior valor

-- Loop until timeout (or BREAK is called)
WHILE (GetDate() < @endTime)
BEGIN
  -- Gerar número aleatório entre 1 e 100
  Set @Random = ROUND(((@Upper - @Lower -1)* RAND() + @Lower), 0);
  
  -- Faz uma operação de acordo com o valor do número gerado
  --  40% Insert; 40% Update; 20% Delete
  BEGIN TRAN
  IF @Random < 40  -- 40% inserir
    EXEC INSERIR_ENCOMENDA
  ELSE IF @Random < 80 -- 40% 
    EXEC ACTUALIZAR_ENCOMENDA
  ELSE
    EXEC APAGAR_ENCOMENDA
      
  IF (@@ERROR = 0)    -- Success!
    COMMIT
  ELSE
    ROLLBACK

  -- Wait 15 seconds
  WAITFOR DELAY  '00:00:15';
END

