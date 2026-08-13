-- A table created and later removed must not appear in the discovered schema.
create table legacy_audit (id integer primary key);
drop table legacy_audit;
