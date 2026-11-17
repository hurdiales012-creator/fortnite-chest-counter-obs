# Colaborar

Usá Python 3.12 en Windows y ejecutá las pruebas con `python -m pytest` tras instalar `requirements-dev.txt`.

Describí en cada cambio qué problema resuelve y cómo se comprobó. Si modificás el OCR o el bloqueo contra duplicados, añadí una prueba que cubra el caso y una negativa que descarte el falso positivo.

Generá las imágenes de la interfaz con `--demo`. No añadas `datos/`, configuraciones locales, contraseñas, grabaciones o nombres privados. El archivo `config.example.json` debe seguir siendo genérico.

El método actual depende del aviso de recompensa. Una mejora para destrucciones necesita evidencia y pruebas de eventos reales; no debe anunciarse como conteo exacto si solo detecta una explosión o la desaparición de un objeto.
