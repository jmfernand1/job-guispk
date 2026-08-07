# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |

# El proyecto: reglas que no se rompen

Motor de enmascaramiento con **dos apps** (interno y aliado) coordinadas por tablas
`guispk_*` **append-only en Impala** (esquema `proceso_enmascarado`). Contexto completo en
[README.md](README.md); el porque de cada decision, en [CHANGELOG.md](CHANGELOG.md)
(indice con tags → `docs/decisiones/`).

- **Entorno.** Tests y apps corren con el entorno conda `guispk`. El python del sistema no
  tiene pytest ni PyQt6:
  `source /opt/miniconda3/etc/profile.d/conda.sh && conda activate guispk && python -m pytest -q`
- **Capas.** `aliado/` **nunca** importa `interno/` — su ejecutable se construye
  excluyendolo. Si puede usar `sparky_bc` a traves de `core/sparky_client.py`: llega a
  Impala por Sparky y cae a pyodbc + su DSN si falla (`connect_impala`). `core/` no
  importa Qt salvo en `core/ui/`.
- **Secretos.** Las tablas `guispk_*` las leen los aliados: no pueden contener credenciales
  ni salts. El SQL se guarda con `{{TEXT_SALT}}` / `{{INT_SALT}}` y se sustituye solo al
  ejecutar. Hay tests que lo verifican; si uno falla, es un fallo de seguridad, no de forma.
- **Append-only.** Las `guispk_*` son insert-only (Impala/Parquet no soporta UPDATE): el
  estado es el fold de eventos (`core/store/requests_repo.py`). Nunca introducir UPDATE ni
  DELETE, y los cambios de esquema son aditivos via `_ALTERS` en `core/store/ddl.py` (nunca
  editar DDL publicado: hay .exe viejos escribiendo en las mismas tablas).
- **Nombres de columna.** Ninguna columna puede llamarse como una palabra reservada de
  Impala: el `CREATE TABLE` falla al desplegar. Por eso `event_at` / `event_by` /
  `actor_role` / `note` y no `at` / `who` / `role` / `comment`. `tests/test_ddl_reserved.py`
  parsea el DDL real y lo verifica; si falla, es el nombre lo que hay que cambiar.
- **Latencia.** Toda llamada a repos desde la UI pasa por `core/ui/repo_worker.py`
  (QThread): contra Impala cada consulta tarda segundos. No llamar repos en el hilo de UI.
- **Quien decide que.** El aliado pide columnas; el enmascaramiento lo decide el interno y
  solo puede restringir (`core/review.py`). No aflojar esa validacion.
- **Respaldo.** `core/store/backup.py` copia las `guispk_*` a un SQLite en OneDrive (solo
  la app interna; agendable con `tools/backup_guispk.py`). El restore **inserta lo que
  falta por id**, nunca borra ni actualiza: un evento duplicado corrompe el fold. El
  esquema del espejo sale de `ddl.SCHEMA`, que es la unica fuente de verdad de columnas —
  agregar una columna ahi la propaga sola.
- **Particionado.** El destino hereda las columnas de particion del origen
  (`sql_builder.build_create/build_insert`). En Impala no se repiten en la lista de
  columnas, llevan tipo, y en el INSERT van al final del SELECT con `PARTITION (...)`.
  `partition_cols=None` tiene que seguir dando el SQL de antes: las solicitudes viejas se
  verifican regenerando su script y comparandolo con el guardado.
