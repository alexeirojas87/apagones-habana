-- Índice semántico del chatbot. Se ejecuta UNA VEZ en el SQL Editor de Supabase.
--
-- Por qué en la base y no en un JSON servido por Pages: el índice son ~1.700
-- fragmentos × 1024 dimensiones, decenas de MB. El enfoque anterior escribía
-- web/data/chatbot_embeddings.json y el archivo terminó borrado por tamaño,
-- dejando el "RAG" sin vectores. Aquí los vectores no salen nunca del servidor:
-- el worker manda la consulta y recibe solo los k fragmentos relevantes.

-- DIMENSIÓN: verificado contra la API — qwen3-embedding devuelve 4096 por
-- defecto, pero los índices ivfflat de pgvector solo admiten hasta 2000. El
-- modelo es Matryoshka, así que embeddings.py y el worker piden
-- "dimensions": 1024 explícitamente. No cambies este número sin cambiarlo
-- también en los dos sitios, o los vectores dejan de ser comparables.

create extension if not exists vector;

-- Un fragmento = un parte oficial o un reporte vecinal, en texto plano, listo
-- para búsqueda semántica. Lo estructurado (estado actual, conteos) NO vive
-- aquí: eso se responde con filtros exactos, no por similitud.
create table if not exists chatbot_fragmentos (
  id         text primary key,        -- message_id del parte, o com_<id> del comentario
  tipo       text not null,           -- afectacion | restablecimiento | reporte | otro
  fecha      timestamptz not null,
  texto      text not null,           -- lo que se embebe y lo que ve el LLM
  metadatos  jsonb not null default '{}'::jsonb,
  hash       text not null,           -- sha1 del texto: evita re-embeber lo ya indexado
  embedding  vector(1024) not null,   -- qwen3-embedding
  indexado   timestamptz not null default now()
);

create index if not exists chatbot_fragmentos_fecha on chatbot_fragmentos (fecha desc);
create index if not exists chatbot_fragmentos_tipo on chatbot_fragmentos (tipo, fecha desc);

-- ivfflat con coseno. lists≈sqrt(filas): con ~2k fragmentos, 45 es razonable.
-- El índice necesita datos para entrenarse; con la tabla vacía se crea igual y
-- Postgres cae a escaneo secuencial, que a esta escala tampoco duele.
create index if not exists chatbot_fragmentos_embedding
  on chatbot_fragmentos using ivfflat (embedding vector_cosine_ops) with (lists = 45);

-- Búsqueda que llama el worker por RPC. Devuelve similitud (1 = idéntico) para
-- que el llamador pueda descartar coincidencias flojas.
create or replace function buscar_fragmentos(
  query_embedding vector(1024),
  match_count     int  default 6,
  tipos           text[] default null,   -- null = todos
  desde           timestamptz default null
)
returns table (
  id        text,
  tipo      text,
  fecha     timestamptz,
  texto     text,
  metadatos jsonb,
  similitud float
)
language sql stable
as $$
  select f.id, f.tipo, f.fecha, f.texto, f.metadatos,
         1 - (f.embedding <=> query_embedding) as similitud
  from chatbot_fragmentos f
  where (tipos is null or f.tipo = any(tipos))
    and (desde is null or f.fecha >= desde)
  order by f.embedding <=> query_embedding
  limit match_count;
$$;

-- El worker usa la service key; anon no debe leer el índice directamente.
alter table chatbot_fragmentos enable row level security;
