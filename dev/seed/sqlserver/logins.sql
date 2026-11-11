-- Author: @SaadZarook
-- Server logins, named and passworded by seed.sh. Its defaults are in dev/README.md.
IF SUSER_ID('$(RO_LOGIN)') IS NULL
    CREATE LOGIN [$(RO_LOGIN)] WITH PASSWORD = '$(RO_PASSWORD)', CHECK_POLICY = OFF;
IF SUSER_ID('$(RW_LOGIN)') IS NULL
    CREATE LOGIN [$(RW_LOGIN)] WITH PASSWORD = '$(RW_PASSWORD)', CHECK_POLICY = OFF;
GO
