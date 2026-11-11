-- Author: @SaadZarook
-- Creates and seeds one copy of the card access database (the target database is chosen by the caller).
-- Fake data only.
CREATE TABLE visitor_profile (
    id          VARCHAR(12)  NOT NULL PRIMARY KEY,
    employee_no VARCHAR(20)  NULL,
    email       VARCHAR(100) NOT NULL,
    type        VARCHAR(20)  NOT NULL,
    status      VARCHAR(20)  NOT NULL CHECK (status IN ('active', 'inactive')),
    created_at  DATETIME     NOT NULL
);
CREATE INDEX ix_visitor_profile_email ON visitor_profile (email);
-- The healthy sample: active visitors of one type, newest first.
CREATE INDEX ix_visitor_profile_healthy ON visitor_profile (status, type, created_at);

CREATE TABLE cards (
    card_id            VARCHAR(10) NOT NULL PRIMARY KEY,
    visitor_profile_id VARCHAR(12) NOT NULL,
    status             VARCHAR(20) NOT NULL CHECK (status IN ('active', 'revoked')),
    valid_from         DATE        NOT NULL,
    valid_to           DATE        NOT NULL,
    access_group       VARCHAR(30) NOT NULL,
    FOREIGN KEY (visitor_profile_id) REFERENCES visitor_profile (id)
);
CREATE INDEX ix_cards_visitor_profile_id ON cards (visitor_profile_id);

INSERT INTO visitor_profile (id, employee_no, email, type, status, created_at) VALUES
    ('VP00012931', 'EMP-100231', 'contractor.one@example.com', 'contractor', 'active', '2025-11-03 09:00:00'),
    ('VP00008807', 'EMP-100231', 'contractor.one@example.com', 'contractor', 'inactive', '2024-05-20 09:00:00');

INSERT INTO cards (card_id, visitor_profile_id, status, valid_from, valid_to, access_group) VALUES
    ('C204518', 'VP00012931', 'active',  CURDATE() - INTERVAL 60 DAY, CURDATE() + INTERVAL 60 DAY, 'HQ-LOBBY'),
    ('C198220', 'VP00012931', 'active',  CURDATE() - INTERVAL 300 DAY, CURDATE() - INTERVAL 30 DAY, 'HQ-LOBBY'),
    ('C177031', 'VP00012931', 'revoked', CURDATE() - INTERVAL 300 DAY, CURDATE() + INTERVAL 60 DAY, 'HQ-PARKING');
