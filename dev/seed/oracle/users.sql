-- Author: @SaadZarook
-- Schema owners (no login) and the two database logins, named and passworded by seed.sh.
WHENEVER SQLERROR EXIT FAILURE
-- SQL*Plus would otherwise print each line with its substitutions, passwords and all.
SET VERIFY OFF
ALTER SESSION SET CONTAINER = FREEPDB1;
CREATE USER &schema NO AUTHENTICATION QUOTA UNLIMITED ON USERS;
CREATE USER &schema_uat NO AUTHENTICATION QUOTA UNLIMITED ON USERS;
CREATE USER &ro_login IDENTIFIED BY "&ro_password";
CREATE USER &rw_login IDENTIFIED BY "&rw_password";
GRANT CREATE SESSION TO &ro_login;
GRANT CREATE SESSION TO &rw_login;
EXIT
