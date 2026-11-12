-- Entirely synthetic GBP orders. This is unrelated to the historical UCI dataset.
create table orders (
    order_id integer primary key check (order_id > 0),
    customer_id integer not null check (customer_id > 0),
    order_day varchar(10) not null,
    gross_cents bigint not null check (gross_cents between 0 and 1000000000000),
    refund_cents bigint not null check (refund_cents >= 0 and refund_cents <= gross_cents),
    status varchar(10) not null check (status in ('paid', 'cancelled'))
);
alter table orders replica identity full;
insert into orders values
    (1, 101, '2026-11-01', 10000, 0, 'paid'),
    (2, 102, '2026-11-01', 5000, 0, 'paid');
