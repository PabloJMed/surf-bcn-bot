# surf-bcn-bot

Bot que cada mañana, hacia las 7:00 hora de Madrid, manda por Telegram si **hoy y mañana**
merece la pena surfear entre Barcelona y Sitges, en qué spot, en qué franja y con qué tabla.
Evalúa a la vez **tres niveles**: principiante, intermedio y avanzado.

La prioridad es el realismo: prefiere decir "no merece la pena" a mandarte al agua con mar de
viento revuelto, periodo de 4 s o temporal.

Python ≥ 3.11, solo librería estándar. Se ejecuta en GitHub Actions.

## Los mensajes

Cada mañana llegan **dos mensajes**: uno para hoy y otro para mañana, cada uno con su propio aviso.

```
🏄 Surf BCN → Sitges · MAÑANA sáb 24 oct

🟢 MERECE LA PENA 🤔
📍 Barceloneta, mejor de 8 a 15 h
 8h 🟡🟢🟢🟢🟢🟢🟡🔴🔴🔴🔴 19h
🌊 Olas de 0,6 m · periodo 4,5 s · entran del E
💨 Viento terral (de tierra, ordena la ola) del O, 7 km/h
⏱️ Desde las 15 h: viento de mar 9 km/h
🤔 Al límite: mira la webcam antes de ir

Por nivel
• Principiante: 🟢 Barceloneta, 8–15 h · softboard 🤔
• Intermedio: 🟡 Ginesta, 9–13 h · pequeño, mejor longboard
• Avanzado: 🟡 Ginesta, 9–13 h · pequeño, mejor longboard

Otras playas para intentarlo (principiante)
• 🟡 Bogatell, 8–15 h 🤔
• 🟡 Castelldefels, 9–13 h

📷 Webcams: Barceloneta · Ginesta

🌡️ Agua 22 °C → shorty o 3/2
🟢 merece la pena · 🟡 justito · 🔴 no merece la pena · 🤔 dudoso
Datos: Open-Meteo.com (CC BY 4.0)
```

Un día malo es una sola línea: `🔴 NO MERECE LA PENA · viento de mar 25 km/h: revuelto`.

- **La franja** ("mejor de 8 a 15 h") es el tramo seguido más largo de horas de luz que son al
  menos 🟡 en esa playa. La **tira de bolas** es cada hora de luz de esa playa, de izquierda a
  derecha, y las líneas ⏱️ dicen qué estropea el mar justo antes y justo después de la franja.
- El titular es la mejor playa para el nivel más bajo que tiene día; debajo, **una línea por nivel**,
  siempre con playa, franja y tabla sugerida.
- **Otras playas para intentarlo**: solo las que no están en rojo y tienen al menos 2 horas
  seguidas aprovechables, para el mismo nivel que el titular. Las demás no se mencionan.
- **Webcams**: solo de las playas recomendadas ese día (también las dudosas). Son páginas de
  surf-forecast o Windfinder que reúnen las cámaras cercanas y enseñan la hora de cada imagen.
- El aviso de cada mensaje **suena** si ese día algún nivel tiene al menos 2 horas seguidas de día
  bueno de verdad; si no, llega en silencio. Siempre llega algo: si Open-Meteo falla, recibes
  "⚠️ Hoy no he podido obtener la previsión", para que el silencio nunca se confunda con un día flat.

## Cómo puntúa

Todo es hora a hora, entre el amanecer y el atardecer, y todos los números están en
[`config.toml`](config.toml).

1. **Altura efectiva** `Heff = altura × dir_factor × exposure`. `dir_factor` vale 1 si la ola entra
   de frente (±30° del `facing` del spot) y cae a 0 a partir de 110°: el spot queda a la sombra.
2. **Vetos**, en orden: ⛔ peligroso (Heff, viento o rachas por encima del techo del nivel) →
   ⚪ flat → 🔴 periodo de pico demasiado corto → 🔴 viento de mar fuerte.
3. **Puntuación 0–10**: media de una nota de tamaño (curva propia de cada nivel) y una de periodo,
   acotada para que una ola grande de periodo corto no puntúe alto. Se ajusta por viento
   (terral flojo +1, de mar −1 a −5, cruzado 0 a −4) y +0,5 si domina el mar de fondo de verdad
   (≥ 7 s de periodo; el mar de viento no cuenta).
