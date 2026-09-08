# Project Stump -- i18n.py
#
# Three-language support for the walk-up interface: French (default),
# English, Spanish. French-first is deliberate -- Quebec's language
# rules aside, this is who the node is actually built for, and French
# should be the thing someone sees without having to ask for it.
#
# SCOPE, STATED HONESTLY
# ----------------------
# This covers the surfaces a walk-up visitor actually uses: BarKeep's
# home page chrome, the RRC chat page shell and its most common
# commands, Billboard, Files, and stumpid's identity/room-access
# messages. It does NOT cover: fservbot's dialogue CONTENT (that's
# operator-authored text set via /fsadd -- translating an operator's
# own words for them doesn't make sense; if they want a bilingual bot,
# that's a separate feature, not this one), the Tools/Flash pages
# (technician-facing, not the community-facing surface this exists
# for), or every rare edge-case error string in the codebase. Anything
# not in STRINGS falls back to English via t()'s own missing-key
# handling, rather than crashing or showing blank text.
#
# WHAT NEVER GETS TRANSLATED, EVER
# ---------------------------------
# stumpid's AUTH-CHALLENGE / AUTH-OK / AUTH-FAIL lines are wire-protocol
# markers Firefly's client parses by splitting on the first space --
# translating or reordering them would break that integration. They are
# built directly in stumpid/install.py, never routed through t(), and
# must stay that way.
#
# PER-CLIENT, NOT PER-NODE
# -------------------------
# Language is a per-VISITOR preference, keyed by the same client_id
# (peer IP for web, LXMF hash for mesh) already used for nick and
# verification state elsewhere in this project. Memory-only, like
# everything else session-scoped here -- a language choice doesn't
# need to survive a reboot any more than a nick does, and persisting it
# would mean tracking one more thing per visitor for no real benefit.

LANGUAGES = ("fr", "en", "es")
DEFAULT_LANG = "fr"

LANGUAGE_LABELS = {"fr": "FR", "en": "EN", "es": "ES"}

_prefs = {}   # client_id -> lang, memory-only


def get_lang(client_id):
    return _prefs.get(client_id, DEFAULT_LANG)


def set_lang(client_id, lang):
    if lang not in LANGUAGES:
        return False
    _prefs[client_id] = lang
    return True


def reset():
    """Clears all preferences. For tests."""
    _prefs.clear()


def _url_encode(s):
    """Minimal percent-encoding, matching barkeep.py's own encoder
    exactly. Duplicated rather than imported: barkeep.py already
    imports THIS module, so importing barkeep's encoder back would be
    a circular import -- the same kind this project has deliberately
    avoided elsewhere (billboard.py/fserv.py). A ~5-line duplication is
    the smaller cost."""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = ""
    for ch in s:
        if ch in safe:
            out += ch
        else:
            out += "%%%02X" % ord(ch)
    return out


def switcher_html(lang, current_path, css_class="langbar"):
    """FR | EN | ES, built once here so barkeep.py's server-rendered
    pages and rrc_ui.py's client page share one implementation instead
    of two that could drift -- exactly the pattern this project has
    paid for in bugs before (barkeep.py/fserv.py's duplicated HTTP
    handlers, most notably). The active language is shown plainly, not
    as a link -- clicking your own current language would be a
    confusing no-op. Every other language links to /lang, which sets
    the preference and redirects straight back to current_path."""
    parts = ["<div class='" + css_class + "'>"]
    for code in LANGUAGES:
        label = LANGUAGE_LABELS[code]
        if code == lang:
            parts.append("<span class='lang-active'>" + label + "</span>")
        else:
            parts.append("<a href='/lang?set=" + code + "&next=" +
                          _url_encode(current_path) + "'>" + label + "</a>")
    parts.append("</div>")
    return "".join(parts)


def t(key, lang=None, **kwargs):
    """Looks up a translated string and interpolates any keyword args.

    Falls back in two stages, neither of which crashes or shows nothing:
    an unknown LANGUAGE falls back to French (the node's default); an
    unknown KEY returns the key itself, so a typo'd lookup is visible
    and fixable rather than silently blank in production.
    """
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or entry.get("en") or key
    # Always run .format(), even with zero kwargs -- {{ }} literal-brace
    # escaping (used in strings like the /nick usage message, which
    # shows literal square/curly brackets with nothing to interpolate)
    # only resolves THROUGH .format() being called at all. Confirmed
    # directly: skipping the call when kwargs is empty left "{{ }}"
    # rendered as literal double braces instead of collapsing to "{ }".
    try:
        return text.format(**kwargs)
    except Exception:
        return text


# ---------------------------------------------------------------------
# Translation table
# ---------------------------------------------------------------------
# {key} placeholders are interpolated via t(key, lang, name="alice").
# Keep placeholder NAMES identical across all three languages for a
# given key -- t() calls .format(**kwargs) once against whichever
# string wins, so a placeholder present in French but named
# differently in Spanish would raise on that branch.

