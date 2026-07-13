-- Ejecutar una vez en el SQL Editor de Supabase.

-- Mensajes crudos del canal y su grupo de discusión (comentarios).
create table if not exists mensajes (
  chat        text        not null,  -- 'canal' | 'comentarios'
  message_id  bigint      not null,
  fecha       timestamptz,
  texto       text,
  reply_to    bigint,                -- id del mensaje al que responde (hilo de comentarios)
  raw         jsonb,                 -- mensaje completo de Telethon, por si el parser mejora
  procesado   boolean     not null default false,
  primary key (chat, message_id)
);

create index if not exists mensajes_pendientes on mensajes (chat, message_id) where not procesado;
create index if not exists mensajes_fecha on mensajes (fecha desc);

-- Eventos ya interpretados (los llenará el extractor en la fase 3).
create table if not exists eventos (
  id          bigint generated always as identity primary key,
  chat        text   not null,
  message_id  bigint not null,
  tipo        text   not null,      -- 'afectacion' | 'restablecimiento' | 'reporte_usuario'
  bloque      int,
  municipios  text[],
  zonas       text[],
  causa       text,
  fecha       timestamptz not null,
  foreign key (chat, message_id) references mensajes (chat, message_id)
);

create index if not exists eventos_bloque on eventos (bloque, fecha desc);

-- Reportes vecinales ("no tengo corriente en...") enviados desde la web.
create table if not exists reportes (
  id        bigint generated always as identity primary key,
  fecha     timestamptz not null default now(),
  lat       double precision not null,
  lon       double precision not null,
  direccion text,
  tipo      text not null default 'sin',
  ip_hash   text not null
);
create index if not exists reportes_fecha on reportes (fecha desc);
create index if not exists reportes_ip on reportes (ip_hash, fecha desc);

-- Señal extraída por LLM de los comentarios de vecinos (lenguaje libre).
create table if not exists comentarios_llm (
  message_id bigint primary key,      -- del grupo de comentarios
  fecha      timestamptz not null,
  reporta    text,                    -- sin_corriente | con_corriente | pregunta | queja | irrelevante
  lugar      text,
  bloque     int,
  horas      int,
  lat        double precision,
  lon        double precision
);
create index if not exists comentarios_llm_fecha on comentarios_llm (fecha desc);
