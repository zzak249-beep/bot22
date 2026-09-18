# Crowding bot — solo señales (v3.0)

Bot de señales del posicionamiento amontonado en perpetuos de BingX.

**NO OPERA. NO PIDE CLAVES DE API.** Solo endpoints públicos.

## Qué cambió en la v3.0, y por qué

El bot llevaba días sin emitir una señal y no había forma de saber por
qué. Tenía la respuesta y no la enseñaba.

### 1. Se perdían muestras en silencio

`_apuntar()` vivía **dentro** de `evaluar()`, detrás de tres salidas
tempranas. Y en el bucle, un símbolo con virtual abierta hacía
`continue` **antes** de llamar a `evaluar()`.

Consecuencia: cada fallo de descarga de OI y cada virtual abierta (hasta
4 h con `MAX_BARS=16`) abría un **hueco** en la historia de ese símbolo.
El calentamiento exige 200 muestras; los símbolos con más huecos tardan
el doble o no llegan.

Ahora el muestreo va primero y es incondicional: si hay dato, se apunta.
Las puertas se miran después y no tocan la historia.

### 2. El panel también estaba vacío

`acc.append()` iba después del `return` de "calentando". Mientras algún
símbolo calentara, no entraba en el panel. Sin señales **y** sin panel no
hay nada que analizar con `analizar_panel.py`.

### 3. El embudo no decía lo que hacía falta

Contaba cuántos morían en cada puerta, pero no **a qué distancia**.
`sin amontonamiento: 300` no distingue dos casos que piden arreglos
opuestos:

- el mercado está plano y el umbral es correcto → esperar
- el umbral está fuera del alcance del mercado → bajarlo o cambiar de modo

Ahora, **con cero señales el log imprime la AUTOPSIA**:

```
AUTOPSIA · 60 símbolos listos de 60
  basis_z alto   p50 -0.04 p90 +2.33 p99 +3.28 máx +3.28 · umbral >=+2.00 · pasan 7
  basis_z bajo   p50 -0.04 p10 -2.30 p01 -2.77 mín -2.77 · umbral <=-2.00 · pasan 9
  oi_z           p50 -0.01 p90 +0.01 p99 +0.03 máx +0.03 · umbral >=+1.00 · pasan 0  ← INALCANZABLE HOY
  pct_precio     p50 +52.00 p90 +97.00 p99 +100.00 máx +100.00 · umbral >=+80.00 · pasan 18
  coste_r        p50 +0.13 p10 +0.11 p01 +0.10 mín +0.10 · umbral <=+0.20 · pasan 60
  atr_pct        p50 +1.34 p10 +1.19 p01 +1.07 mín +1.07 · umbral <=+8.00 · pasan 60
  AMONTONADOS (las tres a la vez): 0 largos · 0 cortos
```

`← INALCANZABLE HOY` significa que **ni el símbolo más extremo del
universo** llega al umbral. Eso no es el mercado: es el umbral.

Cada línea enseña la cola que importa: percentiles altos para los
umbrales `>=`, bajos para los `<=`. Mirar siempre la cola de arriba fue
justo lo que escondía el problema.

### 4. La ventana del z crecía sola

El z se calculaba contra **toda** la historia guardada (hasta 168 h), así
que se iba estrechando solo: al crecer la ventana entran tramos de
volatilidad alta, la desviación típica sube y el mismo movimiento deja de
ser 2 sigma.

Medido por simulación (4.000 repeticiones):

| ventana | ≈ horas | iid (control) | con regímenes de volatilidad |
|---|---|---|---|
| 200 | 30 | 2.70% | **4.10%** |
| 400 | 60 | 2.15% | 4.38% |
| 800 | 120 | 2.25% | 3.38% |
| 2000 | 300 | 2.23% | **3.60%** |

Con ruido iid la ventana casi no importa: el z es invariante a la escala.
Con regímenes, la tasa de disparo cae. Es real pero **secundario** — de
4% a 3% no explica un cero. Aun así `Z_WIN_HORAS` (48 h por defecto) es
ahora independiente de `HIST_HORAS` (retención). Ponlo a 0 para volver al
comportamiento anterior.

### 5. Disparo sobre la vela en curso

`velas[-1]` es la vela **sin cerrar**. Una señal podía aparecer y
desaparecer dentro del mismo bar, y la `entrada` apuntada no era
reproducible ni releyendo el CSV. Con `VELA_CERRADA=true` (por defecto)
la vela de disparo es la última cerrada: menos señales, todas repetibles.

### 6. Aviso de silencio económico

Un bot vivo y mudo no da error en los logs. `MUDO_ALERTA_H=12` manda la
autopsia por Telegram cuando lleva N horas sin emitir **y** los símbolos
ya están listos — o sea, cuando el silencio no es calentamiento.

