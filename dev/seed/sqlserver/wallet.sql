-- Author: @SaadZarook
-- Creates and seeds one copy of the wallet database. Run with -v DB=wallet or -v DB=wallet_uat.
-- Fake data only. Relative times keep the "stuck" top-ups pending for 28, 22 and 14 minutes.
IF DB_ID('$(DB)') IS NULL CREATE DATABASE [$(DB)];
GO
USE [$(DB)];
GO

CREATE TABLE dbo.currencies (
    code char(3) NOT NULL PRIMARY KEY,
    name varchar(50) NOT NULL,
    minor_units tinyint NOT NULL
);

CREATE TABLE dbo.wallets (
    user_id varchar(20) NOT NULL PRIMARY KEY,
    currency char(3) NOT NULL REFERENCES dbo.currencies (code),
    balance decimal(12,2) NOT NULL
);

CREATE TABLE dbo.user_provider_ids (
    user_id varchar(20) NOT NULL,
    provider varchar(30) NOT NULL,
    provider_user_id varchar(40) NOT NULL,
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE dbo.topups (
    id varchar(20) NOT NULL PRIMARY KEY,
    user_id varchar(20) NOT NULL,
    amount decimal(12,2) NOT NULL,
    currency char(3) NOT NULL,
    status varchar(20) NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    provider_payment_id varchar(20) NULL,
    ledger_txn_id varchar(20) NULL,
    payment_method varchar(30) NOT NULL,
    app_version varchar(20) NOT NULL,
    customer_email varchar(100) NOT NULL,
    created_at datetime2 NOT NULL
);
CREATE INDEX ix_topups_created_at ON dbo.topups (created_at);
CREATE INDEX ix_topups_provider_payment_id ON dbo.topups (provider_payment_id);

CREATE TABLE dbo.topup_status_history (
    id int NOT NULL PRIMARY KEY,
    topup_id varchar(20) NOT NULL REFERENCES dbo.topups (id),
    status varchar(20) NOT NULL,
    changed_at datetime2 NOT NULL
);

CREATE TABLE dbo.provider_webhooks (
    id int NOT NULL PRIMARY KEY,
    payment_id varchar(20) NOT NULL,
    event_type varchar(50) NOT NULL,
    received_at datetime2 NOT NULL
);
CREATE INDEX ix_provider_webhooks_received_at ON dbo.provider_webhooks (received_at);
CREATE INDEX ix_provider_webhooks_payment_id ON dbo.provider_webhooks (payment_id);

CREATE TABLE dbo.outbox_events (
    id int NOT NULL PRIMARY KEY,
    aggregate_id varchar(20) NOT NULL,
    status varchar(20) NOT NULL CHECK (status IN ('pending', 'published', 'failed')),
    attempts int NOT NULL,
    last_error varchar(200) NULL,
    created_at datetime2 NOT NULL
);
CREATE INDEX ix_outbox_events_created_at ON dbo.outbox_events (created_at);
GO

INSERT INTO dbo.currencies (code, name, minor_units) VALUES ('USD', 'US dollar', 2);

INSERT INTO dbo.wallets (user_id, currency, balance) VALUES
    ('usr_1001', 'USD', 200.00),
    ('usr_1002', 'USD', 0.00),
    ('usr_1003', 'USD', 0.00);

INSERT INTO dbo.user_provider_ids (user_id, provider, provider_user_id) VALUES
    ('usr_1001', 'checkout', 'cus_fake_1001'),
    ('usr_1002', 'checkout', 'cus_fake_1002'),
    ('usr_1003', 'checkout', 'cus_fake_1003');

INSERT INTO dbo.topups
    (id, user_id, amount, currency, status, provider_payment_id, ledger_txn_id,
     payment_method, app_version, customer_email, created_at)
VALUES
    ('tp_91f2c7', 'usr_1002', 500.00, 'USD', 'succeeded', 'pay_7hq2m9', 'lt_55e1a0',
     'Apple Pay', '5.3.0', 'user1002@example.com', DATEADD(hour, -3, SYSUTCDATETIME())),
    ('tp_91f2b9', 'usr_1001', 200.00, 'USD', 'succeeded', 'pay_4kd8x1', 'lt_55e19f',
     'Card', '5.2.4', 'user1001@example.com', DATEADD(hour, -4, SYSUTCDATETIME())),
    ('tp_92a1b4', 'usr_1001', 50.00, 'USD', 'pending', 'pay_8ab1c4', NULL,
     'Card', '5.2.4', 'user1001@example.com', DATEADD(minute, -28, SYSUTCDATETIME())),
    ('tp_92a1f0', 'usr_1003', 75.00, 'USD', 'pending', 'pay_8ab1f0', NULL,
     'Card', '5.3.0', 'user1003@example.com', DATEADD(minute, -22, SYSUTCDATETIME())),
    ('tp_92a2c3', 'usr_1003', 120.00, 'USD', 'pending', 'pay_8ab2c3', NULL,
     'Apple Pay', '5.3.0', 'user1003@example.com', DATEADD(minute, -14, SYSUTCDATETIME())),
    ('tp_93b001', 'usr_1002', 90.00, 'USD', 'succeeded', 'pay_3nw6t2', NULL,
     'Card', '5.3.0', 'user1002@example.com', DATEADD(hour, -2, SYSUTCDATETIME()));

INSERT INTO dbo.topup_status_history (id, topup_id, status, changed_at) VALUES
    (1, 'tp_91f2c7', 'pending', DATEADD(minute, -181, SYSUTCDATETIME())),
    (2, 'tp_91f2c7', 'succeeded', DATEADD(hour, -3, SYSUTCDATETIME())),
    (3, 'tp_91f2b9', 'pending', DATEADD(minute, -241, SYSUTCDATETIME())),
    (4, 'tp_91f2b9', 'succeeded', DATEADD(hour, -4, SYSUTCDATETIME())),
    (5, 'tp_93b001', 'succeeded', DATEADD(hour, -2, SYSUTCDATETIME()));

INSERT INTO dbo.provider_webhooks (id, payment_id, event_type, received_at) VALUES
    (1, 'pay_7hq2m9', 'payment.captured', DATEADD(hour, -3, SYSUTCDATETIME())),
    (2, 'pay_7hq2m9', 'payment.captured', DATEADD(minute, -175, SYSUTCDATETIME())),
    (3, 'pay_4kd8x1', 'payment.captured', DATEADD(hour, -4, SYSUTCDATETIME())),
    (4, 'pay_3nw6t2', 'payment.captured', DATEADD(hour, -2, SYSUTCDATETIME()));

INSERT INTO dbo.outbox_events (id, aggregate_id, status, attempts, last_error, created_at) VALUES
    (1, 'tp_91f2c7', 'failed', 5, 'ledger posting timed out', DATEADD(hour, -3, SYSUTCDATETIME())),
    (2, 'tp_91f2b9', 'published', 1, NULL, DATEADD(hour, -4, SYSUTCDATETIME())),
    (3, 'tp_93b001', 'pending', 0, NULL, DATEADD(hour, -2, SYSUTCDATETIME()));
GO

CREATE USER [$(RO_LOGIN)] FOR LOGIN [$(RO_LOGIN)];
ALTER ROLE db_datareader ADD MEMBER [$(RO_LOGIN)];
GRANT SHOWPLAN TO [$(RO_LOGIN)];
CREATE USER [$(RW_LOGIN)] FOR LOGIN [$(RW_LOGIN)];
ALTER ROLE db_datareader ADD MEMBER [$(RW_LOGIN)];
ALTER ROLE db_datawriter ADD MEMBER [$(RW_LOGIN)];
GO
