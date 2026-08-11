create table public.owners (
    id integer primary key,
    last_name varchar(255) not null
);

create table public.visits (
    id integer primary key,
    owner_id integer not null
);
