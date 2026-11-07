# Despacho del SIN con y sin FNCER

Modelo en Python del despacho económico colombiano, calibrado con datos reales de XM, para
analizar el impacto de la generación solar y eólica (FNCER) en el despacho, el precio de bolsa,
las emisiones y la reserva operativa (AGC). También busca la capacidad de entrada de FNCER que
minimiza el costo del sistema.

## Uso

```bash
cd despacho_fncer
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt

.venv/bin/python run.py              # todo: validación, escenarios, barrido, optimización, reporte
.venv/bin/python run.py validar      # solo compara el modelo base contra XM
.venv/bin/python -m pytest           # pruebas del LP en un sistema de juguete
```

El periodo, los escenarios y todos los supuestos se cambian en `config.yaml`. La primera corrida
de un mes descarga los datos de XM y SIMEM (unos minutos); luego todo sale de la caché
(`data/raw/`) y la corrida completa tarda menos de un minuto.

Salidas en `resultados/`:

| Archivo | Contenido |
|---|---|
| `reporte.md` | **Empieza aquí.** Reporte por secciones con gráficos: 1 datos y días usados, 2 comportamiento real del SIN (por hora y por día), 3 demanda y solar del mediodía, 4 inercia, 5 validación, 6 escenarios, 7 óptimo, 8 sensibilidad de reserva, 9 supuestos |
| `resultados_despacho.xlsx` | Las mismas secciones en hojas numeradas (`1_Datos`, `2_SIN_por_hora`, `2_SIN_por_dia`...) más las 744 horas de cada escenario (`H_<escenario>`) |
| `graficos/*.png` | Numerados en el orden del reporte |
| `powerfactory/generacion_real_XM.csv` | Generación real medida por planta (MW por hora): el caso base más representativo para estudios de red |
| `powerfactory/despacho_modelo_<escenario>.csv` | Despacho del modelo por planta con oferta + FNCER nueva (MW por hora) |

## Cómo funciona

```
API XM (pydataxm)  ─┐                                  ┌─> validación vs GeneIdea / MaxPrecOferNal
SIMEM (ReadSIMEM)  ─┼─> caché Parquet ─> insumos R×P ──┼─> escenarios (ideal + con reserva)
config.yaml        ─┘                                  ├─> barrido de MW solar × eólica
                                                       └─> optimización de capacidad ─> reporte
```

| Módulo | Qué hace |
|---|---|
| `despacho/datos.py` | Descarga y guarda en caché las métricas de XM y SIMEM en formato largo |
| `despacho/preparacion.py` | Arma matrices recurso × hora en MW: costo, disponibilidad, inflexibilidad, demanda residual, perfiles FNCER, requerimiento AGC |
| `despacho/modelo.py` | LP horario uninodal (HiGHS vía `scipy.optimize.linprog`); el dual del balance es el costo marginal |
| `despacho/sistema.py` | Comportamiento real del SIN: perfil horario, tabla diaria, demanda neta, rampas, solar por clase, real frente a ideal |
| `despacho/inercia.py` | Estimación de la inercia sincrónica (XM no la publica) |
| `despacho/validacion.py` | Métricas de error frente al despacho ideal publicado |
| `despacho/escenarios.py` | Escenarios, barrido, sensibilidad y optimización de la entrada |
| `despacho/graficos.py` / `reporte.py` | Gráficos; Excel, Markdown y CSV para PowerFactory |

### Datos usados

| Dato | Fuente | Uso |
|---|---|---|
| `PrecOferIdeal` (respaldo `PrecOferDesp`) | API XM | Costo de cada recurso; el ideal incluye arranque/parada de térmicas |
| `DispoCome` | API XM | Límite superior. Se comprobó que el despacho ideal la respeta en el 100 % de las horas (`DispoDeclarada` no) |
| `GeneIdea` | API XM | Inyección fija de recursos sin oferta + referencia de validación |
| `DemaCome` | API XM | Demanda horaria |
| `MaxPrecOferNal`, `PrecBolsNaci`, `IndRecMargina` | API XM | Validación de precio y recurso marginal; ΔI = bolsa − máximo precio de oferta |
| `Gene`, `CapEfecNeta` | API XM | Perfiles horarios de factor de planta solar y eólico |
| `EmisionesCO2Eq` | API XM | Factor tCO2e/MWh por planta |
| Inflexibilidades (`FE1E73`) | SIMEM | Piso de generación (no fija precio) |
| Holgura AGC (`897043`) | SIMEM | Requerimiento de reserva base y plantas que prestan AGC |
| Unidades de generación (`670221`) | SIMEM | Unidades por planta, para estimar la inercia |
| `GeneSeguridad`, `GeneFueraMerito` | API XM | Explicar la diferencia entre operación real y despacho ideal |

### Formulación

Para cada hora *p* y recurso *r*:

- **Objetivo:** minimizar Σ precio_oferta·g + penalizaciones (racionamiento, vertimiento,
  déficit de reserva) + costo anualizado de la capacidad FNCER, solo cuando se optimiza.
- **Balance:** Σ g + FNCER nueva + déficit − vertimiento = demanda − inyección fija. Su dual es el
  costo marginal. El precio se calcula con la regla de XM: la oferta más alta entre los recursos
  flexibles despachados, más ΔI.
- **Límites:** inflexibilidad ≤ g ≤ disponibilidad comercial.
- **FNCER nueva:** y ≤ cf·MW si es despachada (puede verterse); y = cf·MW si es distribuida.
- **Reserva AGC** (en la segunda corrida de cada escenario): las plantas AGC dejan holgura arriba
  y abajo ≥ holgura real de XM + β·(generación FNCER nueva esperada). El dual es el precio de la
  reserva y su costo es la diferencia frente al despacho ideal.

## Supuestos y limitaciones

- **Red uninodal**, como el despacho ideal: sin restricciones de transmisión, generación de
  seguridad ni reconciliaciones.
- **LP, no MILP**: sin arranques, mínimos técnicos ni rampas de las térmicas, más allá de las
  inflexibilidades reales. Por eso el modelo subestima el precio en algunas puntas de 18–20 h.
- **Hidráulicas por su precio de oferta**, sin valor del agua dinámico. Cada día se resuelve con
  las ofertas reales, así que el agua que desplaza la FNCER se valora a ese precio y no se
  traslada a otros días. Es la limitación más importante para leer los escenarios grandes.
- **Perfil eólico**: sale de los dos parques con generación en 2026 (Guajira I y WESP01), sin
  capacidad efectiva publicada, normalizados por su máxima generación observada. Es probable
  que eso sobreestime el factor de planta.
- **β de reserva** (5 % solar, 8 % eólica) y **costos de inversión** son supuestos editables.
  Revisa la hoja `8_Sensibilidad_reserva`.
- **Inercia estimada**, no medida: H típico por tecnología y mínimo de unidades en línea (cota
  inferior). La del modelo sale del despacho ideal, con menos térmicas que la operación real.
- **Operación real ≠ despacho ideal**: en octubre de 2026 las térmicas fueron 36 % de la energía real
  pero 14 % del ideal (generación fuera de mérito y de seguridad). El modelo reproduce el ideal.
