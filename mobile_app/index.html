-- Llama a Jaime V5.4 · Seguridad MVP
-- Ejecutar una vez DESPUÉS de las migraciones V5.1, V5.2, V5.3 y V5.3.1.

create extension if not exists pgcrypto;

-- Consentimiento y teléfono normalizado del cliente.
alter table public.nexi_app_solicitudes
  add column if not exists telefono_normalizado text,
  add column if not exists acepta_terminos boolean not null default false,
  add column if not exists acepta_privacidad boolean not null default false,
  add column if not exists consentimiento_at timestamptz,
  add column if not exists terminos_version text;

-- Cuenta única, acceso recuperable y estado de revisión del prestador.
alter table public.nexi_app_prestadores
  add column if not exists rut_normalizado text,
  add column if not exists telefono_normalizado text,
  add column if not exists pin_salt text,
  add column if not exists pin_hash text,
  add column if not exists acepta_terminos boolean not null default false,
  add column if not exists acepta_privacidad boolean not null default false,
  add column if not exists consentimiento_at timestamptz,
  add column if not exists terminos_version text,
  add column if not exists estado_cuenta text not null default 'activa',
  add column if not exists estado_verificacion text not null default 'pendiente',
  add column if not exists suspendido_at timestamptz,
  add column if not exists motivo_suspension text,
  add column if not exists ultimo_login_at timestamptz;

alter table public.nexi_app_prestadores
  drop constraint if exists nexi_app_prestadores_estado_cuenta_check;
alter table public.nexi_app_prestadores
  add constraint nexi_app_prestadores_estado_cuenta_check
  check (estado_cuenta in ('activa','suspendida','cerrada'));

alter table public.nexi_app_prestadores
  drop constraint if exists nexi_app_prestadores_verificacion_check;
alter table public.nexi_app_prestadores
  add constraint nexi_app_prestadores_verificacion_check
  check (estado_verificacion in ('pendiente','verificado','rechazado'));

create unique index if not exists nexi_app_prestadores_empresa_rut_unique
  on public.nexi_app_prestadores (empresa_id, rut_normalizado)
  where rut_normalizado is not null;

create index if not exists nexi_app_solicitudes_telefono_idx
  on public.nexi_app_solicitudes (empresa_id, telefono_normalizado, created_at desc);

create index if not exists nexi_app_prestadores_seguridad_idx
  on public.nexi_app_prestadores (empresa_id, estado_cuenta, activo, disponible);

-- Reclamos privados para revisión y eventual suspensión manual.
create table if not exists public.nexi_app_reclamos (
  id uuid primary key default gen_random_uuid(),
  empresa_id uuid not null,
  solicitud_id uuid not null references public.nexi_app_solicitudes(id) on delete cascade,
  prestador_id uuid references public.nexi_app_prestadores(id) on delete set null,
  reportante_tipo text not null check (reportante_tipo in ('cliente','prestador')),
  categoria text not null check (categoria in ('seguridad','estafa','trato','cobro','servicio','contenido','otro')),
  descripcion text not null check (char_length(descripcion) between 10 and 2000),
  estado text not null default 'abierto' check (estado in ('abierto','en_revision','resuelto','descartado')),
  resolucion text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists nexi_app_reclamos_revision_idx
  on public.nexi_app_reclamos (empresa_id, estado, created_at desc);
create index if not exists nexi_app_reclamos_prestador_idx
  on public.nexi_app_reclamos (prestador_id, estado, created_at desc);

alter table public.nexi_app_reclamos enable row level security;

drop trigger if exists nexi_app_reclamos_touch on public.nexi_app_reclamos;
create trigger nexi_app_reclamos_touch
before update on public.nexi_app_reclamos
for each row execute function public.nexi_app_touch_updated_at();

-- Auditoría mínima; nunca se expone directamente al navegador.
create table if not exists public.nexi_app_auditoria_seguridad (
  id uuid primary key default gen_random_uuid(),
  empresa_id uuid not null,
  evento text not null,
  actor_tipo text,
  actor_id uuid,
  solicitud_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists nexi_app_auditoria_evento_idx
  on public.nexi_app_auditoria_seguridad (empresa_id, evento, created_at desc);
alter table public.nexi_app_auditoria_seguridad enable row level security;

-- La toma se valida también dentro de la base: cuenta activa y sin autoasignación.
create or replace function public.nexi_app_tomar_match(
  p_match_id uuid,
  p_prestador_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_match public.nexi_app_matches%rowtype;
  v_solicitud public.nexi_app_solicitudes%rowtype;
  v_prestador public.nexi_app_prestadores%rowtype;
begin
  select * into v_prestador
  from public.nexi_app_prestadores
  where id = p_prestador_id
  for update;

  if not found or not v_prestador.activo or v_prestador.estado_cuenta <> 'activa' then
    return jsonb_build_object('ok', false, 'error', 'La cuenta del prestador no está habilitada');
  end if;

  select * into v_match
  from public.nexi_app_matches
  where id = p_match_id and prestador_id = p_prestador_id
  for update;

  if not found then
    return jsonb_build_object('ok', false, 'error', 'Oportunidad no encontrada');
  end if;

  select * into v_solicitud
  from public.nexi_app_solicitudes
  where id = v_match.solicitud_id
  for update;

  if v_solicitud.estado not in ('publicada','revisando') then
    return jsonb_build_object('ok', false, 'error', 'La solicitud ya fue tomada');
  end if;

  if v_solicitud.telefono_normalizado is not null
     and v_prestador.telefono_normalizado is not null
     and v_solicitud.telefono_normalizado = v_prestador.telefono_normalizado then
    return jsonb_build_object('ok', false, 'error', 'No puedes tomar una solicitud creada por tu propia cuenta');
  end if;

  update public.nexi_app_solicitudes
  set estado = 'asignada', prestador_id = p_prestador_id, tomada_at = now()
  where id = v_solicitud.id;

  update public.nexi_app_matches
  set estado = case when id = p_match_id then 'tomada' else 'cerrada' end
  where solicitud_id = v_solicitud.id and estado = 'pendiente';

  insert into public.nexi_app_auditoria_seguridad
    (empresa_id, evento, actor_tipo, actor_id, solicitud_id)
  values
    (v_solicitud.empresa_id, 'solicitud_tomada', 'prestador', p_prestador_id, v_solicitud.id);

  return jsonb_build_object(
    'ok', true,
    'solicitud_id', v_solicitud.id,
    'estado', 'asignada'
  );
end;
$$;

revoke all on function public.nexi_app_tomar_match(uuid, uuid) from public;
grant execute on function public.nexi_app_tomar_match(uuid, uuid) to service_role;

comment on table public.nexi_app_reclamos is
  'Reclamos privados del MVP de Llama a Jaime para moderación y revisión';
comment on column public.nexi_app_prestadores.estado_verificacion is
  'Pendiente no equivale a identidad verificada; solo usar verificado tras revisión real';