4. **Veredicto**: ≥ 6,5 🟢 · 4,5–6,5 🟡 · < 4,5 🔴. A menos de 0,5 de un umbral: 🤔 dudoso.
   Con menos de 5 s de periodo de pico, como mucho 🟡: por buen viento que haga, una ola tan
   corta llega floja y rompe toda a la vez.
5. **Día**: la ventana es la racha más larga de horas ≥ 4,5 y su media da el color. Una ventana de
   una sola hora queda como mucho en 🟡 y no hace sonar el aviso.

### Tres niveles, modelo acumulativo

Lo que vale para un principiante nunca sale en rojo para un avanzado: un avanzado le saca partido
a un día pequeño con longboard. Lo que cambia entre niveles es sobre todo **el techo de peligro**
y la **tabla sugerida**.

| | Principiante | Intermedio | Avanzado |
|---|---|---|---|
| Techo de Heff (a 6 s) | 1,10 m | 1,80 m | 2,80 m |
| Viento / rachas máx. | 35 / 45 km/h | 38 / 50 | 45 / 58 |
| Veto de viento de mar | > 22 km/h | > 24 | > 26 |
| Flat por debajo de | 0,30 m | 0,30 m | 0,25 m |
| Tamaño ideal desde | 0,45 m | 0,75 m | 1,10 m |

- El techo baja con el periodo (a más periodo, más energía por metro), de forma continua hasta
  un 80 % a partir de unos 9,6 s.
- **Pinza A**: por debajo del tamaño ideal del nivel, la nota no pasa de 6,0. Es lo que hace que un
  día de 0,6 m sea 🟡 "flojo → longboard" para un avanzado y no 🟢.
- **Pinza B**: si un nivel inferior tiene 🟢/🟡, el superior no baja de 4,8. Es la garantía del
  modelo acumulativo, y los tests la comprueban sobre miles de combinaciones.
- El sonido del aviso no cuenta esos 🟡 de cortesía: solo cuenta la nota sin pinzas y cuando la
  ola llega al tamaño ideal del nivel. Si no, el bot pitaría cada mañana por el avanzado.

**Tabla sugerida**, con el índice de empuje `P = Heff × T` (m·s): principiante siempre softboard
(con aviso "solo acompañado" por encima de 0,85 m); intermedio longboard < 4 ≤ funboard-mini < 7 ≤
shortboard; avanzado longboard < 4,5 ≤ funboard-mini < 7,5 ≤ shortboard < 14 ≤ step-up.

### Spots

| Spot | facing | Niveles | Notas |
|---|---|---|---|
| Barceloneta / Sant Sebastià | 135° | todos | Shorebreak. Mucha gente. |
| Bogatell / Nova Mar Bella | 140° | todos | Corrientes junto a los espigones. |
| Platja del Prat | 160° | todos | Larga y abierta. Tras lluvias, cerca del Llobregat. Sin webcam. |
| Castelldefels | 150° | todos | Techo de principiante 0,95 m por el shorebreak. |
| Port Ginesta | 155° | todos | Techo de principiante 0,60 m por el rip del espigón. |
| Garraf | 165° | intermedio, avanzado | Rocas y corrientes. Nunca se recomienda a principiante. |
| Sitges | 160° | todos | Fondo tendido. |

### Avisos

- 🧹 **Día de limpieza**: en las 24 h previas el oleaje fue igual o mayor y sopló viento de mar de
  más de 15 km/h de media, y hoy hay terral o calma. Suelen ser los mejores días del Mediterráneo.
- 💩 **Calidad del agua**: ≥ 15 mm de lluvia en 48 h en Barcelona o El Prat. Los colectores desbordan al mar:
  evita el baño 48 h y aléjate del Llobregat y el Besòs.
- 🌡️ **Neopreno** según la temperatura del agua.

## Los datos y sus límites

