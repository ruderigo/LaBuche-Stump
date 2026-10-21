# rnfs — sistema de archivos de Reticulum

Archivos en la red Reticulum (LoRa incluida), obtenidos por su identificador de contenido; escrito por Afi Hogan (afit21). Aquí está todo lo necesario para instalarlo en cualquier computadora con Python 3.11 o más reciente, sin internet.

https://github.com/afit21/rnfs   (licencia MIT, ver rnfs-LICENSE.txt)

    rnfs add vacaciones.jpg       -> muestra un CID
    rnfs get <cid> -o vacaciones.jpg

Primero ejecuta "rnfs daemon" y déjalo corriendo; todos los demás
comandos pasan por él.

## Instalarlo desde este Stump

Necesitas Python 3.11 o más reciente (python.org en Windows y macOS; la
mayoría de los Linux ya lo tienen). Descarga estos archivos desde esta
sección de la página de herramientas en UNA MISMA carpeta (Descargas,
por ejemplo):

    rnfs-0.1.1-py3-none-any.whl      rnfs
    lxmf-1.2.0-py3-none-any.whl      la mensajería LXMF
    rnspure-1.5.7-py3-none-any.whl   Reticulum, edición 100 % Python
    rnfs-install.sh                  (macOS, Linux)   o
    rnfs-install.bat                 (Windows)

y ejecuta el instalador desde esa carpeta:

    macOS / Linux:   sh rnfs-install.sh
    Windows:         doble clic en rnfs-install.bat

Se instala en su propia carpeta (~/.rnfs-venv) y no toca nada más. Los
tres son Python puro: funcionan en cualquier computadora.

rnspure es Reticulum con su propia criptografía integrada en lugar del
paquete "cryptography"; eso es lo que permite instalarlo sin conexión.
Es más lento ("slow: True" en "rnfs status"), lo que importa poco para
compartir archivos. Más adelante, con internet, "pip install rns" en el
mismo entorno cambia a la versión rápida.

A mano, sin el script:

    python3 -m venv ~/.rnfs-venv
    ~/.rnfs-venv/bin/pip install --no-index --no-deps \
        rnspure-1.5.7-py3-none-any.whl lxmf-1.2.0-py3-none-any.whl rnfs-0.1.1-py3-none-any.whl

("pip check" dirá entonces que rns no está instalado: es normal,
rnspure lo reemplaza.)

## Instaladores independientes

Las versiones publicadas del proyecto también tienen instaladores de un
solo archivo, con el Reticulum rápido integrado:

    Windows 64 bits        rnfs-<version>-windows-x64.exe  (renómbralo rnfs.exe)
    Debian 12+, Ubuntu     rnfs_<version>_amd64.deb / _arm64.deb
    24.04+, Raspberry Pi   sudo apt install ./rnfs_<version>_amd64.deb
    OS 64 bits

Pesan decenas de MB: si este Stump los tiene, el técnico los copió a su
tarjeta SD (en tools/rnfs/) con un lector de tarjetas, y también
aparecen aquí.

## En este Stump

En el chat: !shelf lista los archivos que la gente de aquí recomienda,
!shelf add <cid> <título> agrega uno, y los enlaces rnfs compartidos en
el chat se reúnen en la página /p/rnfs/.

Si este Stump tiene un nodo de propagación (mensajes guardados para
quien está desconectado), los mensajes LXMF de rnfs pueden usarlo: pon
su dirección en ~/.rnfs/rnfs.toml así:
lxmf_propagation_node = "<32 dígitos hexadecimales>"

## Créditos

- **rnfs** está escrito por **Afi Hogan** ([afit21](https://github.com/afit21)):
  https://github.com/afit21/rnfs — licencia MIT, ver rnfs-LICENSE.txt.
  El paquete (wheel) de aquí está construido sin cambios desde su versión
  fuente v0.1.1.
- **Reticulum** (aquí como rnspure, su edición 100 % Python) y **LXMF**
  son de **Mark Qvist**: https://reticulum.network — licencia Reticulum,
  ver reticulum-LICENSE.txt.

Este Stump solo los reparte, para que se puedan obtener sin internet.
Las preguntas sobre rnfs, los reportes de errores y los agradecimientos
van a su autor, en la página del proyecto.
