
USE master


DECLARE @DBName sysname
DECLARE @DataPath nvarchar(200)
DECLARE @DataFileName nvarchar(200)
DECLARE @BasePath nvarchar(200)
DECLARE @LogPath nvarchar(200)
DECLARE @LogFileName nvarchar(200)

DECLARE @SQLString nvarchar(max)

-- Nome e caminho para a BD
SET @DBName = 'MEI_TRAB'


-- Apagar a Base de dados
DECLARE @SQLDropDB nvarchar(max)
IF ( EXISTS( SELECT * FROM [dbo].[sysdatabases] Where name = @DBName) )
Begin
  Set @SQLDropDB = 'USE '+ @DBName + ' 
  DROP DATABASE '+ @DBName

  -- print (@SQLDropDB) -- Uncomment to see the DDL Command
  Exec(@SQLDropDB)
end


SET @BasePath = '/var/opt/mssql/data/TBD/'    -- Caminho para a base de dados. Alterar se necessário!!!

SET @DataPath = @BasePath + @DBName +'/'
SET @LogPath  = @BasePath + @DBName +'/'

-- Criar as directorias (pastas)
SET NOCOUNT ON;
DECLARE @DirectoryExists int;

EXEC master.dbo.xp_fileexist @DataPath, @DirectoryExists OUT;
IF @DirectoryExists = 0
   EXEC master.sys.xp_create_subdir @DataPath;

EXEC master.dbo.xp_fileexist @LogPath, @DirectoryExists OUT;
IF @DirectoryExists = 0
   EXEC master.sys.xp_create_subdir @LogPath;
   

-- Criar a Base de dados

-- Nome dos ficheiros de dados e log
SET @DataFileName = @DataPath +'Trab2dat.mdf' 
SET @LogFileName  = @LogPath  +'Trab2log.ldf' 


SET @SQLString = 'CREATE DATABASE ' + @DBName +
  ' ON 
   ( NAME = ''Trab1_dat'',
      FILENAME ='''+ @DataFileName + ''',      
      SIZE = 10,
      MAXSIZE = 50,
      FILEGROWTH = 5 )
   LOG ON
   ( NAME = ''Trab1_log'',
     FILENAME ='''+ @LogFileName + ''',
     SIZE = 5MB,
     MAXSIZE = 25MB,
     FILEGROWTH = 5MB )'

 SET NOCOUNT OFF;


-------------------------------------------------------------------------------
--
-- Se não existir a BD então vamos criá-la...
-- (if not exists the database then create them)
--
IF (NOT EXISTS( SELECT * FROM [dbo].[sysdatabases] Where name = @DBName) )
Begin
  --print (@SQLString)
  exec(@SQLString)
end



-- Criar as tabelas na base de dados recém-criada

-------------------------------------------------------------------------------
-- Criar as tabelas
-- (create the database tables)
-------------------------------------------------------------------------------

-- Tabela de Encomenda

SET @SQLString = 
   'CREATE TABLE Encomenda (                               -- Não está em 3FN!!!
	  EncID int NOT NULL CHECK (EncID >= 1),                   
	  ClienteID int NOT NULL CHECK (ClienteID >= 1),                   
      Nome nvarchar(50) NOT NULL,                        -- Nome cliente
      Morada nvarchar(30) NOT NULL  DEFAULT ''Covilhã'',   -- Morada cliente
    
    CONSTRAINT PK_Encomenda PRIMARY KEY (EncID) -- Chave primária
   )'


SET @SQLString = 'USE '+ @DBName + 
                  ' if not exists (select * from dbo.sysobjects  where id = object_id(N''[dbo].[Encomenda]''))  begin '+ 
				      @SQLString +' end'

EXEC ( @SQLString)    


-- Linhas da encomenda / produtos encomendados

SET @SQLString = 
   'CREATE TABLE EncLinha (                       
	  EncId int NOT NULL,
	  ProdutoID int NOT NULL,
	  
	  Designacao nvarchar (50) NOT NULL ,                                  -- Designação produto            
	  Preco decimal(10,2) NOT NULL  DEFAULT 10.0   CHECK (Preco >= 0.0),
	  Qtd decimal(10,2) NOT NULL  DEFAULT 1.0   CHECK (Qtd >= 0.0),         -- Qtd produto
	  
	  
	  
	  CONSTRAINT PK_EncLinha
	    PRIMARY KEY (EncId, ProdutoID),           -- constraint type: primary key
	  
	  
	  CONSTRAINT FK_EncId FOREIGN KEY (EncId) 
	     REFERENCES Encomenda(EncId)
	     ON UPDATE CASCADE 
	     ON DELETE NO ACTION
  )' 
SET @SQLString = 'USE '+ @DBName + 
                  ' if not exists (select * from dbo.sysobjects  where id = object_id(N''[dbo].[EncLinha]''))  begin '+ 
				      @SQLString +' end'

EXEC ( @SQLString)    
  

-- Tabela de Log
  
-- https://docs.microsoft.com/en-us/sql/t-sql/statements/create-table-transact-sql-identity-property
SET @SQLString = 
  'CREATE TABLE LogOperations (
    NumReg int IDENTITY(1,1),       -- Auto increment
	EventType char(1),              -- I, U, D (Insert, Update, Delete)
	
    -- Log
    Objecto     varchar(30),
    Valor       varchar(100),
    Referencia  varchar(100),
	
	-- Dados sobre o utilizador e posto de trabalho
    UserID nvarchar(30) NOT NULL DEFAULT USER_NAME(), 
    TerminalD      nvarchar(30) NOT NULL  DEFAULT HOST_ID(),
	TerminalName   nvarchar(30) NOT NULL  DEFAULT HOST_NAME(),
	
	-- Quanto ocorreu a operação
    DCriacao datetime NOT NULL DEFAULT GetDate(),
   
    CONSTRAINT PK_LogOperations PRIMARY KEY (NumReg)
  )'
  
SET @SQLString = 'USE '+ @DBName + 
                  ' if not exists (select * from dbo.sysobjects  where id = object_id(N''[dbo].[LogOperations]''))  begin '+ 
				      @SQLString +' end'

EXEC ( @SQLString)    
  

