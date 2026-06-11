# Prompt inicial para Claude Code (Sprint 0)

> Antes de pegar el prompt:
> 1. Verifica que los fixtures estén en backend/tests/fixtures/
>    (3-4 PNGs del radar extraídos de KMZs reales + leyenda.png)
> 2. Configura el modelo: /model opusplan
> 3. Entra a plan mode: Shift+Tab
> Luego pega lo de abajo.

---

Lee `CLAUDE.md`, `docs/plan-desarrollo.md` y `docs/spec-radar-iam.md`
completos antes de hacer nada. El CLAUDE.md y los subagentes en
`.claude/agents/` ya están creados — no los modifiques.

Después, en este orden:

1. **Propón `backend/app/schemas.py`** con los modelos Pydantic:
   - `PointForecast`: pronóstico Open-Meteo por punto (precipitación
     horaria 12h, probabilidad, viento 10m y 700hPa, temperatura,
     timestamps en America/Mexico_City)
   - `RadarReading`: lectura del radar por punto (dBZ, categoría de la
     leyenda, timestamp UTC del escaneo, edad del frame)
   - `NowcastResult`: estimación de llegada de lluvia (estructura
     preliminar, se refinará en Sprint 3)
   Este archivo es el contrato entre todos los módulos del proyecto.
   **Detente y espera mi aprobación explícita del schema antes de
   continuar** — no generes nada más hasta que lo apruebe.

2. Tras mi aprobación: genera el scaffold completo del repo según la
   estructura de la sección 1 del plan — stubs con firmas de función
   y docstrings que digan QUÉ hace cada función (sin implementar
   lógica), y el setup de pytest apuntando a backend/tests/fixtures/.

3. En `backend/app/config.py` deja dos puntos de ejemplo con el shape
   correcto; los puntos reales los definiré yo después.

No implementes lógica todavía — Sprint 0 es solo estructura y contrato.

---

> Después del Sprint 0: el prompt del Sprint 1 (tres subagentes en
> paralelo) está en la sección 3 del plan — cópialo tal cual.
