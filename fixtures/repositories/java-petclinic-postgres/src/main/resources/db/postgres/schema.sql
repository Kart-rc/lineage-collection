create table owners (
    id integer primary key,
    last_name varchar(255) not null,
    city varchar(255)
);

create table visits (
    id integer primary key,
    owner_id integer not null,
    visit_date date not null
);