## Selección absoluta vs transversal

```
SELECCION=absoluto      # z >= Z_BASIS contra la propia historia (defecto)
SELECCION=transversal   # el XS_TOP_PCT% más extremo del universo AHORA
```

En modo **absoluto** la tasa de señales depende de la volatilidad general:
en calma, cero. En modo **transversal** siempre existe un 2% más extremo,
así que siempre hay candidatos y la muestra deja de depender del mercado.

**Aviso honesto: eso arregla la MUESTRA, no la ventaja.** Ser el más
extremo de un universo plano no es estar amontonado. Por eso `XS_Z_MIN`
pone un suelo por debajo del cual no se opera aunque se sea el primero.

## Antes de tocar umbrales: lee la autopsia

1. `AUTOPSIA · ninguno listo` → es calentamiento o el volumen no persiste.
2. `← INALCANZABLE HOY` en una línea → ese umbral está fuera del mercado.
3. `AMONTONADOS: 0 largos · 0 cortos` con todos los umbrales alcanzables
   → cada condición se cumple por separado pero nunca a la vez.
4. Amontonados > 0 y cero señales → mueren en amplitud, coste o en la
   vela en contra.

## Despliegue

1. Proyecto de Railway desde este repo.
2. **Monta un Volume en `/data`.** Sin él cada redespliegue borra la
   historia y el calentamiento no termina nunca. El bot ahora avisa en el
   log con un WARNING cuando arranca sin estado previo.
3. Variables (abajo).

## Calentamiento

BingX no sirve histórico de open interest, así que el bot acumula el
suyo. No emite nada hasta cumplir **las dos** condiciones: `MIN_HORAS` de
span **y** `MIN_MUESTRAS` puntos.

Con `MUESTRA_MIN_SEG=300` y una cadencia de ciclo de 2-4,5 min, la
cadencia **efectiva** de muestreo es de 6-9 min, así que 200 muestras son
20-30 h. El límite real lo pone `MIN_HORAS=30`.

## Variables

```
TIMEFRAME=15m
SCAN_SEC=120
MIN_VOL_24H=2000000
MAX_SYMBOLS=300

HIST_HORAS=168          # retención de la historia
Z_WIN_HORAS=48          # ventana del z-score (0 = toda la historia)
MIN_HORAS=30
MIN_MUESTRAS=200
MUESTRA_MIN_SEG=300
MAX_PUNTOS=2200
OI_LOOK_H=6

SELECCION=absoluto      # absoluto | transversal
XS_TOP_PCT=2.0
XS_Z_MIN=1.0
Z_BASIS=2.0
Z_OI=1.0
EXT_PCT=80

VELA_CERRADA=true
ATR_LEN=14
SL_ATR=1.5
TP_R=2.0
MAX_BARS=16
MIN_ATR_PCT=0.0
MAX_ATR_PCT=8.0
COST_PCT=0.25
MAX_COST_R=0.20

KLINES_LIMIT=260
MAX_WORKERS=20
PANEL_ENABLED=true
PANEL_CSV=/data/crowding_panel.csv
PANEL_CADA_SEG=900
PANEL_MIN_SIMBOLOS=30

CONFIRM_ENABLED=true
CONFIRM_Q=8
CONFIRM_WIN=240
CONFIRM_LAMBDA=0.985
CONFIRM_Z=1.5
CONFIRM_MIN_VELAS=120

STATE=/data/crowding_state.json
CSV=/data/crowding_ops.csv
TG_TOKEN=
TG_CHAT=
TG_SIGNALS=false
TG_CLOSES=false
REPORT_HOUR=7
MUDO_ALERTA_H=12
CSV_CON_INFORME=true
```

## Muestra necesaria

| ventaja real | operaciones |
|---|---|
| 0.50 R/op | 31 |
| 0.30 R/op | 87 |
| 0.20 R/op | 196 |
| 0.10 R/op | 784 |

## Régimen (confirm.py)

Se **apunta, nunca decide**. El crowding opera contra la multitud, o sea
que es reversión, y el veto de `confirm.py` está pensado para ruptura: le
quitaría justo sus mejores entradas. La columna `conf_regimen` del CSV
sirve para contestar a los 15 días si rinde mejor en régimen reversivo,
en vez de darlo por hecho.

## Salida

- `/data/crowding_ops.csv` — una fila por virtual cerrada, con `mfe`,
  `mae`, `conf_z` y `conf_regimen`
- `/data/crowding_panel.csv` — el panel transversal (`analizar_panel.py`)
- `/data/crowding_state.json` — historia de basis y OI, virtuales abiertas
