-- Los runs anteriores a la plataforma usaban nombres snake_case; el registro
-- usa slugs kebab-case. Renombrar para que stats y replay los reconozcan.
-- Idempotente: tras la primera pasada ya no quedan '_' en flow.
update flow_runs set flow = replace(flow, '_', '-') where flow like '%\_%' escape '\';