- [Open-Meteo Marine](https://open-meteo.com/en/docs/marine-weather-api) (modelo por defecto) para
  altura, dirección, swell y temperatura del agua; `ecmwf_wam025` para el **periodo de pico**, el
  único modelo que lo da en esta costa. Si falla, se estima como periodo medio × 1,13 (relación
  medida en esta zona) y el mensaje lo marca con `*`.
- [Open-Meteo Forecast](https://open-meteo.com/en/docs) para viento, rachas, lluvia, amanecer y
  atardecer.
- **La rejilla marina es grosera** (0,08°–0,25°): varios spots comparten celda y la diferencia entre
  ellos la ponen sobre todo `facing` y `exposure`. Por eso el mensaje agrupa los spots que comparten
  celda y dan lo mismo.

## Ajustar `config.toml`

- **Calibrar un spot**: `exposure` (multiplica la altura; < 1 si el spot recibe menos ola que el
  modelo) y `facing`. Usa `data/history.csv`, que guarda una fila por día, spot y nivel.
- **Endurecer un spot para un nivel**: `[spots.<id>.overrides.<nivel>]`.
- **Umbrales de cada nivel**: `[levels.*]`. Mantén la regla de oro: los techos nunca bajan al subir
  de nivel, y `flat_threshold` y `t_peak_min` nunca suben. Los tests fallan si la rompes.
- **Webcam**: `webcam_url` de cada spot; el enlace sale solo cuando ese spot se recomienda.

Para depurar, `--explain` enseña cada hora con las dos notas y el motivo:

```bash
python -m surfbot --explain castelldefels --level principiante
```

## Uso

```bash
python -m surfbot --dry-run              # descarga datos reales e imprime el mensaje
python -m surfbot --send                 # envía (requiere las variables de entorno)
python -m surfbot --send --force         # envía aunque ya se haya enviado hoy
python -m surfbot --get-chat-id          # imprime los chat_id que han escrito al bot
python -m surfbot --explain <spot>       # tabla hora a hora por nivel
python -m unittest                       # tests, sin red
```

Con Python < 3.11 en local: `uv run --no-project --python 3.12 python -m surfbot --dry-run`.

## Puesta en marcha

1. En Telegram, habla con [@BotFather](https://t.me/BotFather), `/newbot`, y manda `/start` a tu bot.
2. En **tu** terminal, sin pegarlo en ningún chat:
   ```bash
   read -s TELEGRAM_BOT_TOKEN && export TELEGRAM_BOT_TOKEN
   python -m surfbot --get-chat-id
   ```
   En local también puedes usar un `.env` (está en `.gitignore`).
3. Guarda los secretos en el repo (los piden con entrada oculta):
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN
   gh secret set TELEGRAM_CHAT_ID
   ```
4. Prueba: `gh workflow run daily.yml -f force=true` y `gh run watch`.

El workflow corre a las 4:47 y 5:47 UTC. El script envía en la primera ejecución con hora de
Madrid ≥ 06:30 que no haya enviado ya hoy (`data/state.json`), así que funciona en horario de
verano y de invierno y aguanta los retrasos de GitHub. El commit diario de `data/` mantiene el
repo activo: GitHub desactiva los cron de repos públicos tras 60 días sin actividad.

## Seguridad

- El token y el chat_id solo viven en GitHub Actions Secrets y en tu entorno local.
- El código nunca imprime el token ni la URL de la API; ante un error de Telegram solo muestra el
  código HTTP y la descripción.
- Antes de cada push conviene comprobar que esto da vacío:
  ```bash
  git grep -nE '[0-9]{8,10}:[A-Za-z0-9_-]{35}'
  ```

### Si el token se filtra

1. En @BotFather: `/revoke`, elige el bot y copia el token nuevo. El viejo deja de funcionar al momento.
2. `gh secret set TELEGRAM_BOT_TOKEN` con el nuevo.
3. Si estaba en un commit, reescribir el historial no basta: el token ya está revocado, que es lo
   que importa.

## Fase 2 (preparada, no implementada)

- **Feedback para calibrar**: leer con `getUpdates` respuestas como `fui castelldefels 3` (nota 1–5)
  y guardarlas para ajustar `exposure`, `facing` y umbrales. Ojo: en un repo público eso revela
  cuándo vas a surfear; mejor guardarlo en un gist privado. `telegram.py` ya tiene `getUpdates`.
- **Boya de Barcelona** de Puertos del Estado (THREDDS en `opendap.puertos.es`) para contrastar el
  modelo con lo medido.
- **Alerta anticipada** cuando se prevea un día 🟢 a 2–3 días vista (la API ya da 3 días).

## Atribución

Datos: [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).

Licencia del código: MIT.