STRINGS = {
    # ---- BarKeep home page ----
    "barkeep_greeting": {
        "fr": "Tirez-vous une bûche. Je suis {bot_name}.",
        "en": "Pull up a log. I'm {bot_name}.",
        "es": "Acércate un tronco. Soy {bot_name}.",
    },
    "barkeep_evening": {
        "fr": "Bonsoir. Tape menu pour voir ce qu'il y a.",
        "en": "Evening. Type menu to see what's around.",
        "es": "Buenas noches. Escribe menu para ver qué hay.",
    },
    "nav_home": {"fr": "Accueil", "en": "Home", "es": "Inicio"},
    "nav_chat": {"fr": "Clavardage", "en": "Chat", "es": "Chat"},
    "nav_board": {"fr": "Babillard", "en": "Board", "es": "Tablón"},
    "nav_files": {"fr": "Fichiers", "en": "Files", "es": "Archivos"},
    "nav_tools": {"fr": "Outils", "en": "Tools", "es": "Herramientas"},
    "nav_flash": {"fr": "Flasher", "en": "Flash", "es": "Grabar"},
    "tile_chat_sub": {
        "fr": "clavarder avec les gens ici",
        "en": "talk to whoever's here",
        "es": "habla con quien esté aquí",
    },
    "tile_board_sub": {
        "fr": "avis et messages",
        "en": "notices & messages",
        "es": "avisos y mensajes",
    },
    "tile_files_sub": {
        "fr": "prends quelque chose",
        "en": "take something home",
        "es": "llévate algo",
    },
    "tile_about_sub": {
        "fr": "d'où ça vient, comment se connecter",
        "en": "what this is, how to connect",
        "es": "qué es esto, cómo conectarte",
    },

    # ---- RRC chat page ----
    "rrc_input_placeholder": {
        "fr": "message, ou /help",
        "en": "message, or /help",
        "es": "mensaje, o /help",
    },
    "rrc_send": {"fr": "Envoyer", "en": "Send", "es": "Enviar"},
    "rrc_you_are": {"fr": "tu es", "en": "you are", "es": "eres"},
    "rrc_not_sent": {
        "fr": "non envoyé — problème de connexion",
        "en": "not sent — connection problem",
        "es": "no enviado — problema de conexión",
    },

    # ---- rrc.py command replies ----
    "help_header": {
        "fr": "Voici ce que j'ai\u00a0:",
        "en": "Here's what I've got:",
        "es": "Esto es lo que tengo:",
    },
    "help_msg": {
        "fr": "message privé, seul(e) cette personne le voit",
        "en": "private message, only they see it",
        "es": "mensaje privado, solo esa persona lo ve",
    },
    "help_me": {
        "fr": "parler à la troisième personne",
        "en": "speak in the third person",
        "es": "hablar en tercera persona",
    },
    "help_nick": {
        "fr": "changer de nom",
        "en": "change your name",
        "es": "cambiar tu nombre",
    },
    "help_join": {
        "fr": "rejoindre ou créer une salle",
        "en": "join or create a room",
        "es": "unirte o crear una sala",
    },
    "help_part": {
        "fr": "quitter, retour à #main",
        "en": "leave, back to #main",
        "es": "salir, volver a #main",
    },
    "help_rooms": {
        "fr": "lister les salles",
        "en": "list rooms",
        "es": "listar las salas",
    },
    "help_names": {
        "fr": "qui est dans cette salle",
        "en": "who's in this room",
        "es": "quién está en esta sala",
    },
    "help_topic": {
        "fr": "changer le sujet de la salle",
        "en": "set the room topic",
        "es": "establecer el tema de la sala",
    },
    "help_clear": {
        "fr": "effacer ta propre vue",
        "en": "clear your own view",
        "es": "borrar tu propia vista",
    },
    "help_help": {
        "fr": "cette liste",
        "en": "this list",
        "es": "esta lista",
    },
    "rooms_here": {"fr": "ici", "en": "here", "es": "aquí"},
    "nick_usage": {
        "fr": "usage\u00a0: /nick <nom>  (lettres, chiffres, - _ [ ] {{ }})",
        "en": "usage: /nick <name>  (letters, numbers, - _ [ ] {{ }})",
        "es": "uso: /nick <nombre>  (letras, números, - _ [ ] {{ }})",
    },
    "nick_already": {
        "fr": "c'est déjà ton nom",
        "en": "that's already your name",
        "es": "ese ya es tu nombre",
    },
    "nick_taken": {
        "fr": "« {nick} » est déjà pris ici",
        "en": "'{nick}' is taken in here",
        "es": "«{nick}» ya está en uso aquí",
    },
    "nick_changed": {
        "fr": "{old} s'appelle maintenant {new}",
        "en": "{old} is now known as {new}",
        "es": "{old} ahora se llama {new}",
    },
    "join_usage": {
        "fr": "usage\u00a0: /join <salle>",
        "en": "usage: /join <room>",
        "es": "uso: /join <sala>",
    },
    "join_already": {
        "fr": "tu es déjà dans #{room}",
        "en": "you're already in #{room}",
        "es": "ya estás en #{room}",
    },
    "join_notice": {
        "fr": "{nick} a rejoint",
        "en": "{nick} joined",
        "es": "{nick} se unió",
    },
    "left_notice": {
        "fr": "{nick} est parti(e)",
        "en": "{nick} left",
        "es": "{nick} salió",
    },
    "part_in_main": {
        "fr": "tu es dans #main — nulle part où retourner",
        "en": "you're in #main -- nowhere to part to",
        "es": "estás en #main — no hay adónde volver",
    },
    "room_gone": {
        "fr": "cette salle n'existe plus — essaie /join main",
        "en": "that room is gone -- try /join main",
        "es": "esa sala ya no existe — prueba /join main",
    },
    "names_here": {
        "fr": "dans #{room}\u00a0: {names}",
        "en": "in #{room}: {names}",
        "es": "en #{room}: {names}",
    },
    "just_you": {"fr": "(juste toi)", "en": "(just you)", "es": "(solo tú)"},
    "topic_usage": {
        "fr": "usage\u00a0: /topic <texte>",
        "en": "usage: /topic <text>",
        "es": "uso: /topic <texto>",
    },
    "topic_show": {
        "fr": "sujet de #{room}\u00a0: {topic}",
        "en": "#{room} topic: {topic}",
        "es": "tema de #{room}: {topic}",
    },
    "topic_none": {"fr": "(aucun)", "en": "(none set)", "es": "(ninguno)"},
    "topic_set": {
        "fr": "{nick} a changé le sujet\u00a0: {topic}",
        "en": "{nick} set the topic: {topic}",
        "es": "{nick} estableció el tema: {topic}",
    },
    "me_usage": {
        "fr": "usage\u00a0: /me <action>",
        "en": "usage: /me <action>",
        "es": "uso: /me <acción>",
    },
    "msg_usage": {
        "fr": "usage\u00a0: /msg <qui> <message>",
        "en": "usage: /msg <who> <message>",
        "es": "uso: /msg <quién> <mensaje>",
    },
    "msg_self": {
        "fr": "se parler à soi-même, c'est gratuit — essaie quelqu'un d'autre",
        "en": "talking to yourself is free -- try someone else",
        "es": "hablar solo es gratis — prueba con alguien más",
    },
    "msg_no_such_user": {
        "fr": "personne ici ne s'appelle « {nick} » — /names montre qui est là",
        "en": "no one here called '{nick}' -- /names shows who is",
        "es": "nadie aquí se llama «{nick}» — /names muestra quién está",
    },
    "msg_nothing_to_send": {
        "fr": "rien à envoyer",
        "en": "nothing to send",
        "es": "nada que enviar",
    },
    "msg_sent": {
        "fr": "-> {nick}\u00a0: {body}",
        "en": "-> {nick}: {body}",
        "es": "-> {nick}: {body}",
    },
    "unknown_command": {
        "fr": "commande inconnue\u00a0: /{cmd}  (essaie /help)",
        "en": "unknown command: /{cmd}  (try /help)",
        "es": "comando desconocido: /{cmd}  (prueba /help)",
    },

    # ---- BarKeep's own command console (the single-box /chat dispatcher
    # on the home page -- separate from RRC's multi-user chat. Command
    # WORDS (menu/help/chat/billboard/files/get/balance/upload) stay in
    # English deliberately, as a simple fixed vocabulary, consistent
    # with RRC's /slash commands also staying untranslated -- only the
    # displayed OUTPUT is translated. Making the command words
    # themselves multilingual is a real, bigger feature (a per-language
    # alias table) that this pass doesn't attempt. ----
    "bot_menu_header": {
        "fr": "Voici ce que j'ai\u00a0:<br>",
        "en": "Here's what I've got:<br>",
        "es": "Esto es lo que tengo:<br>",
    },
    "bot_menu_menu": {
        "fr": "&nbsp;&nbsp;<b>menu</b> — cette liste<br>",
        "en": "&nbsp;&nbsp;<b>menu</b> &mdash; this list<br>",
        "es": "&nbsp;&nbsp;<b>menu</b> — esta lista<br>",
    },
    "bot_menu_chat": {
        "fr": "&nbsp;&nbsp;<b>chat</b> — parler avec les gens ici<br>",
        "en": "&nbsp;&nbsp;<b>chat</b> &mdash; talk to whoever else is here<br>",
        "es": "&nbsp;&nbsp;<b>chat</b> — habla con quien esté aquí<br>",
    },
    "bot_menu_billboard": {
        "fr": "&nbsp;&nbsp;<b>billboard</b> — le babillard<br>",
        "en": "&nbsp;&nbsp;<b>billboard</b> &mdash; the notice board<br>",
        "es": "&nbsp;&nbsp;<b>billboard</b> — el tablón de avisos<br>",
    },
    "bot_menu_files": {
        "fr": "&nbsp;&nbsp;<b>files</b> — ce qu'il y a sur l'étagère<br>",
        "en": "&nbsp;&nbsp;<b>files</b> &mdash; what's on the shelf<br>",
        "es": "&nbsp;&nbsp;<b>files</b> — lo que hay en la estantería<br>",
    },
    "bot_menu_get": {
        "fr": "&nbsp;&nbsp;<b>get &lt;nom&gt;</b> — prendre quelque chose<br>",
        "en": "&nbsp;&nbsp;<b>get &lt;name&gt;</b> &mdash; take something home<br>",
        "es": "&nbsp;&nbsp;<b>get &lt;nombre&gt;</b> — llevarte algo<br>",
    },
    "bot_menu_balance": {
        "fr": "&nbsp;&nbsp;<b>balance</b> — ce que tu as accumulé<br>",
        "en": "&nbsp;&nbsp;<b>balance</b> &mdash; what you've got saved up<br>",
        "es": "&nbsp;&nbsp;<b>balance</b> — lo que tienes acumulado<br>",
    },
    "bot_menu_upload": {
        "fr": "&nbsp;&nbsp;<b>upload</b> — comment partager quelque chose",
        "en": "&nbsp;&nbsp;<b>upload</b> &mdash; how to bring something of your own",
        "es": "&nbsp;&nbsp;<b>upload</b> — cómo compartir algo tuyo",
    },
    "bot_upload_credit": {
        "fr": "Tu vois la case en dessous du chat\u00a0? Choisis un fichier et clique sur Envoyer — apporte quelque chose, et je te créditerai pour ça.",
        "en": "See that box below, under the chat? Pick a file there and hit Upload -- bring something, and I'll credit you for it.",
        "es": "¿Ves ese cuadro debajo del chat? Elige un archivo ahí y presiona Subir — trae algo y te daré crédito por eso.",
    },
    "bot_upload_free": {
        "fr": "Tu vois la case en dessous du chat\u00a0? Choisis un fichier et clique sur Envoyer — tout ce que tu laisses est là pour la prochaine personne.",
        "en": "See that box below, under the chat? Pick a file there and hit Upload -- anything you leave is there for the next person.",
        "es": "¿Ves ese cuadro debajo del chat? Elige un archivo ahí y presiona Subir — todo lo que dejes queda ahí para la próxima persona.",
    },
    "bot_chat_pointer": {
        "fr": "Tout le monde est ici — <a href='/rrc'>ouvre RRC</a>.",
        "en": "Everyone's in here &mdash; <a href='/rrc'>open RRC</a>.",
        "es": "Todos están aquí — <a href='/rrc'>abre RRC</a>.",
    },
    "bot_billboard_pointer": {
        "fr": "Juste là — <a href='/billboard'>le babillard</a>.",
        "en": "Right through there &mdash; <a href='/billboard'>the billboard</a>.",
        "es": "Justo ahí — <a href='/billboard'>el tablón</a>.",
    },
    "bot_files_no_card": {
        "fr": "L'étagère est vide pour l'instant — pas de carte dans le lecteur.",
        "en": "Shelf's empty right now &mdash; no card in the slot.",
        "es": "La estantería está vacía — no hay tarjeta en la ranura.",
    },
    "bot_files_empty": {
        "fr": "Rien sur l'étagère pour l'instant. Apporte quelque chose, si tu en as.",
        "en": "Nothing on the shelf yet. Bring something, if you've got it.",
        "es": "Todavía no hay nada aquí. Trae algo, si tienes.",
    },
    "bot_files_header": {
        "fr": "Sur l'étagère\u00a0:<br>",
        "en": "On the shelf:<br>",
        "es": "En la estantería:<br>",
    },
    "bot_get_what": {
        "fr": "Prendre quoi, au juste\u00a0?",
        "en": "Get what, exactly?",
        "es": "¿Llevarte qué, exactamente?",
    },
    "bot_get_here": {
        "fr": "Voilà — <a href='/download?f={href}'>{name}</a>. Clique pour le prendre.",
        "en": "Here you go &mdash; <a href='/download?f={href}'>{name}</a>. Click to take it.",
        "es": "Aquí tienes — <a href='/download?f={href}'>{name}</a>. Haz clic para llevártelo.",
    },
    "bot_balance_free": {
        "fr": "Pas d'ardoise ici — tout est gratuit. Prends ce dont tu as besoin.",
        "en": "No tab to keep here &mdash; everything's free. Take what you need.",
        "es": "Aquí no hay cuenta — todo es gratis. Toma lo que necesites.",
    },
    "bot_balance_have": {
        "fr": "Tu as {n} {unit} avec moi.",
        "en": "You've got {n} {unit} with me.",
        "es": "Tienes {n} {unit} conmigo.",
    },
    "bot_credit_singular": {"fr": "crédit", "en": "credit", "es": "crédito"},
    "bot_credit_plural": {"fr": "crédits", "en": "credits", "es": "créditos"},
    "bot_unknown": {
        "fr": "Je ne connais pas celle-là. Essaie <b>menu</b>.",
        "en": "Don't know that one. Try <b>menu</b>.",
        "es": "No conozco esa. Prueba <b>menu</b>.",
    },
    "home_say_something": {
        "fr": "Dis quelque chose…",
        "en": "Say something...",
        "es": "Di algo…",
    },
    "home_you_label": {"fr": "Toi", "en": "You", "es": "Tú"},
    "home_bring_something": {
        "fr": "Envie de partager quelque chose\u00a0?",
        "en": "Bring something?",
        "es": "¿Quieres compartir algo?",
    },
    "home_upload_button": {"fr": "Envoyer", "en": "Upload", "es": "Subir"},
    "home_awaiting_hash": {
        "fr": "Jeton d'attente (optionnel)",
        "en": "Awaiting-slot hash (optional)",
        "es": "Código de espera (opcional)",
    },
    "home_sending": {"fr": "Envoi…", "en": "Sending...", "es": "Enviando…"},

    # ---- Billboard ----
    "billboard_shelf_empty": {
        "fr": "Rien sur le babillard pour l'instant.",
        "en": "Nothing on the shelf yet.",
        "es": "Todavía no hay nada en el tablón.",
    },
    "billboard_post_placeholder": {
        "fr": "écris un avis…",
        "en": "write a notice...",
        "es": "escribe un aviso…",
    },
    "billboard_post_button": {"fr": "Publier", "en": "Post", "es": "Publicar"},

    # ---- Files ----
    "files_tap_to_download": {
        "fr": "Touche un fichier pour le télécharger.",
        "en": "Tap a file to download it.",
        "es": "Toca un archivo para descargarlo.",
    },
    "files_none_yet": {
        "fr": "Rien sur l'étagère pour l'instant. Utilise la case d'envoi sur la page d'accueil pour laisser quelque chose au prochain visiteur.",
        "en": "Nothing on the shelf yet. Use the upload box on the home page to leave something for the next person.",
        "es": "Todavía no hay nada aquí. Usa el cuadro de subida en la página de inicio para dejar algo para la próxima persona.",
    },
    "files_no_card": {
        "fr": "Pas de carte dans le lecteur pour l'instant, donc rien à partager. Tout le reste fonctionne quand même.",
        "en": "No card in the slot right now, so there's nothing to share. Everything else still works.",
        "es": "No hay tarjeta en la ranura, así que no hay nada para compartir. Todo lo demás sigue funcionando.",
    },
    "files_credit_suffix": {"fr": "crédit", "en": "credit", "es": "crédito"},

    # ---- Tools page (technician-facing; kept English-light on purpose
    # since these are lower priority than the walk-up surfaces, but
    # translated for consistency now that the page renders per-request
    # anyway) ----
    "tools_header": {"fr": "Outils", "en": "Tools", "es": "Herramientas"},
    "tools_intro": {
        "fr": "Pour un technicien. Les téléchargements s'exécutent sur ton propre ordinateur, pas sur le nœud.",
        "en": "For a technician. Downloads run on your own computer, not on the node.",
        "es": "Para un técnico. Las descargas se ejecutan en tu propia computadora, no en el nodo.",
    },
    "tools_none_installed": {
        "fr": "Aucun outil installé sur ce nœud. Relance le Provisioner avec une carte SD en place pour les ajouter ici.",
        "en": "No tools installed on this node. Re-run the Provisioner with an SD card fitted to put them here.",
        "es": "No hay herramientas instaladas en este nodo. Vuelve a ejecutar el Provisioner con una tarjeta SD instalada para agregarlas aquí.",
    },
    "tools_flash_header": {
        "fr": "Flasher une carte",
        "en": "Flash a board",
        "es": "Grabar una placa",
    },
    "tools_flash_intro": {
        "fr": "Branche une carte ESP sur <b>cet ordinateur</b> et écris le micrologiciel depuis le navigateur. Chrome ou Edge sur ordinateur de bureau.",
        "en": "Plug an ESP board into <b>this computer</b> and write firmware to it from the browser. Chrome or Edge on a desktop.",
        "es": "Conecta una placa ESP a <b>esta computadora</b> y graba el firmware desde el navegador. Chrome o Edge en un escritorio.",
    },
    "tools_open_flasher": {
        "fr": "Ouvrir le flasheur &rarr;",
        "en": "Open the flasher &rarr;",
        "es": "Abrir el grabador &rarr;",
    },

    # ---- Billboard page ----
    "billboard_title": {
        "fr": "Le babillard",
        "en": "The Billboard",
        "es": "El tablón",
    },
    "billboard_intro": {
        "fr": "Lis ce qu'il y a. Laisse quelque chose si tu veux.",
        "en": "Read what's here. Leave something if you like.",
        "es": "Lee lo que hay. Deja algo si quieres.",
    },
    "billboard_nothing_yet": {
        "fr": "(rien publié pour l'instant)",
        "en": "(nothing posted yet)",
        "es": "(nada publicado todavía)",
    },

    # ---- stumpid: identity + room access (human-readable text only --
    # never the AUTH-CHALLENGE/OK/FAIL markers themselves) ----
    "whoami_not_verified": {
        "fr": "pas vérifié(e) — envoie /auth pour prouver une identité",
        "en": "not verified -- send /auth to prove an identity",
        "es": "no verificado — envía /auth para probar una identidad",
    },
    "whoami_verified": {
        "fr": "vérifié(e) comme {hash}  surnom\u00a0: {nick}",
        "en": "verified as {hash}  nick={nick}",
        "es": "verificado como {hash}  apodo={nick}",
    },
    "room_needs_verified": {
        "fr": "cette salle exige une identité vérifiée — envoie /auth d'abord",
        "en": "that room requires a verified identity -- send /auth first",
        "es": "esa sala requiere una identidad verificada — envía /auth primero",
    },
    "room_invite_only": {
        "fr": "cette salle est sur invitation pour les personnes non vérifiées — demande à quelqu'un déjà dedans de t'inviter avec /invite",
        "en": "that room is invite-only for unverified visitors -- ask someone already inside to /invite you",
        "es": "esa sala es solo por invitación para visitantes no verificados — pide a alguien que ya esté dentro que te invite con /invite",
    },
    "auth_required_generic": {
        "fr": "identité vérifiée requise — envoie /auth pour te vérifier",
        "en": "auth required for that -- send /auth to verify your identity",
        "es": "se requiere identidad verificada — envía /auth para verificarte",
    },
    "invite_need_verified": {
        "fr": "seule une identité vérifiée peut inviter quelqu'un — /auth d'abord",
        "en": "only a verified identity can invite someone -- /auth first",
        "es": "solo una identidad verificada puede invitar a alguien — /auth primero",
    },
    "invite_usage": {
        "fr": "usage\u00a0: /invite <surnom> [salle]  (par défaut, la salle où tu es)",
        "en": "usage: /invite <nick> [room]  (defaults to the room you're in)",
        "es": "uso: /invite <apodo> [sala]  (por defecto, la sala en la que estás)",
    },
    "invite_wrong_room": {
        "fr": "tu ne peux inviter que dans la salle où tu te trouves",
        "en": "you can only invite people into a room you're currently in",
        "es": "solo puedes invitar a gente a la sala en la que estás",
    },
    "invite_not_gated": {
        "fr": "cette salle n'est pas sur invitation",
        "en": "that room isn't invite-gated",
        "es": "esa sala no requiere invitación",
    },
    "invite_no_such_user": {
        "fr": "personne ici ne s'appelle « {nick} »",
        "en": "no one here called '{nick}'",
        "es": "nadie aquí se llama «{nick}»",
    },
    "invite_done": {
        "fr": "{nick} a été invité(e) dans #{room}",
        "en": "invited {nick} into #{room}",
        "es": "se invitó a {nick} a #{room}",
    },

    # ---- About page. Copy from the PR/marketing team, ported verbatim
    # -- these are their words, not mine, and this integration should
    # not silently edit them. Rendered through this app's own i18n
    # system (server-side, per-visitor, persisted) rather than the
    # standalone client-side toggle the source file shipped with, so it
    # stays consistent with every other page's language behaviour
    # instead of being a second, disconnected mechanism. ----
    "nav_about": {"fr": "À propos", "en": "About", "es": "Acerca de"},
    "about_tagline": {
        "fr": "Tirez-vous une bûche. Un réseau local autonome, sans internet.",
        "en": "Pull up a stump. A local, autonomous network without the internet.",
        "es": "Toma asiento en el tronco. Una red local autónoma sin internet.",
    },
    "about_badge": {
        "fr": "100% OPEN SOURCE &amp; LIBRE \U0001f938",
        "en": "100% OPEN SOURCE &amp; FREE FOR ALL \U0001f938",
        "es": "100% C\u00d3DIGO ABIERTO Y LIBRE \U0001f938",
    },
    "about_badge_text": {
        "fr": "Ce projet est enti\u00e8rement gratuit, libre et ouvert \u00e0 tous. L'architecture mat\u00e9rielle et logicielle est pens\u00e9e pour \u00eatre inspect\u00e9e, partag\u00e9e, reproduite et adapt\u00e9e par quiconque d\u00e9sire se r\u00e9approprier ses communications.",
        "en": "This project is completely free and open source. The hardware and software architecture is built to be inspected, shared, modified, and duplicated by anyone who wants to reclaim their communication sovereignty.",
        "es": "Este proyecto es completamente libre y de c\u00f3digo abierto. Todo el dise\u00f1o de hardware y software est\u00e1 hecho para ser auditado, compartido y replicado por cualquiera que busque recuperar su autonom\u00eda digital.",
    },
    "about_what_h2": {
        "fr": "Qu'est-ce que le Projet Stump\u00a0?",
        "en": "What is Project Stump?",
        "es": "\u00bfQu\u00e9 es el Proyecto Stump?",
    },
    "about_what_p1": {
        "fr": "<strong>Stump (La B\u00fbche)</strong> est un point de rencontre num\u00e9rique ind\u00e9pendant. C'est un serveur autonome et portatif qui cr\u00e9e son propre r\u00e9seau sans jamais d\u00e9pendre d'Internet, des antennes cellulaires ou des monopoles technologiques.",
        "en": "<strong>Stump (La B\u00fbche)</strong> is an independent digital gathering place. It is an off-grid, self-contained node that hosts its own connection without relying on telecommunications towers, centralized servers, or the internet.",
        "es": "<strong>Stump (El Tronco / La B\u00fbche)</strong> es un punto de encuentro digital aut\u00f3nomo. Es un nodo port\u00e1til fuera de la red (off-grid) que genera su propio espacio de conexi\u00f3n sin depender de internet ni de infraestructura corporativa.",
    },
    "about_what_p2": {
        "fr": "On s'y connecte avec n'importe quel appareil Wi-Fi pour partager des messages, \u00e9changer des fichiers ou discuter localement avec ceux qui partagent le m\u00eame espace physique.",
        "en": "Anyone nearby can connect via Wi-Fi to chat, post notes to a community billboard, or share files with people sharing the physical space.",
        "es": "Cualquier persona puede conectarse v\u00eda Wi-Fi para conversar, leer avisos comunitarios y compartir archivos con quienes comparten el mismo entorno f\u00edsico.",
    },
    "about_fireflies_h2": {
        "fr": "Et les Fireflies\u00a0?",
        "en": "What are the Fireflies?",
        "es": "\u00bfY qu\u00e9 son las Fireflies?",
    },
    "about_fireflies_intro": {
        "fr": "Les <strong>Fireflies (Lucioles)</strong> sont de petits bo\u00eetiers radio autonomes et basse consommation (LoRa) diss\u00e9min\u00e9s dans l'environnement.",
        "en": "<strong>Fireflies</strong> are compact, ultra-low-power radio nodes (LoRa) deployed across the terrain.",
        "es": "Las <strong>Fireflies (Luci\u00e9rnagas)</strong> son peque\u00f1os nodos de radio (LoRa) de muy bajo consumo repartidos por el territorio.",
    },
    "about_tile1_title": {"fr": "Maillage", "en": "Mesh", "es": "Red Malla"},
    "about_tile1_desc": {
        "fr": "Relais radio de proche en proche sans fil central.",
        "en": "Packet relays from node to node without infrastructure.",
        "es": "Reenv\u00edo de mensajes de salto en salto sin cables.",
    },
    "about_tile2_title": {"fr": "R\u00e9silience", "en": "Resilient", "es": "Resiliente"},
    "about_tile2_desc": {
        "fr": "Fonctionne sur batterie ou \u00e9nergie solaire en tout temps.",
        "en": "Runs indefinitely on small batteries or solar harvest.",
        "es": "Opera de forma continua con bater\u00edas o placas solares.",
    },
    "about_tile3_title": {"fr": "Veille", "en": "Watch", "es": "Vig\u00eda"},
    "about_tile3_desc": {
        "fr": "Transmet les signaux et alertes \u00e0 longue port\u00e9e.",
        "en": "Carries telemetry, whispers, and long-range signals.",
        "es": "Lleva se\u00f1ales y alertas a largas distancias.",
    },
    "about_fireflies_closing": {
        "fr": "Pendant que la <strong>B\u00fbche</strong> accueille les gens autour d'un feu de camp num\u00e9rique local, les <strong>Lucioles</strong> \u00e9tendent la port\u00e9e des communications \u00e0 travers le quartier ou les espaces bois\u00e9s, reliant les \u00eelots isol\u00e9s entre eux.",
        "en": "While the <strong>Stump</strong> acts as the campfire hub where people gather and browse, the <strong>Fireflies</strong> carry signals across distances and obstacles, stitching isolated clearings into a resilient mesh.",
        "es": "Mientras que el <strong>Stump</strong> funciona como la fogata digital donde la gente se re\u00fane, las <strong>Fireflies</strong> extienden el alcance a trav\u00e9s del terreno, comunicando puntos distantes en una red viva e independiente.",
    },
    "about_visions_h2": {
        "fr": "Visions: For\u00eat Techno-Cyb\u00e9rn\u00e9tique",
        "en": "Visions: Techno-Cybernetic Forest",
        "es": "Visions: Bosque Tecno-Cibern\u00e9tico",
    },
    "about_visions_p": {
        "fr": "Une proposition artistique et technologique\u00a0: d\u00e9ployer des clairi\u00e8res num\u00e9riques et un maillage invisible au c\u0153ur de nos environnements vivants. Allier l'artisanat du hardware, la po\u00e9sie du signal radio et la souverainet\u00e9 collective pour faire dialoguer nature et syst\u00e8mes d\u00e9centralis\u00e9s.",
        "en": "An artistic and technological exploration: weaving invisible radio threads and autonomous digital clearings through our living spaces. Blending raw hardware craftsmanship, radio signal poetry, and community autonomy so nature and decentralization can coexist.",
        "es": "Una propuesta art\u00edstica y comunitaria: sembrar claros digitales y enlaces invisibles de radiofrecuencia a lo largo de nuestros entornos. Uniendo el ensamblaje de hardware artesanal, se\u00f1ales libres y soberan\u00eda comunitaria para hermanar la naturaleza y la tecnolog\u00eda.",
    },
    "about_creator_h2": {
        "fr": "Cr\u00e9ateur &amp; Liens",
        "en": "Creator &amp; Links",
        "es": "Creador y Enlaces",
    },
    "about_creator_p": {
        "fr": "Con\u00e7u et explor\u00e9 par <strong>Rodrigo Gonzalez</strong> \u2014 artisan technologique, passionn\u00e9 de syst\u00e8mes autonomes, de hardware portable et de r\u00e9seaux maill\u00e9s r\u00e9silients.",
        "en": "Designed and built by <strong>Rodrigo Gonzalez</strong> \u2014 builder exploring resilient off-grid meshes, portable hardware, and decentralized community tools.",
        "es": "Dise\u00f1ado y construido por <strong>Rodrigo Gonzalez</strong> \u2014 apasionado del hardware port\u00e1til, redes malladas resilientes y herramientas tecnol\u00f3gicas aut\u00f3nomas.",
    },
    "about_link_github_desc": {
        "fr": "Code source &amp; docs",
        "en": "Source code &amp; repo",
        "es": "C\u00f3digo fuente y repo",
    },
    "about_link_email_title": {"fr": "Courriel", "en": "Email", "es": "Correo"},

    # ---- About page: view tabs (About / Connect / Hardware) ----
    "about_tab_about": {"fr": "À propos", "en": "About", "es": "Acerca de"},
    "about_tab_connect": {"fr": "Se connecter", "en": "Connect", "es": "Conectarse"},
    "about_tab_hardware": {"fr": "Matériel", "en": "Hardware", "es": "Hardware"},

    # ---- About page: "How to Connect" pane. Ported from the
    # marketing team's copy, with one deliberate correction: their
    # step 1 said to look for a network "starting with LaBuche (or
    # Stump)" -- stale against this build's actual default SSID
    # (the full literal string from captive_portal.DEFAULT_SSID, or
    # whatever custom name a technician chose), and the example IP
    # (192.168.0.140) was a placeholder from wherever the page was
    # drafted, not this project's real, consistent AP address. Both
    # fixed to describe what a visitor will actually see rather than
    # what an earlier draft assumed. ----
    "about_connect_tagline": {
        "fr": "Accède à la clairière numérique.",
        "en": "Access the digital clearing.",
        "es": "Accede al claro digital.",
    },
    "about_connect_h2": {
        "fr": "Comment se connecter à la Bûche (Stump)\u00a0?",
        "en": "How to Connect to the Stump",
        "es": "\u00bfC\u00f3mo conectarse al Tronco (Stump)?",
    },
    "about_connect_intro": {
        "fr": "Tu es près d'une Bûche, ou ton appareil détecte un réseau local ouvert\u00a0? Suis ces 3 étapes simples\u00a0:",
        "en": "Standing near a Stump, or does your device detect an open local network nearby? Follow these 3 simple steps:",
        "es": "\u00bfEst\u00e1s cerca de un nodo Stump o tu tel\u00e9fono detecta una red local abierta? Sigue estos 3 pasos sencillos:",
    },
    "about_connect_step1_title": {
        "fr": "1. Ouvre les paramètres Wi-Fi",
        "en": "1. Open your WiFi settings",
        "es": "1. Abre la lista de redes Wi-Fi",
    },
    "about_connect_step1_text": {
        "fr": "Cherche les réseaux à proximité sur ton téléphone ou ordinateur. Le nom du réseau est affiché tel quel dans la liste — pas besoin de deviner.",
        "en": "Scan for nearby networks on your phone or laptop. The network name shows up exactly as broadcast in the list -- nothing to guess.",
        "es": "Mira las conexiones disponibles en tu tel\u00e9fono o computadora. El nombre de la red aparece tal cual en la lista, no hay que adivinar.",
    },
    "about_connect_step2_title": {
        "fr": "2. Rejoins-le sans mot de passe",
        "en": "2. Join without a password",
        "es": "2. Con\u00e9ctate sin contrase\u00f1a",
    },
    "about_connect_step2_text": {
        "fr": "Touche le réseau pour te connecter. Aucun mot de passe, connexion ou compte personnel requis. C'est privé, ouvert et anonyme.",
        "en": "Tap the network to connect. No password, login, or personal account needed. It's private, open, and anonymous.",
        "es": "Toca la red para entrar. No necesitas contrase\u00f1a ni registros. Es una red comunitaria, libre y totalmente an\u00f3nima.",
    },
    "about_connect_step3_title": {
        "fr": "3. Entre dans la clairière",
        "en": "3. Enter the clearing",
        "es": "3. Accede al claro digital",
    },
    "about_connect_step3_text": {
        "fr": "Un écran d'accueil s'ouvre habituellement tout seul. Si rien ne s'affiche, ouvre simplement un navigateur et va à\u00a0:<br><br>{ip}",
        "en": "A welcome screen usually pops up automatically. If nothing opens, just open any web browser and go to:<br><br>{ip}",
        "es": "Suele abrirse una ventana de bienvenida autom\u00e1ticamente. Si no aparece, abre el navegador e ingresa a:<br><br>{ip}",
    },

    # ---- About page: hardware gallery. Images live on the SD card,
    # not baked into the firmware image -- see /about/img route. ----
    "about_hardware_tagline": {
        "fr": "Composants physiques, tests en atelier et nœuds radio.",
        "en": "Physical components, bench testing, and radio nodes.",
        "es": "Componentes f\u00edsicos, pruebas de campo y nodos de radio.",
    },
    "about_hardware_h2": {
        "fr": "Vitrine matérielle",
        "en": "Hardware Showcase",
        "es": "Muestra de Hardware",
    },
    "about_hardware_intro": {
        "fr": "De vrais nœuds construits pour un déploiement en réseau maillé local\u00a0:",
        "en": "Real hardware nodes engineered for local mesh deployment:",
        "es": "Nodos reales montados para la red de malla local:",
    },
    "about_hardware_img1_alt": {
        "fr": "Nœud de terrain autonome — vue 1",
        "en": "Autonomous field node -- view 1",
        "es": "Nodo de campo aut\u00f3nomo \u2014 vista 1",
    },
    "about_hardware_img1_title": {
        "fr": "Nœud Firefly (vue 1)",
        "en": "Firefly Node (View 1)",
        "es": "Nodo Firefly (Vista 1)",
    },
    "about_hardware_img1_sub": {"fr": "LoRa / ESP32", "en": "LoRa / ESP32", "es": "LoRa / ESP32"},
    "about_hardware_img2_alt": {
        "fr": "Nœud de terrain autonome — vue 2",
        "en": "Autonomous field node -- view 2",
        "es": "Nodo de campo aut\u00f3nomo \u2014 vista 2",
    },
    "about_hardware_img2_title": {
        "fr": "Nœud Firefly (vue 2)",
        "en": "Firefly Node (View 2)",
        "es": "Nodo Firefly (Vista 2)",
    },
    "about_hardware_img2_sub": {
        "fr": "Boîtier &amp; antenne",
        "en": "Enclosure &amp; Antenna",
        "es": "Carcasa y Antena",
    },
}
