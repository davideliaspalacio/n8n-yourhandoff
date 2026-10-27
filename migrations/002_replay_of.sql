-- Un run relanzado desde el dashboard apunta al run original. Idempotente.
alter table flow_runs
    add column if not exists replay_of uuid references flow_runs (id) on delete set null;
