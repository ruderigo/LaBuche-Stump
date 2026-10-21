# Provisioner

L'outil qui installe et met à jour les nœuds Stump, depuis un ordinateur branché en USB : la version exacte avec laquelle ce nœud a été installé.

## Ce qu'il y a ici

- `provisioner.py` — le Provisioner lui-même.
- `Stump_Beta_A.zip` — le code source du micrologiciel de ce nœud.
- `Stump-README.md` — le manuel complet (en anglais).
- `LICENSE` — la licence de l'ensemble (MIT).

## L'utiliser

Télécharge les fichiers dans un même dossier et décompresse
`Stump_Beta_A.zip` à cet endroit (il contient `final_firmware/`). Puis,
avec le nœud branché en USB :

    python3 provisioner.py --check-tools     # esptool, mpremote, rnodeconf
    python3 provisioner.py                   # l'assistant

Pour mettre à jour un nœud déjà installé en gardant son identité et sa
carte SD : `python3 provisioner.py --upload-app PORT`. Tout le reste est
dans `Stump-README.md`.

Pour une nouvelle installation, la version publiée sur
https://github.com/ruderigo/SandBox_Stump_On/releases contient la même
chose en un seul zip.
