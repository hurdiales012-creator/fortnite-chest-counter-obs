# Privacidad y archivos locales

El contador funciona localmente. Durante el uso normal, se comunica con OBS por WebSocket en `127.0.0.1` y sirve el panel en esa misma dirección. No contiene telemetría, acceso a una cuenta de Epic ni subida de capturas a servicios externos.

La instalación descarga Python o paquetes cuando el método elegido lo necesita. Eso no incluye enviar capturas o conteos del juego.

## Datos que puede guardar el programa

- `datos/config.local.json`: nombres de escena y captura, región OCR y ruta opcional de OBS portable.
- `datos/cuenta.json` y `datos/total-nativo.txt`: el total del usuario.
- `datos/detector.log`: eventos del contador y tipos de errores de conexión.
- `datos/validacion-clips.json`: informe opcional de análisis, con nombres de archivos locales.
- `.demo-data/`: datos de demostración separados del uso normal.

Todos esos archivos se excluyen de Git. Las variables `COFRES_OBS_PASSWORD`, `COFRES_OBS_CONFIG`, `COFRES_OBS_PORT` y `COFRES_DATA_DIR` son opciones locales; no deben copiarse a archivos que se publiquen.

## Antes de publicar tu propia copia

1. Revisá `git status` y los archivos que vas a añadir. `.gitignore` no retira un archivo que ya estuviera versionado.
2. No incluyas `datos/`, `.env`, credenciales de OBS o GitHub, grabaciones, repeticiones ni registros.
3. Generá las imágenes de documentación con `--demo`; no uses capturas de tu partida o cuenta sin revisarlas.
4. Revisá los metadatos de las imágenes y que ningún documento incluya nombres, rutas o totales privados.

Las imágenes incluidas en esta versión se generaron con nombres genéricos y un total de demostración. El proyecto no necesita datos de su creador para instalarse.
