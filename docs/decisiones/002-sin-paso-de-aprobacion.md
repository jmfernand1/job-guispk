# 002 — Se elimina el paso de aprobacion

**Tags:** `#estados` `#ui-interno` · **Fecha:** 2026-08-05 · **Commits:** `b785daa`, `76702b7`

## Contexto

El ciclo original era `borrador → enviada → aprobada → ejecutada`. Al probar el flujo
aparecio el problema: para sacar una tabla, el interno tenia que **aprobar**, despues
volver a buscar la solicitud que acababa de aprobar y **ejecutarla**. Dos pasos, dos
busquedas y una lista de solicitudes aprobadas que nadie ejecutaba.

La aprobacion no aportaba control real: quien aprobaba era la misma persona que ejecutaba,
en la misma pantalla, con la misma informacion a la vista.

## Decision

Fuera el estado `aprobada`. Desde `enviada`, el interno decide en **una sola accion**:

```
borrador → enviada → ejecutada
                  → rechazada → borrador  (corregir y reenviar)
enviada → borrador                        (el aliado la retira)
```

Al ejecutar desde `enviada` se rellenan tambien `reviewed_by` / `reviewed_at`: quien
ejecuta es quien revisa, y la trazabilidad de "quien autorizo" no se pierde.

## Consecuencias

- Entre menos pasos tenga que dar el interno, mas probable es que la solicitud
  efectivamente se ejecute. Ese era el objetivo.
- El boton *Aprobar* desaparecio de la app interna; quedan *Ejecutar solicitud* y
  *Rechazar*, ambos activos solo con la solicitud en `enviada`.
- El estado se elimino **por completo**, incluido el `CHECK` del esquema: se borro la BD
  de pruebas en el mismo cambio, asi que no quedaron filas atrapadas en `aprobada`. Si
  apareciera una BD vieja con ese estado, el `CHECK` la rechazaria — habria que agregar
  una migracion que la reasigne.

## Archivos

- `core/states.py` — `TRANSITIONS`, `ALL_STATES`.
- `core/store/requests_repo.py` — `transition()` rellena la revision al ejecutar.
- `core/store/migrations.py` — `CHECK (state IN (...))` sin `aprobada`.
- `interno/ui/main_window.py` — pestana Solicitudes.
