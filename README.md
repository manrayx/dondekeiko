# ¿Dónde está Keiko? · Por MNRY

Sitio estático que se actualiza solo. Cada 30 minutos, GitHub Actions lee los feeds de noticias, guarda los titulares tal como los publica cada medio y actualiza `public/data.json`. Render publica el sitio cada vez que ese archivo cambia.

## Estructura

```
public/            Lo que publica Render
  index.html       La página
  data.json        Titulares (lo escribe el robot)
  peru.json        Mapa del Perú
  img/             Sprites por estado
scripts/
  update.py        Recolector de titulares
  config.json      Feeds, palabras clave, lugares y textos de cada estado
.github/workflows/actualizar.yml   Programación cada 30 minutos
render.yaml        Configuración del sitio en Render
```

## Puesta en marcha

1. **GitHub:** crea un repositorio nuevo y sube todo el contenido de esta carpeta.
2. **Permisos del robot:** en el repositorio, entra a *Settings → Actions → General → Workflow permissions* y marca **Read and write permissions**.
3. **Primera carga:** en la pestaña *Actions*, abre **Actualizar titulares** y pulsa **Run workflow**. En un minuto, `public/data.json` tendrá los primeros titulares.
4. **Render:** crea un *New → Static Site* (o *Blueprint*, que lee `render.yaml`), conecta el repositorio y usa `public` como *Publish directory*, sin build command. Render vuelve a publicar solo con cada actualización.

## Ajustes sin tocar código

Todo está en `scripts/config.json`:

- `feeds`: los medios. Para sumar uno, agrega su URL de RSS. En el registro de cada ejecución (pestaña *Actions*) se ve cuántas entradas trajo cada feed; si alguno marca 0 de forma constante, su URL cambió.
- `states`: el nombre, color, frase y palabras clave de cada pose. El orden de prioridad está en `order`. Las palabras se comparan sin tildes.
- `places` y `abroad`: lugares que se reconocen en los titulares, con sus coordenadas.
- `here_verbs` y `next_verbs`: las expresiones que indican dónde está («llegó a», «desde») y adónde irá («viajará a», «visitará»).
- `silence_hours`: horas sin notas para que aparezca «Paradero desconocido».

Para cambiar los sprites, reemplaza los PNG de `public/img/` manteniendo los nombres.
