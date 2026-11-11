-- Author: @SaadZarook
-- The planted drift case: UAT FEE_RULES has one column Production does not. Argument 1 is the
-- UAT schema.
WHENEVER SQLERROR EXIT FAILURE
ALTER SESSION SET CONTAINER = FREEPDB1;
ALTER TABLE &1..FEE_RULES ADD (MIN_FEE NUMBER(8,2));
EXIT
