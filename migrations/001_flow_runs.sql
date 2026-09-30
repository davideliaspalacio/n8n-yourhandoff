-- Runs de los flujos y coste de cada llamada de pago. Idempotente.
create table if not exists flow_runs (
    id          uuid primary key,
    flow        text        not null,
    status      text        not null check (status in ('running', 'succeeded', 'failed')),
    dry_run     boolean     not null default false,
    input       jsonb       not null default '{}'::jsonb,
    output      jsonb,
    error       text,
    steps       jsonb       not null default '[]'::jsonb,
    created_at  timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists flow_runs_flow_created_idx on flow_runs (flow, created_at desc);
create index if not exists flow_runs_running_idx on flow_runs (status) where status = 'running';

create table if not exists cost_events (
    id          bigserial primary key,
    run_id      uuid        not null references flow_runs (id) on delete cascade,
    provider    text        not null,
    operation   text        not null,
    units       numeric     not null default 0,
    cost_usd    numeric(12, 6) not null default 0,
    meta        jsonb       not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists cost_events_run_idx on cost_events (run_id);
create index if not exists cost_events_provider_created_idx on cost_events (provider, created_at);

-- Solo el servicio (rol con la cadena de conexión) lee y escribe aquí.
alter table flow_runs enable row level security;
alter table cost_events enable row level security;
