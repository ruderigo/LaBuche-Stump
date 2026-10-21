# rnfs — système de fichiers Reticulum

Des fichiers sur le réseau Reticulum (LoRa compris), récupérés par leur identifiant de contenu — écrit par Afi Hogan (afit21). Tout ce qu'il faut ici pour l'installer sur n'importe quel ordinateur avec Python 3.11 ou plus récent, sans internet.

https://github.com/afit21/rnfs   (licence MIT, voir rnfs-LICENSE.txt)

    rnfs add vacances.jpg         -> affiche un CID
    rnfs get <cid> -o vacances.jpg

Lance d'abord « rnfs daemon » et laisse-le tourner ; toutes les autres
commandes passent par lui.

## L'installer depuis ce Stump

Il te faut Python 3.11 ou plus récent (python.org sur Windows et macOS ;
la plupart des Linux l'ont déjà). Télécharge ces fichiers depuis cette
section de la page Outils dans UN MÊME dossier (Téléchargements, par
exemple) :

    rnfs-0.1.1-py3-none-any.whl      rnfs lui-même
    lxmf-1.2.0-py3-none-any.whl      la messagerie LXMF
    rnspure-1.5.7-py3-none-any.whl   Reticulum, édition 100 % Python
    rnfs-install.sh                  (macOS, Linux)   ou
    rnfs-install.bat                 (Windows)

puis lance l'installateur depuis ce dossier :

    macOS / Linux :   sh rnfs-install.sh
    Windows :         double-clique sur rnfs-install.bat

Il s'installe dans son propre dossier (~/.rnfs-venv) et ne touche à
rien d'autre. Les trois sont en pur Python : ils marchent sur n'importe
quel ordinateur.

rnspure, c'est Reticulum avec sa propre cryptographie intégrée au lieu
du paquet « cryptography » — c'est ce qui permet une installation hors
ligne. Il est plus lent (« slow: True » dans « rnfs status »), ce qui
compte peu pour partager des fichiers. Plus tard, avec internet,
« pip install rns » dans le même environnement passe à la version
rapide.

À la main, sans le script :

    python3 -m venv ~/.rnfs-venv
    ~/.rnfs-venv/bin/pip install --no-index --no-deps \
        rnspure-1.5.7-py3-none-any.whl lxmf-1.2.0-py3-none-any.whl rnfs-0.1.1-py3-none-any.whl

(« pip check » dit alors que rns n'est pas installé : c'est normal,
rnspure en tient lieu.)

## Installateurs autonomes

Les versions publiées du projet ont aussi des installateurs en un seul
fichier, avec le Reticulum rapide intégré :

    Windows 64 bits        rnfs-<version>-windows-x64.exe  (à renommer rnfs.exe)
    Debian 12+, Ubuntu     rnfs_<version>_amd64.deb / _arm64.deb
    24.04+, Raspberry Pi   sudo apt install ./rnfs_<version>_amd64.deb
    OS 64 bits

Ils pèsent des dizaines de Mo : si ce Stump les a, le technicien les a
copiés sur sa carte SD (dans tools/rnfs/) avec un lecteur de cartes, et
ils sont listés ici aussi.

## Sur ce Stump

Dans le clavardage : !shelf liste les fichiers que les gens d'ici
recommandent, !shelf add <cid> <titre> en ajoute un, et les liens rnfs
partagés dans le clavardage sont rassemblés sur la page /p/rnfs/.

Si ce Stump fait tourner un nœud de propagation (messages gardés pour
les gens hors ligne), les messages LXMF de rnfs peuvent s'en servir :
mets son adresse dans ~/.rnfs/rnfs.toml sous la forme
lxmf_propagation_node = "<32 chiffres hexadécimaux>"

## Remerciements

- **rnfs** est écrit par **Afi Hogan** ([afit21](https://github.com/afit21)) :
  https://github.com/afit21/rnfs — licence MIT, voir rnfs-LICENSE.txt.
  Le paquet (wheel) d'ici est construit tel quel depuis sa version
  source v0.1.1.
- **Reticulum** (ici sous la forme de rnspure, son édition 100 % Python)
  et **LXMF** sont de **Mark Qvist** : https://reticulum.network —
  licence Reticulum, voir reticulum-LICENSE.txt.

Ce Stump ne fait que les distribuer, pour qu'on puisse les obtenir sans
internet. Les questions sur rnfs, les signalements de bogues et les
remerciements vont à son auteur, sur la page du projet.
