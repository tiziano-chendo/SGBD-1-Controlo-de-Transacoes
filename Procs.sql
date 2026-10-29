-------------------------------------------------------------------------------
------------------------ CREATE SOME Stored Procedure -------------------------
--                            and use them...
-------------------------------------------------------------------------------


-------------------------------------------------------------------------------
-- USE MEI_TRAB: Changes the database context to the MEI_TRAB database.
--
USE MEI_TRAB
--
-------------------------------------------------------------------------------

IF EXISTS (SELECT name FROM sysobjects 
      WHERE name = 'INSERIR_ENCOMENDA' AND type = 'P')
   DROP PROCEDURE INSERIR_ENCOMENDA
GO

-- Insere uma nova encomenda

CREATE PROCEDURE INSERIR_ENCOMENDA
AS 
  DECLARE @EncId int
  
  DECLARE @MAX_TRY int
  DECLARE @NTry int

  SET @MAX_TRY = 20;    -- Max number of times to try

  SET @NTry = 1;     

  -- try to insert the new Encomenda
  WHILE (@NTry <= @MAX_TRY)
  BEGIN 
    Set @NTry = @NTry +1;   
     
    -- get next EncId
    Select @EncId = Max(EncId) From Encomenda;     

    IF (@EncId IS NULL)
      SET @EncId = 1;
    ELSE
      SET @EncId = @EncId +1;

    -- Inserir  encomenda
    INSERT INTO Encomenda Values (@EncId, 1000, 'Fernando Pessoa', 'Lisboa');

    IF (@@ERROR = 0)    -- Success!
	BEGIN
	  -- Inserir linhas da encomenda 
      INSERT INTO EncLinha Values (@EncId, 111, 'Mensagem', 2500, 2);
      INSERT INTO EncLinha Values (@EncId, 131, 'Livro do Desassossego', 3000, 1);
	
      BREAK  -- Sucesso, hora de sair!
	END
  END  -- Fim de ciclo
    
  IF ( @NTry > @MAX_TRY)
  BEGIN
    RAISERROR('Can''t insert the record',16,1 )
    ROLLBACK TRANSACTION
  END
GO


IF EXISTS (SELECT name FROM sysobjects 
      WHERE name = 'APAGAR_ENCOMENDA' AND type = 'P')
   DROP PROCEDURE APAGAR_ENCOMENDA
GO

-- Apaga uma encomenda

CREATE PROCEDURE APAGAR_ENCOMENDA
AS 
  DECLARE @EncId int
  
  DECLARE @N_Linhas int
  
  -- Obter a quantidade de encomendas
  Select @N_Linhas  = Count (*) From Encomenda
  
  DECLARE @Random INT, @Upper INT, @Lower INT
  
  -- Limites para a geração de números aleatórios
  Set @Lower = 1;   -- Menor valor
  Set @Upper = @N_Linhas; -- Maior valor
  
  -- Escolher aleatóriamente a linha a apagar
  Set @Random = ROUND(((@Upper - @Lower -1)* RAND() + @Lower), 0);
  
  -- Obter o ID da encomenda na linha "Random"
  Select @EncId = EncId
  from ( SELECT Row_Number() Over (Order By EncId) as N_Linha, [EncID]
         FROM Encomenda) AS X
  Where N_LInha = @Random

  Delete From EncLinha Where EncId = @EncId
  Delete From Encomenda Where EncId = @EncId
  
GO


-- Actualiza uma encomenda
IF EXISTS (SELECT name FROM sysobjects 
      WHERE name = 'ACTUALIZAR_ENCOMENDA' AND type = 'P')
   DROP PROCEDURE ACTUALIZAR_ENCOMENDA
GO

CREATE PROCEDURE ACTUALIZAR_ENCOMENDA
AS 
  DECLARE @EncId int
  
  DECLARE @N_Linhas int
  
  -- Obter a quantidade de encomendas
  Select @N_Linhas  = Count (*) From Encomenda
  
  DECLARE @Random INT, @Upper INT, @Lower INT
  
  -- Limites para a geração de números aleatórios
  Set @Lower = 1;   -- Menor valor
  Set @Upper = @N_Linhas; -- Maior valor
  
  -- Escolher aleatóriamente a linha a encomendar
  Set @Random = ROUND(((@Upper - @Lower -1)* RAND() + @Lower), 0);
  
  -- Obter o ID da encomenda na linha "Random"
  Select @EncId = EncId
  from ( SELECT Row_Number() Over (Order By EncId) as N_Linha, [EncID]
         FROM Encomenda) AS X
  Where N_LInha = @Random

  -- Alterar o campo Morada para o instante corrente 
  Update Encomenda 
    Set Morada = CONVERT(varchar,GETDATE(),121)
  Where EncId = @EncId
  
GO