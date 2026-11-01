# Provisioner

La herramienta que instala y actualiza los nodos Stump desde una computadora conectada por USB: la versión exacta con la que se instaló este nodo.

## Qué hay aquí

- `provisioner.py`: el Provisioner.
- `Stump-<versión>.zip` (por ejemplo `Stump-1.0.0-beta.35.zip`): el código fuente del firmware de este nodo.
- `Stump-README.md`: el manual completo (en inglés).
- `LICENSE`: la licencia de todo el conjunto (MIT).

## Cómo usarlo

Descarga los archivos en una misma carpeta y descomprime
`Stump-<versión>.zip` ahí (contiene `final_firmware/`). Después, con el
nodo conectado por USB:

    python3 provisioner.py --check-tools     # esptool, mpremote, rnodeconf
    python3 provisioner.py                   # el asistente

Para actualizar un nodo ya instalado conservando su identidad y su
tarjeta SD: `python3 provisioner.py --upload-app PUERTO`. Todo lo demás
está en `Stump-README.md`.

Para una instalación nueva, la versión publicada en
https://github.com/ruderigo/LaBuche-Stump/releases trae lo mismo en
un solo zip.
