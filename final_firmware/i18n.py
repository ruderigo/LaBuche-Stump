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
        "fr": "prends ou partage quelque chose",
        "en": "take something, leave something",
        "es": "llévate o comparte algo",
    },
    "tile_tools_sub": {
        "fr": "les applis FireFly, les outils du technicien",
        "en": "FireFly apps, technician tools",
        "es": "las apps FireFly, herramientas del técnico",
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

    "rrc_message_someone": {
        "fr": "Écrire à quelqu'un",
        "en": "Message someone",
        "es": "Escribir a alguien",
    },
    # The other Stumps of the chain, at the bottom of the chat's column.
    # #lora-mesh on a Stump with its own radio (rrc.LORA_LOCKED)
    "lora_room_locked": {"fr": "la salle de la radio : il faut une radio LoRa, ou une invitation de la personne qui tient ce Stump (/lora invite)",
                         "en": "the radio's room: it takes a LoRa radio, or an invitation from whoever runs this Stump (/lora invite)",
                         "es": "la sala de la radio: hace falta una radio LoRa, o una invitación de quien lleva este Stump (/lora invite)"},
    "lora_room_open": {"fr": "#lora-mesh est ouverte à tous ici", "en": "#lora-mesh is open to everyone here", "es": "#lora-mesh está abierta a todos aquí"},
    "lora_room_status": {"fr": "#lora-mesh : la radio, et sur le web les invités : {names}", "en": "#lora-mesh: the radio, and on the web those invited: {names}", "es": "#lora-mesh: la radio, y en la web los invitados: {names}"},
    "lora_room_owner_only": {"fr": "seule la personne sur ce Stump (l'ordinateur) peut inviter", "en": "only the person on this Stump (the computer) can invite", "es": "solo la persona en este Stump (la computadora) puede invitar"},
    "lora_room_invited": {"fr": "{nick} peut entrer dans #lora-mesh", "en": "{nick} may now join #lora-mesh", "es": "{nick} ya puede entrar en #lora-mesh"},
    "lora_room_uninvited": {"fr": "{nick} n'a plus accès à #lora-mesh", "en": "{nick} no longer has #lora-mesh", "es": "{nick} ya no tiene acceso a #lora-mesh"},
    "rrc_stumps": {"fr": "Buches", "en": "Stumps", "es": "Stumps"},
    # The profile at the bottom of the chat's column, and the radio's status dot.
    "rrc_profile_you": {"fr": "Toi", "en": "You", "es": "Tú"},
    "rrc_profile_nick": {"fr": "Pseudo", "en": "Nickname", "es": "Apodo"},
    "rrc_profile_set": {"fr": "OK", "en": "OK", "es": "OK"},
    "rrc_profile_reg": {"fr": "Garder ce pseudo et tes crédits : créer un profil ou se connecter →", "en": "Keep this nickname and your credits: create a profile or log in →", "es": "Guardar este apodo y tus créditos: crear un perfil o entrar →"},
    "rrc_profile_mine": {"fr": "Profil enregistré — gérer →", "en": "Registered profile — manage →", "es": "Perfil registrado — gestionar →"},
    "rrc_profile_stump": {"fr": "Ce Stump", "en": "This Stump", "es": "Este Stump"},
    "rrc_profile_radio": {"fr": "Radio", "en": "Radio", "es": "Radio"},
    "rrc_hw_none": {"fr": "Pas de radio sur ce Stump", "en": "No radio on this Stump", "es": "Sin radio en este Stump"},
    "rrc_hw_online": {"fr": "Radio en ligne", "en": "Radio online", "es": "Radio en línea"},
    "rrc_hw_connecting": {"fr": "Connexion à la radio…", "en": "Connecting to the radio…", "es": "Conectando con la radio…"},
    "rrc_hw_error": {"fr": "Problème de radio", "en": "Radio problem", "es": "Problema con la radio"},
    "rrc_direct_messages": {
        "fr": "Messages directs",
        "en": "Direct Messages",
        "es": "Mensajes directos",
    },
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
    "help_arg_name": {"fr": "<nom>", "en": "<name>", "es": "<nombre>"},
    "help_arg_room": {"fr": "<salle>", "en": "<room>", "es": "<sala>"},
    "help_arg_text": {"fr": "<texte>", "en": "<text>", "es": "<texto>"},
    "help_arg_who": {"fr": "<qui>", "en": "<who>", "es": "<quién>"},
    "help_arg_action": {"fr": "<action>", "en": "<action>", "es": "<acción>"},
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
    "topic_show": {
        "fr": "sujet de #{room}\u00a0: {topic}",
        "en": "#{room} topic: {topic}",
        "es": "tema de #{room}: {topic}",
    },
    "topic_none": {"fr": "(aucun)", "en": "(none set)", "es": "(ninguno)"},
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
    "msg_nothing_to_send": {
        "fr": "rien à envoyer",
        "en": "nothing to send",
        "es": "nada que enviar",
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
    # Default topics of the two built-in rooms, shown in each reader's
    # language until someone sets a real topic.
    "topic_default_main": {
        "fr": "Général. Reste correct.",
        "en": "General. Be decent.",
        "es": "General. Sé respetuoso.",
    },
    "topic_default_lora": {
        "fr": "Le trafic LoRa (radio) arrive ici par défaut.",
        "en": "LoRa (radio) traffic lands here by default.",
        "es": "El tráfico LoRa (radio) llega aquí por defecto.",
    },
    "topic_default_mesh": {
        "fr": "Les Stumps de la chaîne (Wi-Fi) et leurs gens se parlent ici.",
        "en": "The Stumps of the chain (Wi-Fi) and their people talk here.",
        "es": "Los Stumps de la cadena (Wi-Fi) y su gente hablan aquí.",
    },
    # The key to the symbols used for room activity and DMs (rrc.ev_*).
    "plugin_error": {"fr": "{plugin} a eu un problème — ce n'est pas ta faute, réessaie plus tard", "en": "{plugin} had a problem — not your fault, try again later", "es": "{plugin} tuvo un problema — no es tu culpa, inténtalo más tarde"},
    "plugin_rate": {"fr": "une commande à la fois — attends un peu", "en": "one command at a time — wait a little", "es": "un comando a la vez — espera un poco"},
    "plugin_help_mesh": {"fr": "extras : {cmds}", "en": "extras: {cmds}", "es": "extras: {cmds}"},
    "plugin_none": {"fr": "aucun plugin installé", "en": "no plugins installed", "es": "ningún plugin instalado"},
    "plugin_list_line": {"fr": "{name} : {state} — {cmds}", "en": "{name}: {state} — {cmds}", "es": "{name}: {state} — {cmds}"},
    "plugin_admin_usage": {"fr": "usage : /admin <mot de passe> plugin [on|off <nom>]", "en": "usage: /admin <password> plugin [on|off <name>]", "es": "uso: /admin <contraseña> plugin [on|off <nombre>]"},
    "plugin_no_such": {"fr": "aucun plugin nommé {name}", "en": "no plugin called {name}", "es": "ningún plugin llamado {name}"},
    "plugin_on_now": {"fr": "{name} est activé", "en": "{name} is on", "es": "{name} está activado"},
    "plugin_on_failed": {"fr": "{name} est activé, mais n'a pas démarré — voir /admin", "en": "{name} is switched on but didn't start — see /admin", "es": "{name} está activado pero no arrancó — mira /admin"},
    "plugin_off_now": {"fr": "{name} est désactivé", "en": "{name} is off", "es": "{name} está desactivado"},
    "plugin_off_restart": {"fr": "{name} sera désactivé au prochain redémarrage", "en": "{name} will be off from the next restart", "es": "{name} estará desactivado desde el próximo reinicio"},
    "plugin_protected": {"fr": "{name} gère l'accès au nœud : on ne peut pas le désactiver ici", "en": "{name} runs this node's access control: it can't be switched off here", "es": "{name} controla el acceso al nodo: no se puede desactivar aquí"},
    "admin_plugins_header": {"fr": "Plugins", "en": "Plugins", "es": "Plugins"},
    "admin_plugins_culprit": {"fr": "Le nœud s'est arrêté pendant le démarrage de {name} : il a été désactivé. Réactive-le ici une fois corrigé.", "en": "The node stopped while {name} was starting, so it was switched off. Turn it back on here once it's fixed.", "es": "El nodo se detuvo mientras {name} arrancaba, así que se desactivó. Vuelve a activarlo aquí cuando esté arreglado."},
    "admin_plugins_safe": {"fr": "Mode sans échec : le nœud s'est arrêté moins d'une minute après le démarrage. Seuls les plugins de base tournent jusqu'au prochain redémarrage.", "en": "Safe mode: the node stopped less than a minute after starting last time. Only the built-in plugins run until the next restart.", "es": "Modo seguro: el nodo se detuvo menos de un minuto después de arrancar. Solo los plugins de base funcionan hasta el próximo reinicio."},
    "admin_plugins_state_on": {"fr": "actif", "en": "on", "es": "activo"},
    "admin_plugins_state_off": {"fr": "désactivé", "en": "off", "es": "desactivado"},
    "admin_plugins_state_failed": {"fr": "n'a pas démarré", "en": "didn't start", "es": "no arrancó"},
    "admin_plugins_state_off_at_restart": {"fr": "désactivé au prochain redémarrage", "en": "off from the next restart", "es": "desactivado desde el próximo reinicio"},
    "admin_plugins_off": {"fr": "Désactiver", "en": "Switch off", "es": "Desactivar"},
    "admin_plugins_on": {"fr": "Activer", "en": "Switch on", "es": "Activar"},
    "admin_chain_header": {"fr": "Chaîne de Stumps", "en": "Stump chain", "es": "Cadena de Stumps"},
    "admin_chain_root": {"fr": "Premier Stump de la chaîne, sur {ip}{channel}.", "en": "First Stump of the chain, on {ip}{channel}.", "es": "Primer Stump de la cadena, en {ip}{channel}."},
    "admin_chain_member": {"fr": "Relié à « {ssid} » : {ip} via {parent}{heard}.", "en": "Linked to '{ssid}': {ip} via {parent}{heard}.", "es": "Enlazado a «{ssid}»: {ip} vía {parent}{heard}."},
    "admin_chain_heard": {"fr": ", réponse il y a {s} s", "en": ", answered {s} s ago", "es": ", respondió hace {s} s"},
    "admin_chain_trying": {"fr": "En train de rejoindre « {ssid} »…", "en": "Trying to join '{ssid}'…", "es": "Intentando unirse a «{ssid}»…"},
    "admin_chain_below": {"fr": "Reliés en dessous : {list}", "en": "Linked below this one: {list}", "es": "Enlazados debajo: {list}"},
    "admin_chain_none_below": {"fr": "Rien n'est encore relié en dessous.", "en": "Nothing has linked below this one yet.", "es": "Nada se ha enlazado debajo todavía."},
    "admin_chain_nosecret": {"fr": "Pas de secret de chaîne : n'importe qui sur ce Wi-Fi pourrait s'y joindre. Réglez CHAIN_SECRET.", "en": "No chain secret: anyone on this Wi-Fi could join the chain. Set CHAIN_SECRET.", "es": "Sin secreto de cadena: cualquiera en este Wi-Fi podría unirse. Configure CHAIN_SECRET."},
    "help_legend": {
        "fr": "✓ arrivé · ✗ parti · ✎ changé · → vers · ⊖ personne de ce nom · ~ sur le maillage · [DM] message privé · = déjà là · ? commande inconnue · ⊘ refusé · ♪ note vocale · ⧗ ralentis",
        "en": "✓ joined · ✗ left · ✎ changed · → to · ⊖ no one by that name · ~ on the mesh · [DM] private message · = already there · ? unknown command · ⊘ refused · ♪ voice note · ⧗ slow down",
        "es": "✓ llegó · ✗ se fue · ✎ cambió · → a · ⊖ nadie con ese nombre · ~ en la malla · [DM] mensaje privado · = ya estás ahí · ? comando desconocido · ⊘ rechazado · ♪ nota de voz · ⧗ más despacio",
    },
    "help_room_prefix": {
        "fr": "publier dans une salle précise",
        "en": "post to a specific room",
        "es": "publicar en una sala concreta",
    },
    # First DM delivered to a mesh peer who has only announced.
    "dm_hint": {
        "fr": "(message privé via le clavardage de ce nœud — pour répondre : /msg <qui> <message>)",
        "en": "(private message via this node's chat — to reply: /msg <who> <message>)",
        "es": "(mensaje privado vía el chat de este nodo — para responder: /msg <quién> <mensaje>)",
    },
    "clear_mesh": {
        "fr": "rien à effacer sur le maillage — tu ne reçois que les nouvelles lignes",
        "en": "nothing to clear over the mesh — you only receive new lines",
        "es": "nada que borrar en la malla — solo recibes las líneas nuevas",
    },
    "rrc_not_sent": {
        "fr": "non envoyé — problème de connexion",
        "en": "not sent — connection problem",
        "es": "no enviado — problema de conexión",
    },
    # Spoken labels for the chat's symbols (tooltips, screen readers).
    "sym_join": {"fr": "arrivé", "en": "joined", "es": "llegó"},
    "sym_leave": {"fr": "parti", "en": "left", "es": "se fue"},
    "sym_change": {"fr": "changé", "en": "changed", "es": "cambió"},
    "sym_to": {"fr": "vers", "en": "to", "es": "a"},
    "sym_dm": {"fr": "message privé", "en": "private message", "es": "mensaje privado"},
    "sym_already": {"fr": "déjà là", "en": "already there", "es": "ya estás ahí"},
    "sym_unknown_cmd": {"fr": "commande inconnue", "en": "unknown command", "es": "comando desconocido"},
    "sym_refused": {"fr": "refusé", "en": "refused", "es": "rechazado"},
    "sym_unknown": {"fr": "personne de ce nom", "en": "no one by that name", "es": "nadie con ese nombre"},
    # /admin radio settings for the Heltec Bridge (radio.py)
    "admin_radio_header": {"fr": "Radio (pont Heltec)", "en": "Radio (Heltec Bridge)", "es": "Radio (puente Heltec)"},
    "admin_radio_intro": {
        "fr": "Envoyés au Heltec à chaque connexion. Tous les nœuds d'un maillage doivent avoir la même fréquence, largeur de bande, facteur d'étalement et taux de codage — sinon ils ne s'entendent plus. La puissance d'émission peut différer.",
        "en": "Sent to the Heltec every time it connects. Every node on a mesh needs the same frequency, bandwidth, spreading factor and coding rate, or they stop hearing each other. TX power can differ.",
        "es": "Se envían al Heltec cada vez que se conecta. Todos los nodos de una malla necesitan la misma frecuencia, ancho de banda, factor de dispersión y tasa de codificación, o dejan de oírse. La potencia de emisión puede ser distinta.",
    },
    "rrc_radio_same": {"fr": "Fréquence, largeur de bande, SF et CR : les mêmes sur tout le maillage, sinon on ne s'entend plus. La puissance peut différer.",
                       "en": "Frequency, bandwidth, SF and CR: the same across the whole mesh, or nodes stop hearing each other. TX power can differ.",
                       "es": "Frecuencia, ancho de banda, SF y CR: los mismos en toda la malla, o los nodos dejan de oírse. La potencia puede variar."},
    "rrc_radio_tap": {"fr": "Régler", "en": "Settings", "es": "Ajustes"},
    "rrc_radio_owner_only": {"fr": "Seule la personne qui tient ce Stump peut changer la radio (sur l'ordinateur, ou connectée à /admin).",
                             "en": "Only whoever runs this Stump can change the radio (on the computer itself, or logged in to /admin).",
                             "es": "Solo quien lleva este Stump puede cambiar la radio (en la computadora, o con sesión en /admin)."},
    "rrc_radio_presets": {"fr": "Courants : 915 (Amériques), 868–869.525 (Europe), 433 (Asie, Europe)",
                          "en": "Common: 915 (Americas), 868–869.525 (Europe), 433 (Asia, Europe)",
                          "es": "Comunes: 915 (Américas), 868–869.525 (Europa), 433 (Asia, Europa)"},
    "radio_txp": {"fr": "Puissance d'émission (dBm, 0–22)", "en": "TX power (dBm, 0–22)", "es": "Potencia de emisión (dBm, 0–22)"},
    "radio_freq": {"fr": "Fréquence (MHz)", "en": "Frequency (MHz)", "es": "Frecuencia (MHz)"},
    "radio_bw": {"fr": "Largeur de bande", "en": "Bandwidth", "es": "Ancho de banda"},
    "radio_sf": {"fr": "Facteur d'étalement", "en": "Spreading factor", "es": "Factor de dispersión"},
    "radio_cr": {"fr": "Taux de codage", "en": "Coding rate", "es": "Tasa de codificación"},
    "admin_radio_chat_on": {"fr": "Dans le chat, la ligne Radio du profil s'ouvre sur ces réglages (pour la personne sur l'ordinateur, ou connectée ici).",
                            "en": "In the chat, the profile's Radio row opens into these settings (for the person on the computer, or logged in here).",
                            "es": "En el chat, la fila Radio del perfil se abre con estos ajustes (para quien está en la computadora, o conectado aquí)."},
    "admin_radio_chat_off": {"fr": "Dans le chat, le profil montre l'état de la radio seulement : les réglages se changent ici.",
                             "en": "In the chat, the profile shows the radio's state only: the settings are changed here.",
                             "es": "En el chat, el perfil solo muestra el estado de la radio: los ajustes se cambian aquí."},
    "admin_group_air": {"fr": "Sur les ondes", "en": "On the air", "es": "En el aire"},
    "admin_group_content": {"fr": "Contenu", "en": "Content", "es": "Contenido"},
    "admin_group_look": {"fr": "Apparence", "en": "Look", "es": "Aspecto"},
    "admin_setup_header": {"fr": "Réglages du Stump", "en": "Stump settings", "es": "Ajustes del Stump"},
    "admin_setup_intro": {"fr": "Les réponses du Provisioner, changées ici. L'identité du Stump, l'historique, les fichiers, les crédits et la radio restent tels quels : rien n'est réinstallé.", "en": "The Provisioner's answers, changed here. The Stump's identity, its history, files, credits and radio stay as they are: nothing is reinstalled.", "es": "Las respuestas del Provisioner, cambiadas aquí. La identidad del Stump, su historial, archivos, créditos y radio se quedan como están: no se reinstala nada."},
    "admin_setup_names": {"fr": "Nom et bot", "en": "Name and bot", "es": "Nombre y bot"},
    "admin_setup_node": {"fr": "Nom du Stump", "en": "Stump name", "es": "Nombre del Stump"},
    "admin_setup_node_note": {"fr": "C'est aussi le nom de son Wi-Fi : un Stump enchaîné à celui-ci par ce nom devra être reconfiguré.", "en": "It's also its Wi-Fi name: a Stump chained to this one by that name will need reconfiguring.", "es": "También es el nombre de su Wi-Fi: un Stump encadenado a este por ese nombre tendrá que reconfigurarse."},
    "admin_setup_bot": {"fr": "Nom du bot", "en": "Bot's name", "es": "Nombre del bot"},
    "admin_setup_greeting": {"fr": "Réponse automatique (premier contact par le maillage, {n} caractères max ; vide : aucune)", "en": "Auto-reply (first contact over the mesh, {n} characters max; blank: none)", "es": "Respuesta automática (primer contacto por la malla, {n} caracteres máx.; vacío: ninguna)"},
    "admin_setup_files": {"fr": "Partage de fichiers", "en": "File sharing", "es": "Archivos compartidos"},
    "admin_setup_free": {"fr": "Gratuit — rien ne coûte rien", "en": "Free — nothing costs anything", "es": "Gratis — nada cuesta nada"},
    "admin_setup_standard": {"fr": "Économie standard — vidéo 3, musique 2, documents 1", "en": "Standard economy — video 3, music 2, documents 1", "es": "Economía estándar — vídeo 3, música 2, documentos 1"},
    "admin_setup_custom": {"fr": "Économie sur mesure — le poids de chaque type", "en": "Custom economy — each type's weight", "es": "Economía a medida — el peso de cada tipo"},
    "admin_setup_weights": {"fr": "Ce qu'un fichier rapporte en le déposant, et coûte en le téléchargeant.", "en": "What a file earns when uploaded, and costs when downloaded.", "es": "Lo que un archivo gana al subirlo y cuesta al descargarlo."},
    "admin_setup_w_video": {"fr": "Vidéo", "en": "Video", "es": "Vídeo"},
    "admin_setup_w_music": {"fr": "Musique", "en": "Music", "es": "Música"},
    "admin_setup_w_document": {"fr": "Documents", "en": "Documents", "es": "Documentos"},
    "admin_setup_w_other": {"fr": "Autres", "en": "Other", "es": "Otros"},
    "hello_required": {"fr": "Ce Stump demande aux applications de dire qui elles sont d'abord : /auth (prouver votre identité), puis /nick <votre nom>.", "en": "This Stump asks apps to say who they are first: /auth (prove your identity), then /nick <your name>.", "es": "Este Stump pide a las aplicaciones que digan quiénes son primero: /auth (demostrar tu identidad), luego /nick <tu nombre>."},
    "upload_one_at_a_time": {"fr": "Un autre envoi de votre part est en cours : attendez qu'il finisse.", "en": "Another upload of yours is still going: wait for it to finish.", "es": "Otra subida tuya sigue en curso: espera a que termine."},
    "upload_too_many": {"fr": "Vous avez envoyé {n} fichiers dans l'heure : réessayez plus tard (pour laisser de la place aux autres).", "en": "You've sent {n} files this hour: try again later (so there's room for everyone).", "es": "Has enviado {n} archivos esta hora: inténtalo más tarde (para que haya sitio para todos)."},
    "upload_quota": {"fr": "Vous avez partagé votre part du jour ({mb} Mo) : réessayez demain, pour que chacun ait de la place.", "en": "You've shared your share for today ({mb} MB): try again tomorrow, so everyone has room.", "es": "Ya compartiste tu parte de hoy ({mb} MB): vuelve a intentarlo mañana, para que todos tengan sitio."},
    "admin_rescue_header": {"fr": "Démarrage de secours", "en": "Rescue start", "es": "Arranque de rescate"},
    "admin_rescue_text": {"fr": "{n} démarrages de suite ont échoué (le dernier : {why}). Le Stump tourne sans radio, sans chaîne et sans plugins : son Wi-Fi, les pages et /admin. Il réessaie un démarrage normal dans {min} min, ou maintenant :", "en": "{n} starts in a row failed (the last: {why}). The Stump runs without its radio, the chain or plugins: its Wi-Fi, the pages and /admin. It tries a normal start again in {min} min, or now:", "es": "{n} arranques seguidos fallaron (el último: {why}). El Stump funciona sin radio, sin cadena y sin plugins: su Wi-Fi, las páginas y /admin. Vuelve a intentar un arranque normal en {min} min, o ahora:"},
    "admin_rescue_try": {"fr": "Démarrer normalement", "en": "Start normally", "es": "Arrancar normalmente"},
    "admin_watchdog_note": {"fr": "Le Stump ne répondait plus : le chien de garde l'a redémarré.", "en": "The Stump had stopped responding: the watchdog restarted it.", "es": "El Stump dejó de responder: el perro guardián lo reinició."},
    "admin_setup_storage": {"fr": "Stockage", "en": "Storage", "es": "Almacenamiento"},
    "admin_setup_storage_mb": {"fr": "Taille de la « carte » de cet ordinateur (le dossier sd/), en Mo", "en": "Size of this computer's \"card\" (the sd/ folder), in MB", "es": "Tamaño de la «tarjeta» de este ordenador (la carpeta sd/), en MB"},
    "admin_setup_storage_note": {"fr": "Environ {share} pour les fichiers partagés : ils la remplissent jusqu'à {pct} %, puis les plus anciens font de la place. Dedans maintenant : {used}. Le disque a {free} de libre. Plus petit que ce qu'elle contient : les plus anciens partent au prochain dépôt.", "en": "About {share} for shared files: they fill it to {pct}%, then the oldest make room. In it now: {used}. The disk has {free} free. Smaller than what it holds: the oldest go at the next upload.", "es": "Unos {share} para archivos compartidos: la llenan hasta el {pct} %, y luego los más antiguos dejan sitio. Dentro ahora: {used}. El disco tiene {free} libres. Más pequeña que lo que contiene: los más antiguos se van en la próxima subida."},
    "admin_setup_access": {"fr": "Qui peut entrer", "en": "Who may enter", "es": "Quién puede entrar"},
    "admin_setup_open": {"fr": "Ouvert — tout le monde lit et écrit", "en": "Open — everyone reads and writes", "es": "Abierto — todos leen y escriben"},
    "admin_setup_hybrid": {"fr": "Hybride — tout le monde lit tous les salons et écrit dans #main ; écrire ailleurs (autres salons, messages privés) demande une identité vérifiée (FireFly)", "en": "Hybrid — everyone reads every room and writes in #main; writing anywhere else (other rooms, private messages) needs a verified identity (FireFly)", "es": "Híbrido — todos leen todas las salas y escriben en #main; escribir en otro lugar (otras salas, mensajes privados) requiere una identidad verificada (FireFly)"},
    "admin_setup_mandatory": {"fr": "Fermé — lire et écrire demandent une identité vérifiée", "en": "Closed — reading and writing both need a verified identity", "es": "Cerrado — leer y escribir requieren una identidad verificada"},
    "rrc_auth_to_read": {"fr": "Ce Stump est fermé : vérifiez votre identité (/auth, un bouton dans FireFly) pour lire et écrire.", "en": "This Stump is closed: verify your identity (/auth, one button in FireFly) to read and write.", "es": "Este Stump está cerrado: verifica tu identidad (/auth, un botón en FireFly) para leer y escribir."},
    "admin_setup_features": {"fr": "Fonctions", "en": "Features", "es": "Funciones"},
    "admin_setup_f_none": {"fr": "aucune", "en": "none", "es": "ninguna"},
    "admin_setup_save": {"fr": "Enregistrer", "en": "Save", "es": "Guardar"},
    "admin_setup_saved": {"fr": "Enregistré. C'est en place.", "en": "Saved. It's in place.", "es": "Guardado. Ya está en marcha."},
    "admin_setup_unchanged": {"fr": "Rien n'a changé.", "en": "Nothing changed.", "es": "Nada cambió."},
    "admin_setup_later": {"fr": "Enregistré. Le reste s'applique au redémarrage ({what}) : Système → Redémarrer le Stump, tout en bas.", "en": "Saved. The rest applies at the next restart ({what}): System → Restart the Stump, at the very bottom.", "es": "Guardado. El resto se aplica al reiniciar ({what}): Sistema → Reiniciar el Stump, abajo del todo."},
    "admin_setup_librarian": {"fr": "La luciole de {bot}, la bibliothécaire (la bulle en bas à droite)", "en": "{bot}'s firefly, the librarian (the bubble at the bottom right)", "es": "La luciérnaga de {bot}, la bibliotecaria (la burbuja abajo a la derecha)"},
    "admin_restart_intro": {"fr": "Le redémarrer applique les réglages qui l'attendent ; les visiteurs le perdent quelques secondes.", "en": "Restarting applies the settings waiting for it; visitors lose it for a few seconds.", "es": "Reiniciarlo aplica los ajustes pendientes; los visitantes lo pierden unos segundos."},
    "admin_power_header_cam": {"fr": "Redémarrer", "en": "Restart", "es": "Reiniciar"},
    "admin_setup_not_saved": {"fr": "Pas pu écrire config.py : rien n'a été gardé.", "en": "Couldn't write config.py: nothing was kept.", "es": "No se pudo escribir config.py: no se guardó nada."},
    "admin_setup_invalid": {"fr": "Cette valeur ne va pas : {field}.", "en": "That value won't do: {field}.", "es": "Ese valor no sirve: {field}."},
    "admin_setup_restart": {"fr": "Redémarrer le Stump", "en": "Restart the Stump", "es": "Reiniciar el Stump"},
    "admin_setup_restart_confirm": {"fr": "Redémarrer le Stump maintenant ? Les visiteurs le perdent quelques secondes.", "en": "Restart the Stump now? Visitors lose it for a few seconds.", "es": "¿Reiniciar el Stump ahora? Los visitantes lo pierden unos segundos."},
    "admin_setup_restarting": {"fr": "Le Stump redémarre : de retour dans une vingtaine de secondes (la page se recharge seule).", "en": "The Stump is restarting: back in about twenty seconds (the page reloads by itself).", "es": "El Stump se reinicia: vuelve en unos veinte segundos (la página se recarga sola)."},
    "profile_title": {"fr": "Ton profil", "en": "Your profile", "es": "Tu perfil"},
    "profile_link": {"fr": "Ton profil : tes crédits et ton pseudo, sur tous tes appareils", "en": "Your profile: your credits and nickname, on all your devices", "es": "Tu perfil: tus créditos y tu apodo, en todos tus dispositivos"},
    "profile_intro": {"fr": "Un profil garde ton pseudo (personne d'autre ne peut le prendre), tes crédits et tes messages privés — sur ce Stump et ceux de sa chaîne. Tu t'y connectes de n'importe quel navigateur avec ton mot de passe.", "en": "A profile keeps your nickname (no one else can take it), your credits and your private messages — on this Stump and the Stumps of its chain. Log in from any browser with your password.", "es": "Un perfil guarda tu apodo (nadie más puede tomarlo), tus créditos y tus mensajes privados — en este Stump y los de su cadena. Entra desde cualquier navegador con tu contraseña."},
    "profile_login": {"fr": "Se connecter", "en": "Log in", "es": "Entrar"},
    "profile_login_go": {"fr": "Se connecter", "en": "Log in", "es": "Entrar"},
    "profile_create": {"fr": "Créer un profil", "en": "Create a profile", "es": "Crear un perfil"},
    "profile_create_go": {"fr": "Créer mon profil", "en": "Create my profile", "es": "Crear mi perfil"},
    "profile_password": {"fr": "Mot de passe", "en": "Password", "es": "Contraseña"},
    "profile_pw_rule": {"fr": "({n} caractères ou plus)", "en": "({n} characters or more)", "es": "({n} caracteres o más)"},
    "profile_again": {"fr": "Encore une fois", "en": "Once more", "es": "Otra vez"},
    "profile_no_email": {"fr": "Pas de courriel ici : si tu oublies ton mot de passe, la personne qui s'occupe du Stump peut le réinitialiser.", "en": "No email here: if you forget your password, whoever runs the Stump can reset it.", "es": "Aquí no hay correo: si olvidas tu contraseña, quien cuida el Stump puede restablecerla."},
    "profile_firefly": {"fr": "Avec un FireFly, pas besoin de profil : ton identité fait le travail, et elle ouvre aussi les salons réservés.", "en": "With a FireFly you don't need one: your identity does the job, and it also opens the reserved rooms.", "es": "Con un FireFly no hace falta: tu identidad hace el trabajo y también abre las salas reservadas."},
    "profile_logged_in": {"fr": "Connecté·e en tant que {nick}.", "en": "Logged in as {nick}.", "es": "Conectado/a como {nick}."},
    "profile_balance": {"fr": "Crédits : {n}", "en": "Credits: {n}", "es": "Créditos: {n}"},
    "profile_kept": {"fr": "Ton pseudo, tes crédits et tes messages privés te suivent sur ce Stump et ceux de sa chaîne.", "en": "Your nickname, credits and private messages follow you on this Stump and the Stumps of its chain.", "es": "Tu apodo, tus créditos y tus mensajes privados te siguen en este Stump y los de su cadena."},
    "profile_logout": {"fr": "Se déconnecter", "en": "Log out", "es": "Salir"},
    "profile_change": {"fr": "Changer le mot de passe", "en": "Change the password", "es": "Cambiar la contraseña"},
    "profile_current": {"fr": "Mot de passe actuel", "en": "Current password", "es": "Contraseña actual"},
    "profile_new": {"fr": "Nouveau mot de passe", "en": "New password", "es": "Contraseña nueva"},
    "profile_change_go": {"fr": "Changer", "en": "Change", "es": "Cambiar"},
    "profile_mismatch": {"fr": "Les deux mots de passe ne sont pas pareils.", "en": "The two passwords aren't the same.", "es": "Las dos contraseñas no son iguales."},
    "profile_taken": {"fr": "« {nick} » est déjà pris.", "en": "\"{nick}\" is already taken.", "es": "«{nick}» ya está ocupado."},
    "profile_err_taken": {"fr": "« {nick} » a déjà un profil.", "en": "\"{nick}\" already has a profile.", "es": "«{nick}» ya tiene un perfil."},
    "profile_err_nick": {"fr": "Ce pseudo ne va pas : lettres, chiffres et - _ seulement, 16 au plus.", "en": "That nickname won't do: letters, digits and - _ only, 16 at most.", "es": "Ese apodo no sirve: letras, cifras y - _ solamente, 16 como máximo."},
    "profile_err_password": {"fr": "Le mot de passe doit avoir {n} caractères ou plus.", "en": "The password needs {n} characters or more.", "es": "La contraseña necesita {n} caracteres o más."},
    "profile_err_full": {"fr": "Ce Stump a atteint son nombre de profils.", "en": "This Stump has reached its number of profiles.", "es": "Este Stump alcanzó su número de perfiles."},
    "profile_created": {"fr": "Profil créé : bienvenue, {nick}. Tes crédits y sont passés.", "en": "Profile created: welcome, {nick}. Your credits moved into it.", "es": "Perfil creado: bienvenido/a, {nick}. Tus créditos pasaron a él."},
    "profile_welcome": {"fr": "Bon retour, {nick}.", "en": "Welcome back, {nick}.", "es": "Bienvenido/a de nuevo, {nick}."},
    "profile_wrong": {"fr": "Pseudo ou mot de passe incorrect.", "en": "Wrong nickname or password.", "es": "Apodo o contraseña incorrectos."},
    "profile_wait": {"fr": "Trop d'essais : réessaie dans {s} s.", "en": "Too many tries: try again in {s} s.", "es": "Demasiados intentos: vuelve a probar en {s} s."},
    "profile_bye": {"fr": "Déconnecté·e. Ton profil t'attend.", "en": "Logged out. Your profile is waiting for you.", "es": "Has salido. Tu perfil te espera."},
    "profile_changed": {"fr": "Mot de passe changé. Les autres appareils devront se reconnecter.", "en": "Password changed. Your other devices will need to log in again.", "es": "Contraseña cambiada. Tus otros dispositivos tendrán que volver a entrar."},
    "admin_profiles_header": {"fr": "Profils enregistrés", "en": "Registered profiles", "es": "Perfiles registrados"},
    "admin_profiles_intro": {"fr": "Les visiteurs sans FireFly qui ont créé un profil, partagés par la chaîne. Pas de courriel : un mot de passe oublié se réinitialise ici (donne-le à la personne de vive voix).", "en": "Visitors without a FireFly who made a profile, shared by the chain. No email: a forgotten password is reset here (tell the person in person).", "es": "Visitantes sin FireFly que crearon un perfil, compartidos por la cadena. Sin correo: una contraseña olvidada se restablece aquí (díselo a la persona en persona)."},
    "admin_profiles_none": {"fr": "Aucun profil pour l'instant.", "en": "No profiles yet.", "es": "Aún no hay perfiles."},
    "admin_profiles_peek": {"fr": "{n} profil(s)", "en": "{n} profile(s)", "es": "{n} perfil(es)"},
    "admin_profiles_newpw": {"fr": "Nouveau mot de passe ({n} caractères ou plus)", "en": "New password ({n} characters or more)", "es": "Contraseña nueva ({n} caracteres o más)"},
    "admin_profiles_reset": {"fr": "Réinitialiser le mot de passe", "en": "Reset the password", "es": "Restablecer la contraseña"},
    "admin_profiles_delete": {"fr": "Supprimer le profil", "en": "Delete the profile", "es": "Borrar el perfil"},
    "admin_profiles_delete_confirm": {"fr": "Supprimer le profil {nick}, ses crédits avec, sur tous les Stumps de la chaîne ?", "en": "Delete {nick}'s profile, its credits too, on every Stump of the chain?", "es": "¿Borrar el perfil de {nick}, con sus créditos, en todos los Stumps de la cadena?"},
    "admin_profiles_deleted": {"fr": "Profil {nick} supprimé.", "en": "{nick}'s profile deleted.", "es": "Perfil de {nick} borrado."},
    "admin_profiles_missing": {"fr": "Pas de profil {nick}.", "en": "No profile {nick}.", "es": "No hay perfil {nick}."},
    "admin_profiles_was_reset": {"fr": "Mot de passe de {nick} réinitialisé : ses appareils devront se reconnecter.", "en": "{nick}'s password reset: their devices will need to log in again.", "es": "Contraseña de {nick} restablecida: sus dispositivos tendrán que volver a entrar."},
    "profile_created_empty": {"fr": "Profil créé : bienvenue, {nick}.", "en": "Profile created: welcome, {nick}.", "es": "Perfil creado: bienvenido/a, {nick}."},
    "nick_profile": {"fr": "Ton pseudo est celui de ton profil ({nick}) ; pour en changer, déconnecte-toi.", "en": "Your nickname is your profile's ({nick}); to use another, log out.", "es": "Tu apodo es el de tu perfil ({nick}); para usar otro, sal de tu perfil."},
    "admin_group_system": {"fr": "Système", "en": "System", "es": "Sistema"},
    "admin_files_header": {"fr": "Fichiers", "en": "Files", "es": "Archivos"},
    "admin_tile_radio": {"fr": "radio", "en": "radio", "es": "radio"},
    "admin_tile_people": {"fr": "dans le chat", "en": "in the chat", "es": "en el chat"},
    "admin_tile_files": {"fr": "fichiers", "en": "files", "es": "archivos"},
    "admin_peek_files": {"fr": "{n} fichier(s)", "en": "{n} file(s)", "es": "{n} archivo(s)"},
    "admin_peek_noradio": {"fr": "Pas de radio", "en": "No radio", "es": "Sin radio"},
    "admin_peek_root": {"fr": "Premier Stump", "en": "First Stump", "es": "Primer Stump"},
    "admin_peek_member": {"fr": "Relié", "en": "Linked", "es": "Enlazado"},
    "admin_peek_below": {"fr": "{n} en dessous", "en": "{n} below", "es": "{n} debajo"},
    "admin_peek_plugins": {"fr": "{n}/{total} actifs", "en": "{n}/{total} on", "es": "{n}/{total} activos"},
    "admin_power_header": {"fr": "Ce Stump sur l'ordinateur", "en": "This Stump on the computer", "es": "Este Stump en el ordenador"},
    "admin_power_intro": {"fr": "Il tourne en arrière-plan. L'éteindre coupe la radio d'abord ; pour le rallumer : double-clic sur « {start} » dans son dossier (ou à la prochaine ouverture de session).",
                          "en": "It runs in the background. Turning it off switches the radio off first; to start it again, double-click \"{start}\" in its folder (or it starts at your next login).",
                          "es": "Funciona en segundo plano. Apagarlo apaga la radio primero; para encenderlo de nuevo, doble clic en «{start}» en su carpeta (o al próximo inicio de sesión)."},
    "admin_power_off": {"fr": "Éteindre ce Stump", "en": "Turn off this Stump", "es": "Apagar este Stump"},
    "admin_power_confirm": {"fr": "Éteindre ce Stump ? Le chat, les fichiers et la radio s'arrêtent pour tout le monde.",
                            "en": "Turn this Stump off? The chat, files and radio stop for everyone.",
                            "es": "¿Apagar este Stump? El chat, los archivos y la radio se detienen para todos."},
    "admin_power_bye_title": {"fr": "Ce Stump s'éteint", "en": "This Stump is turning off", "es": "Este Stump se está apagando"},
    "admin_power_bye": {"fr": "La radio est coupée, puis le Stump s'arrête. Pour le rallumer : double-clic sur « {start} » dans {folder} (ou à la prochaine ouverture de session).",
                        "en": "The radio is switched off, then the Stump stops. To start it again, double-click \"{start}\" in {folder} (or it starts at your next login).",
                        "es": "La radio se apaga y luego el Stump se detiene. Para encenderlo de nuevo, doble clic en «{start}» en {folder} (o al próximo inicio de sesión)."},
    "admin_radio_settings": {"fr": "Réglages", "en": "Settings", "es": "Ajustes"},
    "admin_bt_intro": {"fr": "Le Bluetooth de la Heltec, sans terminal : pour l'appairer à un téléphone ou à un ordinateur.",
                       "en": "The Heltec's Bluetooth, no terminal: to pair it with a phone or a computer.",
                       "es": "El Bluetooth de la Heltec, sin terminal: para emparejarla con un teléfono o un ordenador."},
    "admin_bt_pair": {"fr": "Appairer un appareil", "en": "Pair a device", "es": "Emparejar un dispositivo"},
    "admin_bt_on": {"fr": "Activer", "en": "Turn on", "es": "Activar"},
    "admin_bt_off": {"fr": "Désactiver", "en": "Turn off", "es": "Desactivar"},
    "admin_bt_pairing": {"fr": "Mode appairage (35 s) : sur le téléphone ou l'ordinateur, choisis « RNode … » ; le code apparaît ici quand il le demande.",
                         "en": "Pairing mode (35 s): on the phone or computer, pick \"RNode …\"; the code shows here when it asks for it.",
                         "es": "Modo de emparejamiento (35 s): en el teléfono o el ordenador, elige «RNode …»; el código aparece aquí cuando lo pida."},
    "admin_bt_computer": {"fr": "Appairer cet ordinateur", "en": "Pair this computer", "es": "Emparejar este ordenador"},
    "admin_bt_computer_go": {"fr": "Appairage de cet ordinateur : quand il demande un code, tape celui qui apparaît ici. Le passage au Bluetooth se fait tout seul.",
                             "en": "Pairing this computer: when it asks for a code, type the one that shows here. It switches to Bluetooth by itself.",
                             "es": "Emparejando este ordenador: cuando pida un código, escribe el que aparece aquí. Pasa a Bluetooth solo."},
    "admin_bt_computer_usb": {"fr": "D'abord en USB : l'appairage passe le code par le câble.", "en": "On USB first: pairing passes the code over the cable.", "es": "Primero por USB: el emparejamiento pasa el código por el cable."},
    "admin_bt_probe_searching": {"fr": "L'ordinateur cherche la Heltec en Bluetooth…", "en": "The computer is looking for the Heltec over Bluetooth…", "es": "El ordenador busca la Heltec por Bluetooth…"},
    "admin_bt_probe_connected": {"fr": "L'ordinateur est relié à la Heltec : tape le code quand il le demande…", "en": "The computer reached the Heltec: type the code when it asks…", "es": "El ordenador llegó a la Heltec: escribe el código cuando lo pida…"},
    "admin_bt_heard": {"fr": "Dernières réponses de la Heltec :", "en": "Last answers from the Heltec:", "es": "Últimas respuestas de la Heltec:"},
    "admin_bt_unpair": {"fr": "Oublier les appareils appairés", "en": "Forget paired devices", "es": "Olvidar los dispositivos emparejados"},
    "admin_bt_unpair_confirm": {"fr": "La Heltec oublie tous les appareils appairés (téléphones, ordinateurs) : il faudra les appairer de nouveau. Continuer ?",
                                "en": "The Heltec forgets every paired device (phones, computers): they'll need pairing again. Go on?",
                                "es": "La Heltec olvida todos los dispositivos emparejados (teléfonos, ordenadores): habrá que emparejarlos de nuevo. ¿Seguir?"},
    "admin_bt_done_unpair": {"fr": "C'est fait : la Heltec n'est plus appairée à rien. Sur les appareils, oublie aussi « RNode … » dans les réglages Bluetooth.",
                             "en": "Done: the Heltec is paired with nothing now. On the devices, also forget \"RNode …\" in their Bluetooth settings.",
                             "es": "Hecho: la Heltec ya no está emparejada con nada. En los dispositivos, olvida también «RNode …» en sus ajustes Bluetooth."},
    "admin_bt_left": {"fr": "({s} s restantes)", "en": "({s} s left)", "es": "(quedan {s} s)"},
    "admin_bt_probe_paired": {"fr": "Appairé ! Passage au Bluetooth…", "en": "Paired! Switching to Bluetooth…", "es": "¡Emparejado! Pasando a Bluetooth…"},
    "admin_bt_ble_note": {"fr": "En Bluetooth, la Heltec est occupée par cet ordinateur : pour appairer un autre appareil, repasse d'abord en USB.",
                          "en": "Over Bluetooth, this computer holds the Heltec's one connection: to pair another device, switch back to USB first.",
                          "es": "Por Bluetooth, este ordenador ocupa la única conexión de la Heltec: para emparejar otro dispositivo, vuelve primero a USB."},
    "admin_transport": {"fr": "Transport :", "en": "Transport:", "es": "Transporte:"},
    "admin_link_title": {"fr": "La Heltec, reliée par", "en": "The Heltec, connected by", "es": "La Heltec, conectada por"},
    "admin_link_usb": {"fr": "Pour passer au Bluetooth : « Appairer cet ordinateur » ci-dessous (le code s'affiche ici), puis Bluetooth.",
                       "en": "To move to Bluetooth: \"Pair this computer\" below (the code shows here), then Bluetooth.",
                       "es": "Para pasar a Bluetooth: «Emparejar este ordenador» abajo (el código aparece aquí), luego Bluetooth."},
    "admin_link_ble": {"fr": "En Bluetooth : le câble USB peut rester branché pour charger ; il n'est pas nécessaire : déjà jumelée, elle se reconnecte seule ; branché, le Stump s'en sert aussi pour redémarrer la Heltec au démarrage ou si le Bluetooth reste muet.",
                       "en": "Over Bluetooth: the USB cable can stay plugged in to charge; it isn't needed: once paired, it reconnects on its own; plugged in, the Stump also uses it to restart the Heltec at start-up or when Bluetooth stays silent.",
                       "es": "Por Bluetooth: el cable USB puede quedar enchufado para cargar; no hace falta: ya emparejada, se reconecta sola; enchufado, el Stump también lo usa para reiniciar la Heltec al arrancar o si el Bluetooth se queda mudo."},
    "admin_link_switching": {"fr": "Changement en cours…", "en": "Switching…", "es": "Cambiando…"},
    "admin_link_r_done": {"fr": "C'est fait : la Heltec se reconnecte (quelques secondes ; en Bluetooth, le temps de la trouver).",
                          "en": "Done: the Heltec is reconnecting (a few seconds; over Bluetooth, the time to find it).",
                          "es": "Hecho: la Heltec se reconecta (unos segundos; por Bluetooth, el tiempo de encontrarla)."},
    "admin_link_r_same": {"fr": "C'est déjà le cas.", "en": "It already is.", "es": "Ya lo está."},
    "admin_link_r_none": {"fr": "Pas de Heltec en USB ou Bluetooth sur cet ordinateur.", "en": "No Heltec on USB or Bluetooth on this computer.", "es": "No hay Heltec por USB o Bluetooth en este ordenador."},
    "admin_link_r_bleak": {"fr": "Installation du Bluetooth pour Python (il faut Internet, une fois) : réessaie dans une minute.",
                           "en": "Installing Bluetooth support for Python (needs the internet, once): try again in a minute.",
                           "es": "Instalando el Bluetooth para Python (necesita Internet, una vez): vuelve a intentarlo en un minuto."},
    "admin_link_r_failed": {"fr": "Le changement n'a pas marché : regarde stump.log.", "en": "The switch didn't work: see stump.log.", "es": "El cambio no funcionó: mira stump.log."},
    "admin_bt_pin": {"fr": "Code d'appairage", "en": "Pairing code", "es": "Código de emparejamiento"},
    "admin_bt_gone": {"fr": "Le mode appairage est terminé. Recommence si besoin.", "en": "Pairing mode is over. Start again if needed.", "es": "El modo de emparejamiento terminó. Empieza de nuevo si hace falta."},
    "admin_bt_done_on": {"fr": "Bluetooth activé sur la Heltec.", "en": "Bluetooth is on on the Heltec.", "es": "Bluetooth activado en la Heltec."},
    "admin_bt_done_off": {"fr": "Bluetooth désactivé sur la Heltec.", "en": "Bluetooth is off on the Heltec.", "es": "Bluetooth desactivado en la Heltec."},
    "admin_bt_offline": {"fr": "La Heltec n'est pas connectée en ce moment : reconnecte-la, puis recommence.", "en": "The Heltec isn't connected right now: reconnect it, then try again.", "es": "La Heltec no está conectada ahora: reconéctala y vuelve a intentarlo."},
    "admin_bt_denied": {"fr": "Réservé à l'admin.", "en": "Admin only.", "es": "Solo para el admin."},
    "admin_bt_help": {"fr": "Ça ne marche pas ?", "en": "Not working?", "es": "¿No funciona?"},
    "admin_bt_tips": {"fr": "Elle n'accepte qu'une connexion Bluetooth à la fois : si un téléphone y est déjà connecté (FireFly, Sideband), coupe son Bluetooth, ou oublie la Heltec dans ses réglages, avant d'en appairer un autre. Sur un Mac, elle n'apparaît pas toujours dans Réglages > Bluetooth : le code est demandé quand le Stump s'y connecte. Branchée en USB seulement pour charger ? Laisse ce Stump en Bluetooth (le Provisioner) : ouvrir son port USB redémarre la Heltec et coupe son Bluetooth ; et un Stump qui l'utilise en USB entre en conflit avec un appareil connecté en Bluetooth.",
                       "en": "It takes one Bluetooth connection at a time: if a phone is already connected to it (FireFly, Sideband), turn that phone's Bluetooth off, or forget the Heltec in its settings, before pairing another. On a Mac it doesn't always show in Settings > Bluetooth: the code is asked for when the Stump connects to it. Plugged in by USB only to charge? Keep this Stump on Bluetooth (the Provisioner): opening its USB port restarts the Heltec and drops its Bluetooth link, and a Stump using it over USB conflicts with a device connected over Bluetooth.",
                       "es": "Acepta una sola conexión Bluetooth a la vez: si un teléfono ya está conectado (FireFly, Sideband), apaga su Bluetooth, u olvida la Heltec en sus ajustes, antes de emparejar otro. En un Mac no siempre aparece en Ajustes > Bluetooth: el código se pide cuando el Stump se conecta. ¿Enchufada por USB solo para cargar? Deja este Stump en Bluetooth (el Provisioner): abrir su puerto USB reinicia la Heltec y corta su Bluetooth, y un Stump que la usa por USB choca con un dispositivo conectado por Bluetooth."},
    "admin_radio_retry": {"fr": "Reconnecter la radio", "en": "Reconnect the radio", "es": "Reconectar la radio"},
    "admin_radio_retry_intro": {"fr": "Elle se reconnecte d'elle-même quand on la rebranche ou qu'elle revient ; ce bouton refait la poignée de main tout de suite.",
                                "en": "It reconnects by itself when it's plugged back in or comes back; this button redoes the handshake now.",
                                "es": "Se reconecta sola cuando se vuelve a enchufar o vuelve; este botón repite el saludo ahora."},
    "admin_radio_retrying": {"fr": "Reconnexion de la radio : la poignée de main recommence (quelques secondes ; l'état se met à jour sous « Radio — en direct »).",
                             "en": "Reconnecting the radio: the handshake starts again (a few seconds; the state updates under \"Radio — live\").",
                             "es": "Reconectando la radio: el saludo empieza de nuevo (unos segundos; el estado se actualiza en «Radio — en vivo»)."},
    "admin_radio_chat_turn_off": {"fr": "Retirer les réglages du chat", "en": "Remove the settings from the chat", "es": "Quitar los ajustes del chat"},
    "admin_radio_chat_turn_on": {"fr": "Offrir les réglages dans le chat", "en": "Offer the settings in the chat", "es": "Ofrecer los ajustes en el chat"},
    "admin_radio_save": {"fr": "Appliquer", "en": "Apply", "es": "Aplicar"},
    "admin_radio_sent": {
        "fr": "Réglages radio envoyés au Heltec et enregistrés.",
        "en": "Radio settings sent to the Heltec and saved.",
        "es": "Ajustes de radio enviados al Heltec y guardados.",
    },
    "admin_radio_pending": {
        "fr": "Réglages enregistrés — le Heltec n'est pas connecté en ce moment ; ils lui seront envoyés à sa prochaine connexion.",
        "en": "Settings saved — the Heltec isn't connected right now; they'll be sent when it next connects.",
        "es": "Ajustes guardados — el Heltec no está conectado ahora; se le enviarán cuando vuelva a conectarse.",
    },
    "admin_radio_not_saved": {
        "fr": "Réglages appliqués, mais pas enregistrés : ils ne survivront pas à un redémarrage.",
        "en": "Settings applied, but not saved: they won't survive a reboot.",
        "es": "Ajustes aplicados, pero no guardados: no sobrevivirán a un reinicio.",
    },
    "admin_radio_invalid": {
        "fr": "Valeur refusée : {field}. Rien n'a été changé.",
        "en": "Value refused: {field}. Nothing was changed.",
        "es": "Valor rechazado: {field}. No se cambió nada.",
    },
    # /admin: the radio, live (/hw.json)
    "admin_hw_title": {"fr": "Radio — en direct", "en": "Radio — live", "es": "Radio — en vivo"},
    "admin_hw_none": {"fr": "Aucune radio.", "en": "No radio.", "es": "Sin radio."},
    "admin_hw_where": {"fr": "Branchée sur", "en": "Connected on", "es": "Conectada en"},
    "admin_hw_error": {"fr": "Problème", "en": "Problem", "es": "Problema"},
    "admin_hw_set": {"fr": "Réglages envoyés", "en": "Settings sent", "es": "Ajustes enviados"},
    "admin_hw_echo": {"fr": "Confirmés par la radio", "en": "Confirmed by the radio", "es": "Confirmados por la radio"},
    "admin_hw_on": {"fr": "émetteur allumé", "en": "transmitter on", "es": "emisor encendido"},
    "admin_hw_noise": {"fr": "Bruit de fond", "en": "Noise floor", "es": "Ruido de fondo"},
    "admin_hw_load": {"fr": "Occupation du canal", "en": "Channel load", "es": "Ocupación del canal"},
    "admin_hw_airtime": {"fr": "temps d'antenne", "en": "airtime", "es": "tiempo de aire"},
    "admin_hw_battery": {"fr": "Batterie", "en": "Battery", "es": "Batería"},
    "admin_hw_temp": {"fr": "Température", "en": "Temperature", "es": "Temperatura"},
    "admin_hw_counts": {"fr": "Paquets reçus / envoyés", "en": "Packets received / sent", "es": "Paquetes recibidos / enviados"},
    "admin_hw_last": {"fr": "Depuis le dernier paquet reçu", "en": "Since the last packet received", "es": "Desde el último paquete recibido"},
    "admin_hw_since": {"fr": "Connectée depuis", "en": "Connected for", "es": "Conectada desde hace"},
    "admin_radio_none": {"fr": "Aucun pont Heltec sur ce nœud.", "en": "No Heltec Bridge on this node.", "es": "No hay puente Heltec en este nodo."},
    # /admin: LXMF propagation node (propagation.py)
    "admin_pn_header": {"fr": "Nœud de propagation (messages hors ligne)", "en": "Propagation node (offline messages)", "es": "Nodo de propagación (mensajes sin conexión)"},
    "admin_pn_intro": {
        "fr": "Garde les messages LXMF des gens hors ligne et les leur remet à leur retour. Les applis (FireFly, Sideband) le voient comme nœud de propagation. Chaque message reçu est vérifié en arrière-plan (environ 30 s).",
        "en": "Holds LXMF messages for people who are offline and hands them over when they're back. Apps (FireFly, Sideband) see it as a propagation node. Each received message is checked in the background (about 30 s).",
        "es": "Guarda los mensajes LXMF de quienes están sin conexión y se los entrega a su regreso. Las apps (FireFly, Sideband) lo ven como nodo de propagación. Cada mensaje recibido se verifica en segundo plano (unos 30 s).",
    },
    "admin_pn_on_state": {"fr": "Activé. Adresse :", "en": "On. Address:", "es": "Activado. Dirección:"},
    "admin_pn_off_state": {"fr": "Désactivé.", "en": "Off.", "es": "Desactivado."},
    "admin_pn_enable": {"fr": "Activer", "en": "Turn on", "es": "Activar"},
    "admin_pn_disable": {"fr": "Désactiver", "en": "Turn off", "es": "Desactivar"},
    "admin_pn_counts": {
        "fr": "{stored} messages gardés, {checking} en vérification · {valid} acceptés, {invalid} refusés, {served} remis",
        "en": "{stored} messages held, {checking} being checked · {valid} accepted, {invalid} refused, {served} handed over",
        "es": "{stored} mensajes guardados, {checking} en verificación · {valid} aceptados, {invalid} rechazados, {served} entregados",
    },
    "admin_pn_mailbox": {
        "fr": "Boîte de test (jamais en ligne) — envoie-lui un message par ce nœud :",
        "en": "Test mailbox (never online) — send it a message through this node:",
        "es": "Buzón de prueba (nunca conectado) — envíale un mensaje a través de este nodo:",
    },
    "admin_pn_mailbox_none": {"fr": "Rien reçu pour l'instant.", "en": "Nothing received yet.", "es": "Nada recibido todavía."},
    "admin_pn_leave_to": {"fr": "Laisser un message pour l'adresse LXMF", "en": "Leave a message for LXMF address", "es": "Dejar un mensaje para la dirección LXMF"},
    "admin_pn_leave_text": {"fr": "Message", "en": "Message", "es": "Mensaje"},
    "admin_pn_leave_button": {"fr": "Laisser le message", "en": "Leave message", "es": "Dejar el mensaje"},
    "admin_pn_left": {
        "fr": "Message laissé. Ferme l'appli, rouvre-la et synchronise avec ce nœud : il doit arriver.",
        "en": "Message left. Close the app, reopen it and sync with this node: it should arrive.",
        "es": "Mensaje dejado. Cierra la app, ábrela de nuevo y sincroniza con este nodo: debería llegar.",
    },
    "admin_pn_bad_address": {"fr": "Adresse invalide : 32 caractères hexadécimaux.", "en": "Invalid address: 32 hexadecimal characters.", "es": "Dirección no válida: 32 caracteres hexadecimales."},
    "admin_pn_unknown": {
        "fr": "Adresse inconnue : ce nœud n'a pas encore entendu son annonce. Annonce depuis l'appli, puis réessaie.",
        "en": "Unknown address: this node hasn't heard its announce yet. Announce from the app, then try again.",
        "es": "Dirección desconocida: este nodo aún no ha oído su anuncio. Anuncia desde la app y vuelve a intentarlo.",
    },
    "admin_pn_enabled": {"fr": "Nœud de propagation activé et annoncé.", "en": "Propagation node on and announced.", "es": "Nodo de propagación activado y anunciado."},
    "admin_pn_disabled": {
        "fr": "Désactivé : plus de nouveaux messages acceptés. Ceux déjà gardés peuvent encore être récupérés.",
        "en": "Off: no new messages accepted. Ones already held can still be collected.",
        "es": "Desactivado: no se aceptan mensajes nuevos. Los ya guardados aún se pueden recoger.",
    },
    "admin_pn_no_sd": {"fr": "Impossible : il faut une carte SD.", "en": "Can't: it needs an SD card.", "es": "No es posible: necesita una tarjeta SD."},
    # Voice notes (rrc.send_voice)
    "voice_bad": {"fr": "note vocale illisible (Opus ou Codec 2 attendu)", "en": "unreadable voice note (Opus or Codec 2 expected)", "es": "nota de voz ilegible (se esperaba Opus o Codec 2)"},
    "voice_length": {"fr": "note vocale entre 0,6 et 15 secondes", "en": "voice notes must be 0.6 to 15 seconds", "es": "las notas de voz deben durar de 0,6 a 15 segundos"},
    "voice_rate": {"fr": "trop de notes vocales — attends un peu", "en": "too many voice notes — wait a little", "es": "demasiadas notas de voz — espera un poco"},
    "ask_title": {"fr": "Demander à {bot}", "en": "Ask {bot}", "es": "Pregúntale a {bot}"},
    "ask_hint": {"fr": "{bot} sait ce qu'il y a sur ce nœud : fichiers, avis, applis.", "en": "{bot} knows what's on this node: files, notices, apps.", "es": "{bot} sabe lo que hay en este nodo: archivos, avisos, aplicaciones."},
    "ask_placeholder": {"fr": "Demande à {bot} : « As-tu un film ? », « Quelque chose sur le solaire ? »…", "en": "Ask {bot}: “Do you have a movie?”, “Any books or tools?”…", "es": "Pregúntale a {bot}: «¿Tienes una película?», «¿Algo sobre música?»…"},
    "ask_role": {"fr": "le bibliothécaire", "en": "the librarian", "es": "el bibliotecario"},
    "ask_short": {"fr": "Demande-moi un film, un livre…", "en": "Ask me for a film, a book…", "es": "Pídeme una película, un libro…"},
    "ask_chips": {"fr": "As-tu un film ?|Quoi de neuf au babillard ?|Quelles applis ?",
                  "en": "Any films?|What's new on the billboard?|Which apps?",
                  "es": "¿Tienes una película?|¿Qué hay en el tablón?|¿Qué aplicaciones?"},
    "ask_button": {"fr": "Demander", "en": "Ask", "es": "Preguntar"},
    "sort_label": {"fr": "Trier", "en": "Sort", "es": "Ordenar"},
    "sort_newest": {"fr": "Plus récents", "en": "Newest first", "es": "Más recientes"},
    "sort_oldest": {"fr": "Plus anciens", "en": "Oldest first", "es": "Más antiguos"},
    "sort_name": {"fr": "Nom (A → Z)", "en": "Name (A → Z)", "es": "Nombre (A → Z)"},
    "sort_title": {"fr": "Titre (A → Z)", "en": "Title (A → Z)", "es": "Título (A → Z)"},
    "sort_small": {"fr": "Taille (petits d'abord)", "en": "Size (smallest first)", "es": "Tamaño (pequeños primero)"},
    "sort_large": {"fr": "Taille (gros d'abord)", "en": "Size (largest first)", "es": "Tamaño (grandes primero)"},
    "filter_placeholder": {"fr": "Filtrer… (Entrée : demander à {bot})", "en": "Filter… (Enter: ask {bot})", "es": "Filtrar… (Intro: preguntar a {bot})"},
    "search_placeholder": {"fr": "Chercher dans les avis…", "en": "Search the notices…", "es": "Buscar en los avisos…"},
    "filter_none": {"fr": "Rien ne correspond.", "en": "Nothing matches.", "es": "Nada coincide."},
    "cat_all": {"fr": "Tout", "en": "All", "es": "Todo"},
    "cat_video": {"fr": "Vidéo", "en": "Video", "es": "Vídeo"},
    "cat_music": {"fr": "Audio", "en": "Audio", "es": "Audio"},
    "cat_document": {"fr": "Livres/Docs", "en": "Books/Docs", "es": "Libros/Docs"},
    "cat_image": {"fr": "Images", "en": "Images", "es": "Imágenes"},
    "cat_other": {"fr": "Autres", "en": "Other", "es": "Otros"},
    "kind_video": {"fr": "vidéos", "en": "videos", "es": "vídeos"},
    "kind_music": {"fr": "pistes audio", "en": "audio tracks", "es": "pistas de audio"},
    "kind_document": {"fr": "livres et documents", "en": "books and documents", "es": "libros y documentos"},
    "kind_image": {"fr": "images", "en": "pictures", "es": "imágenes"},
    "kind_other": {"fr": "autres fichiers", "en": "other files", "es": "otros archivos"},
    "lib_hello": {"fr": "Je sais ce qu'il y a sur ce nœud : demande-moi un film, un livre, de la musique, des photos, les applis ou ce qu'il y a au babillard. (« menu » pour le reste.)", "en": "I know what's on this node: ask me for a film, a book, music, pictures, the apps, or what's on the billboard. (“menu” for the rest.)", "es": "Sé lo que hay en este nodo: pídeme una película, un libro, música, fotos, las aplicaciones o lo que hay en el tablón. («menu» para lo demás.)"},
    "lib_files_found": {"fr": "Oui ! Sur l'étagère :", "en": "Yes! On the file shelf:", "es": "¡Sí! En el estante:"},
    "lib_files_none_topic": {"fr": "Rien sur l'étagère à propos de « {topic} ».", "en": "Nothing on the shelf about “{topic}”.", "es": "Nada en el estante sobre «{topic}»."},
    "lib_files_others": {"fr": "Mais j'ai des {kind} :", "en": "But I have {kind}:", "es": "Pero tengo {kind}:"},
    "lib_files_none_kind": {"fr": "Pas de {kind} sur l'étagère pour l'instant.", "en": "No {kind} on the shelf right now.", "es": "No hay {kind} en el estante por ahora."},
    "lib_shelf_has": {"fr": "Il y a : {list}.", "en": "There are: {list}.", "es": "Hay: {list}."},
    "lib_shelf_empty": {"fr": "L'étagère est vide pour l'instant.", "en": "The shelf is empty for now.", "es": "El estante está vacío por ahora."},
    "lib_board_found": {"fr": "Au babillard :", "en": "On the billboard:", "es": "En el tablón:"},
    "lib_board_none": {"fr": "Rien de neuf au babillard.", "en": "Nothing new on the billboard.", "es": "Nada nuevo en el tablón."},
    "lib_board_open": {"fr": "Ouvrir le babillard", "en": "Open the billboard", "es": "Abrir el tablón"},
    "lib_files_open": {"fr": "Voir l'étagère", "en": "See the shelf", "es": "Ver el estante"},
    "lib_apps": {"fr": "Les applis sur ce nœud :", "en": "The apps on this node:", "es": "Las aplicaciones en este nodo:"},
    "lib_apps_none": {"fr": "Aucune appli sur ce nœud pour l'instant ; la page Outils a le reste.", "en": "No apps on this node yet; the Tools page has the rest.", "es": "Aún no hay aplicaciones en este nodo; la página Herramientas tiene lo demás."},
    "lib_nothing": {"fr": "J'ai regardé l'étagère et le babillard : rien sur « {topic} » pour l'instant. Si tu en as, tu peux le partager sur la page Fichiers !", "en": "I checked the file shelf and the billboard, and I don't have anything about “{topic}” yet. If you have one, you can upload it on the Files page!", "es": "Miré el estante y el tablón: nada sobre «{topic}» por ahora. Si tienes algo, ¡puedes subirlo en la página Archivos!"},
    "lib_files_off": {"fr": "Le partage de fichiers n'est pas offert sur ce nœud.", "en": "File sharing isn't offered on this node.", "es": "El intercambio de archivos no está disponible en este nodo."},
    "lib_no_card": {"fr": "Pas de carte SD : l'étagère est fermée.", "en": "No SD card: the shelf is closed.", "es": "No hay tarjeta SD: el estante está cerrado."},
    "rec_hint": {"fr": "Touche ♪, parle, touche ■ : la note part dans le clavardage.", "en": "Tap ♪, speak, tap ■: the note goes to the chat.", "es": "Toca ♪, habla, toca ■: la nota va al chat."},
    "rec_sending": {"fr": "Envoi…", "en": "Sending…", "es": "Enviando…"},
    "rec_refused": {"fr": "Le micro n'est pas permis : autorise-le pour ce site, ou ferme cette fenêtre et utilise l'enregistreur du téléphone.", "en": "The microphone isn't allowed: allow it for this site, or close this window and use the phone's recorder.", "es": "El micrófono no está permitido: permítelo para este sitio, o cierra esta ventana y usa la grabadora del teléfono."},
    "rec_no_opener": {"fr": "Ouvre l'enregistreur depuis le bouton ♪ du clavardage.", "en": "Open the recorder from the chat's ♪ button.", "es": "Abre la grabadora desde el botón ♪ del chat."},
    "chat_rate": {"fr": "tu écris trop vite — attends un peu", "en": "you're typing too fast — wait a little", "es": "escribes demasiado rápido — espera un poco"},
    "post_rate": {"fr": "trop d'annonces d'un coup — réessaie dans une minute", "en": "too many posts at once — try again in a minute", "es": "demasiados anuncios seguidos — vuelve a intentarlo en un minuto"},
    "admin_locked": {"fr": "trop de mots de passe erronés — réessaie dans {s} s", "en": "too many wrong passwords — try again in {s} s", "es": "demasiadas contraseñas incorrectas — vuelve a intentarlo en {s} s"},
    "voice_needs_target": {
        "fr": "pour envoyer une note vocale par ce nœud : /msg <qui> avec la note jointe",
        "en": "to send a voice note through this node: /msg <who> with the note attached",
        "es": "para enviar una nota de voz por este nodo: /msg <quién> con la nota adjunta",
    },
    "voice_record": {"fr": "Note vocale", "en": "Voice note", "es": "Nota de voz"},
    "voice_unreadable": {
        "fr": "impossible de lire cet enregistrement — essaie un autre format ou une autre appli",
        "en": "couldn't read this recording — try another format or recorder app",
        "es": "no se pudo leer esta grabación — prueba otro formato u otra app",
    },
    "admin_tls_on": {"fr": "Activé pour {host} — certificat valide jusqu'au {until} ({days} jours).", "en": "On for {host} — certificate valid until {until} ({days} days).", "es": "Activado para {host} — certificado válido hasta el {until} ({days} días)."},
    "admin_tls_off": {"fr": "Désactivé : {reason}.", "en": "Off: {reason}.", "es": "Desactivado: {reason}."},
    "admin_tls_why_no_cert": {"fr": "aucun certificat installé", "en": "no certificate installed", "es": "no hay ningún certificado instalado"},
    "admin_tls_why_no_tls": {
        "fr": "ce MicroPython ne sait pas servir le TLS ({detail})",
        "en": "this MicroPython can't serve TLS ({detail})",
        "es": "este MicroPython no puede servir TLS ({detail})",
    },
    "admin_tls_why_rejected": {"fr": "certificat ou clé refusé : {detail}", "en": "certificate or key rejected: {detail}", "es": "certificado o clave rechazados: {detail}"},
    "admin_tls_why_port": {"fr": "impossible d'ouvrir le port 443 : {detail}", "en": "couldn't open port 443: {detail}", "es": "no se pudo abrir el puerto 443: {detail}"},
    "admin_tls_why_failed": {"fr": "{detail}", "en": "{detail}", "es": "{detail}"},
    "home_st_no_sd": {"fr": "pas de carte SD", "en": "no SD card", "es": "no hay tarjeta SD"},
    "home_st_no_folder": {"fr": "pas de dossier home sur la carte SD", "en": "no home folder on the SD card", "es": "no hay carpeta home en la tarjeta SD"},
    "home_st_hidden": {
        "fr": "masquée de la page d'accueil (désactivée ici) — le logo Stump s'affiche",
        "en": "hidden from the home page (turned off here) — the Stump logo shows",
        "es": "oculta de la página de inicio (desactivada aquí): se muestra el logo de Stump",
    },
    "home_st_page": {"fr": "une page complète, dans un cadre de {height} px", "en": "a full page, in a {height} px frame", "es": "una página completa, en un marco de {height} px"},
    "home_st_section": {"fr": "une section", "en": "a section", "es": "una sección"},
    "home_st_in_use": {"fr": "home/{name} : {how}, placée {place}", "en": "home/{name}: {how}, placed {place}", "es": "home/{name}: {how}, colocada {place}"},
    "home_st_place_top": {"fr": "au-dessus des boutons", "en": "above the buttons", "es": "encima de los botones"},
    "home_st_place_bottom": {"fr": "sous les boutons", "en": "under the buttons", "es": "debajo de los botones"},
    "home_st_place_left": {"fr": "à gauche", "en": "on the left", "es": "a la izquierda"},
    "home_st_place_right": {"fr": "à droite", "en": "on the right", "es": "a la derecha"},
    "home_st_no_html": {"fr": "home/ n'a aucun fichier .html", "en": "home/ has no .html file", "es": "home/ no tiene ningún archivo .html"},
    "home_st_too_big": {
        "fr": "home/{name} dépasse {kb} Ko : ignoré (un morceau de HTML doit rester sous cette taille)",
        "en": "home/{name} is over {kb} KB: ignored (a piece of HTML must stay under that)",
        "es": "home/{name} supera {kb} KB: se ignora (un fragmento de HTML debe quedar por debajo)",
    },
    "home_st_several": {
        "fr": "home/ contient plusieurs fichiers .html ({names}) : nomme home.html celui à utiliser",
        "en": "home/ has several .html files ({names}): name the one to use home.html",
        "es": "home/ tiene varios archivos .html ({names}): llama home.html al que se debe usar",
    },
    "admin_tls_renew": {"fr": "renouvelle-le (docs/HTTPS_SETUP.md)", "en": "renew it (docs/HTTPS_SETUP.md)", "es": "renuévalo (docs/HTTPS_SETUP.md)"},
    "voice_recording": {"fr": "Enregistrement… touche pour envoyer", "en": "Recording… tap to send", "es": "Grabando… toca para enviar"},
    "msg_expired": {
        "fr": "ton message est arrivé avec {minutes} min de retard (laissé à un nœud de propagation) — rien n'a été fait ; renvoie-le directement",
        "en": "your message arrived {minutes} min late (left at a propagation node) — nothing was done; send it again directly",
        "es": "tu mensaje llegó con {minutes} min de retraso (dejado en un nodo de propagación) — no se hizo nada; reenvíalo directamente",
    },
    "voice_unplayable": {"fr": "format non pris en charge", "en": "format not supported", "es": "formato no compatible"},
    "sym_voice": {"fr": "note vocale", "en": "voice note", "es": "nota de voz"},
    "sym_rate": {"fr": "ralentis", "en": "slow down", "es": "más despacio"},
    "about_tab_about": {
        "fr": "Projet (v1)",
        "en": "Project (v1)",
        "es": "Proyecto (v1)",
    },
    "about_tab_connect": {
        "fr": "Connexion",
        "en": "How to Connect",
        "es": "Conexión",
    },
    "about_tab_apps": {
        "fr": "Terminaux & Apps",
        "en": "Apps & Handhelds",
        "es": "Apps y Consolas",
    },
    "about_visions_h2": {
        "fr": "Visions: Forêt Techno-Cybernétique",
        "en": "Visions: Techno-Cybernetic Forest",
        "es": "Visions: Bosque Tecno-Cibernético",
    },
    "about_link_stump_desc": {
        "fr": "Serveur & nœud racine",
        "en": "Server & root node",
        "es": "Servidor y nodo raíz",
    },
    "about_link_android_desc": {
        "fr": "Application mobile Android",
        "en": "Mobile Android client",
        "es": "Cliente móvil Android",
    },
    "about_link_rk_desc": {
        "fr": "Firmware dArkOS R36S/MAX",
        "en": "dArkOS handheld terminal",
        "es": "Terminal dArkOS para R36S/MAX",
    },
    "about_connect_tagline": {
        "fr": "Accéder à la clairière numérique.",
        "en": "Access the digital clearing.",
        "es": "Accede al claro digital.",
    },
    "about_connect_h2": {
        "fr": "Comment se brancher à La Bûche ?",
        "en": "How to Connect to the Stump",
        "es": "¿Cómo conectarse al Tronco (Stump)?",
    },
    "about_connect_intro": {
        "fr": "Vous êtes à côté d'un point Stump ou votre téléphone détecte un réseau ouvert ? Suivez simplement ces 3 étapes :",
        "en": "Standing near a Stump, or does your device detect an open local network nearby? Follow these 3 simple steps:",
        "es": "¿Estás cerca de un nodo Stump o tu teléfono detecta una red local abierta? Sigue estos 3 pasos sencillos:",
    },
    "about_connect_step1_title": {
        "fr": "1. Ouvrez vos réglages Wi-Fi",
        "en": "1. Open your Wi-Fi settings",
        "es": "1. Abre la lista de redes Wi-Fi",
    },
    "about_connect_step1_text": {
        "fr": "Regardez la liste des réseaux disponibles autour de vous. Cherchez le réseau {ssid}.",
        "en": "Scan for nearby networks on your phone or laptop. Look for the network named {ssid}.",
        "es": "Mira las conexiones disponibles en tu teléfono o computadora. Busca la red {ssid}.",
    },
    "about_connect_step2_title": {
        "fr": "2. Rejoignez sans mot de passe",
        "en": "2. Join without a password",
        "es": "2. Conéctate sin contraseña",
    },
    "about_connect_step2_text": {
        "fr": "Appuyez dessus pour vous connecter. Aucun mot de passe n'est requis. C'est un accès local, ouvert et anonyme.",
        "en": "Tap the network to connect. No password, login, or personal account is needed. It is private, open, and anonymous.",
        "es": "Toca la red para entrar. No necesitas contraseña ni registros. Es una red comunitaria, libre y totalmente anónima.",
    },
    "about_connect_step3_title": {
        "fr": "3. Entrez dans la clairière",
        "en": "3. Enter the clearing",
        "es": "3. Accede al claro digital",
    },
    "about_connect_step3_text": {
        "fr": "Une fenêtre de bienvenue s'ouvrira toute seule sur votre écran. Si rien n'apparaît, ouvrez simplement votre navigateur web (Safari, Chrome, etc.) et visitez :<br><br>{ip}",
        "en": "A welcome screen will usually pop up automatically. If nothing opens on your screen, simply open any web browser and go to:<br><br>{ip}",
        "es": "Suele abrirse una ventana de bienvenida automáticamente. Si no aparece en tu pantalla, abre el navegador (Chrome, Safari, etc.) e ingresa a:<br><br>{ip}",
    },
    "about_apps_tagline": {
        "fr": "Des outils de poche pour communiquer hors réseau.",
        "en": "Pocket devices and clients to navigate the off-grid mesh.",
        "es": "Herramientas de bolsillo para navegar la red sin internet.",
    },
    "about_app1_h2": {
        "fr": "1. FireFly pour Android",
        "en": "1. FireFly for Android",
        "es": "1. FireFly para Android",
    },
    "about_app1_title": {
        "fr": "Application Téléphone & Tablette",
        "en": "Phone & Tablet Mobile Client",
        "es": "Aplicación para Teléfonos y Tablets",
    },
    "about_app1_desc": {
        "fr": "Une application Android autonome qui permet d'échanger des messages et d'interagir directement avec le maillage radio des Lucioles. Elle fonctionne partout sur le terrain, sans réseau cellulaire, sans forfait de données et sans compte externe.",
        "en": "A lightweight, independent Android application built to chat, send whispers, and exchange packets across Firefly mesh nodes. Works natively out in the field without internet, cellular data plans, or cloud accounts.",
        "es": "Una aplicación móvil diseñada para intercambiar mensajes y paquetes de datos directamente a través de los nodos de radio Luciérnaga (Fireflies). Funciona de forma completamente independiente, sin datos móviles, sin internet y sin cuentas corporativas.",
    },
    "about_app1_github": {
        "fr": "Dépôt GitHub & Code Source",
        "en": "GitHub Repository & Code",
        "es": "Repositorio y Código en GitHub",
    },
    "about_app2_h2": {
        "fr": "2. FireFly pour Consoles RK3326 (R36S / R36MAX)",
        "en": "2. FireFly for RK3326 Handhelds (R36S / R36MAX)",
        "es": "2. FireFly para Consolas RK3326 (R36S / R36MAX)",
    },
    "about_app2_title": {
        "fr": "Terminal de Poche sous dArkOS",
        "en": "Pocket Terminal running dArkOS",
        "es": "Terminal Portátil con dArkOS",
    },
    "about_app2_desc": {
        "fr": "Transformez votre petite console de jeux rétro à puce Rockchip RK3326 (R36S, R36MAX) en un terminal de transmission autonome et durci. Grâce au système dArkOS / dArkOS4clone et une radio LoRa branchée, naviguez dans le réseau avec de vrais boutons physiques, sans dépendre d'un smartphone.",
        "en": "Turn an affordable Rockchip RK3326 retro handheld console (such as the R36S or R36MAX running dArkOS / dArkOS4clone) into a dedicated tactical mesh terminal. Plug in a LoRa radio module and navigate local peer channels using tactile physical controls—no phone required.",
        "es": "Convierte tu consola retro portátil con procesador Rockchip RK3326 (R36S o R36MAX, con sistema dArkOS / dArkOS4clone) en un terminal de radio autónomo. Con un módulo LoRa conectado, navega por los canales de la red usando sus botones físicos sin depender de ningún smartphone.",
    },
    "about_app2_github": {
        "fr": "Dépôt GitHub & Instructions",
        "en": "GitHub Repository & Setup",
        "es": "Repositorio e Instrucciones en GitHub",
    },
    "about_app_download": {
        "fr": "Télécharger depuis cette Bûche",
        "en": "Download from this Stump",
        "es": "Descargar desde este Stump",
    },
    "about_app_missing": {
        "fr": "Pas encore sur cette Bûche.",
        "en": "Not on this Stump yet.",
        "es": "Todavía no está en este Stump.",
    },
    "about_app_github_note": {
        "fr": "(demande internet)",
        "en": "(needs internet)",
        "es": "(requiere internet)",
    },
    "about_app1_install": {
        "fr": "Ouvrir le fichier l'installe ; votre téléphone peut d'abord demander d'autoriser les installations depuis le navigateur.",
        "en": "Opening the file installs it; your phone may first ask you to allow installs from your browser.",
        "es": "Abrir el archivo lo instala; tu teléfono puede pedirte primero que permitas instalaciones desde el navegador.",
    },
    "about_app2_install": {
        "fr": "Copiez le zip sur la console et lancez bash deploy/install.sh par SSH. L'installation demande internet sur la console (Wi-Fi ou Ethernet USB).",
        "en": "Copy the zip to the handheld and run bash deploy/install.sh over SSH. The install needs internet on the handheld (Wi-Fi or USB Ethernet).",
        "es": "Copia el zip a la consola y ejecuta bash deploy/install.sh por SSH. La instalación necesita internet en la consola (Wi-Fi o Ethernet USB).",
    },
    "tools_apps_h2": {
        "fr": "Applications",
        "en": "Apps",
        "es": "Aplicaciones",
    },
    "tools_other_h2": {
        "fr": "Autres fichiers",
        "en": "Other files",
        "es": "Otros archivos",
    },
    "tools_readme": {
        "fr": "Lisez-moi",
        "en": "Read me",
        "es": "Léeme",
    },
    "tools_tech_h2": {
        "fr": "Outils du technicien",
        "en": "Technician tools",
        "es": "Herramientas del técnico",
    },
    "admin_home_header": {"fr": "Section de la page d'accueil", "en": "Home page section", "es": "Sección de la página de inicio"},
    "admin_home_place": {
        "fr": "Où la placer",
        "en": "Where to put it",
        "es": "Dónde ponerla",
    },
    "admin_home_top": {
        "fr": "Au-dessus des boutons",
        "en": "Above the buttons",
        "es": "Encima de los botones",
    },
    "admin_home_bottom": {
        "fr": "Sous les boutons",
        "en": "Under the buttons",
        "es": "Debajo de los botones",
    },
    "admin_home_left": {
        "fr": "À côté, à gauche (sur un grand écran)",
        "en": "Beside them, on the left (on a wide screen)",
        "es": "Al lado, a la izquierda (en pantalla ancha)",
    },
    "admin_home_right": {
        "fr": "À côté, à droite (sur un grand écran)",
        "en": "Beside them, on the right (on a wide screen)",
        "es": "Al lado, a la derecha (en pantalla ancha)",
    },
    "admin_home_height": {
        "fr": "Hauteur du cadre (pixels, 80 à 1200)",
        "en": "Frame height (pixels, 80 to 1200)",
        "es": "Altura del marco (píxeles, 80 a 1200)",
    },
    "admin_home_height_note": {
        "fr": "Pour une page complète, affichée dans un cadre. Sur un téléphone, « à côté » passe au-dessus (gauche) ou en dessous (droite).",
        "en": "For a complete page, shown in a frame. On a phone, “beside” goes above (left) or under (right).",
        "es": "Para una página completa, mostrada en un marco. En un teléfono, «al lado» pasa arriba (izquierda) o debajo (derecha).",
    },
    "admin_home_show": {
        "fr": "L'afficher sur la page d'accueil",
        "en": "Show it on the home page",
        "es": "Mostrarla en la página de inicio",
    },
    "admin_home_save": {
        "fr": "Enregistrer",
        "en": "Save",
        "es": "Guardar",
    },
    "admin_home_saved": {
        "fr": "Enregistré. Recharge la page d'accueil pour voir.",
        "en": "Saved. Reload the home page to see it.",
        "es": "Guardado. Recarga la página de inicio para verlo.",
    },
    "admin_home_failed": {
        "fr": "Pas enregistré : {why}",
        "en": "Not saved: {why}",
        "es": "No guardado: {why}",
    },
    "feature_off": {
        "fr": "Cette fonction n'est pas offerte sur ce nœud.",
        "en": "This feature isn't offered on this node.",
        "es": "Esta función no está disponible en este nodo.",
    },
    "home_upload_error": {
        "fr": "échec de l'envoi — problème de connexion. réessaie.",
        "en": "upload failed — connection problem. try again.",
        "es": "error al subir — problema de conexión. intenta de nuevo.",
    },

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
    "billboard_title_placeholder": {
        "fr": "titre de l'avis",
        "en": "notice title",
        "es": "título del aviso",
    },
    "billboard_body_placeholder": {
        "fr": "détails (facultatif)",
        "en": "details (optional)",
        "es": "detalles (opcional)",
    },

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

    "files_admin_password": {
        "fr": "Mot de passe admin",
        "en": "Admin password",
        "es": "Contraseña de admin",
    },

    # ---- /admin -- no link anywhere points here; see
    # barkeep._render_admin_login_page's own docstring for why. ----
    "admin_header": {"fr": "Admin", "en": "Admin", "es": "Admin"},
    "admin_wrong_password": {
        "fr": "Mot de passe incorrect.",
        "en": "Wrong password.",
        "es": "Contraseña incorrecta.",
    },
    "admin_login_button": {"fr": "Connexion", "en": "Log in", "es": "Iniciar sesión"},
    "admin_select_intro": {
        "fr": "Coche les fichiers à supprimer, puis confirme une seule fois.",
        "en": "Check the files to delete, then confirm once.",
        "es": "Marca los archivos a eliminar y confirma una sola vez.",
    },
    "admin_delete_selected": {
        "fr": "Supprimer la sélection",
        "en": "Delete selected",
        "es": "Eliminar selección",
    },
    "admin_deleted_notice": {
        "fr": "{n} fichier(s) supprimé(s).",
        "en": "{n} file(s) deleted.",
        "es": "{n} archivo(s) eliminado(s).",
    },
    "admin_post_deleted_notice": {
        "fr": "{n} message(s) supprimé(s).",
        "en": "{n} post(s) deleted.",
        "es": "{n} mensaje(s) eliminado(s).",
    },
    "admin_billboard_header": {
        "fr": "Babillard",
        "en": "Billboard",
        "es": "Tablón",
    },

    "admin_theme_header": {"fr": "Thème de ce navigateur", "en": "Theme on this browser", "es": "Tema de este navegador"},
    "admin_theme_browser_intro": {
        "fr": "Seulement pour ce navigateur-ci : les visiteurs ne voient rien de ces boutons. Pour tout le monde, c'est la section au-dessus.",
        "en": "For this browser only: visitors don't see what these buttons do. For everyone, use the section above.",
        "es": "Solo para este navegador: los visitantes no ven lo que hacen estos botones. Para todos, usa la sección de arriba.",
    },
    "admin_logout": {"fr": "Se déconnecter", "en": "Log out", "es": "Cerrar sesión"},
    "admin_layout_header": {"fr": "Ordre des boutons", "en": "Order of the buttons", "es": "Orden de los botones"},
    "admin_layout_intro": {
        "fr": "Glisse-les à leur place (ou utilise les flèches sur un téléphone), masque ceux qu'on ne doit pas voir, puis enregistre. Pour tout le monde, tout de suite. Une page masquée répond encore à son adresse.",
        "en": "Drag them into place (or use the arrows on a phone), hide the ones people shouldn't see, then save. For everyone, right away. A hidden page still answers at its address.",
        "es": "Arrástralos a su sitio (o usa las flechas en un teléfono), oculta los que no deban verse y guarda. Para todos, enseguida. Una página oculta sigue respondiendo en su dirección.",
    },
    "admin_layout_nav": {"fr": "Barre de navigation (chaque page)", "en": "Navigation row (every page)", "es": "Barra de navegación (cada página)"},
    "admin_layout_tiles": {"fr": "Tuiles de la page d'accueil", "en": "Home page tiles", "es": "Mosaicos de la página de inicio"},
    "admin_layout_hide": {"fr": "Masquer", "en": "Hide", "es": "Ocultar"},
    "admin_layout_show": {"fr": "Afficher", "en": "Show", "es": "Mostrar"},
    "admin_layout_save": {"fr": "Enregistrer l'ordre", "en": "Save the order", "es": "Guardar el orden"},
    "admin_layout_reset": {"fr": "Ordre par défaut", "en": "Default order", "es": "Orden predeterminado"},
    "admin_layout_saved": {"fr": "Ordre enregistré.", "en": "Order saved.", "es": "Orden guardado."},
    "admin_layout_not_saved": {"fr": "L'ordre n'a pas pu être enregistré.", "en": "The order couldn't be saved.", "es": "No se pudo guardar el orden."},
    "admin_site_theme_header": {"fr": "Thème pour tout le monde", "en": "Theme for everyone", "es": "Tema para todos"},
    "admin_site_theme_intro": {
        "fr": "L'apparence que voit chaque visiteur, sur chaque page. Elle change tout de suite, sans redémarrer.",
        "en": "The look every visitor gets, on every page. It changes right away, no restart.",
        "es": "El aspecto que ve cada visitante, en cada página. Cambia enseguida, sin reiniciar.",
    },
    "admin_site_theme_setup": {
        "fr": "Comme à l'installation ({name})",
        "en": "As set up ({name})",
        "es": "Como en la instalación ({name})",
    },
    "admin_site_theme_save": {"fr": "Appliquer pour tout le monde", "en": "Apply for everyone", "es": "Aplicar para todos"},
    "admin_site_theme_saved": {"fr": "Thème pour tout le monde : {name}.", "en": "Theme for everyone: {name}.", "es": "Tema para todos: {name}."},
    "admin_site_theme_not_saved": {
        "fr": "Le thème n'a pas pu être enregistré.",
        "en": "The theme couldn't be saved.",
        "es": "No se pudo guardar el tema.",
    },
    "admin_tools_header": {"fr": "Page Outils", "en": "Tools page", "es": "Página de herramientas"},
    "admin_tools_intro": {
        "fr": "Coche ce qu'il faut retirer de la page Outils ; c'est effacé de la carte SD. Le Provisioner remet ses propres outils (Provisioner, flasheur, applis FireFly) à la prochaine mise à jour, sauf avec --skip-tools.",
        "en": "Tick what to take off the Tools page; it's deleted from the SD card. The Provisioner puts its own tools (Provisioner, flasher, FireFly apps) back at its next update, unless run with --skip-tools.",
        "es": "Marca lo que quieras quitar de la página de herramientas; se borra de la tarjeta SD. El Provisioner vuelve a poner sus propias herramientas (Provisioner, flasheador, apps FireFly) en su próxima actualización, salvo con --skip-tools.",
    },
    "admin_tools_whole": {
        "fr": "toute la section ({n} fichiers)",
        "en": "the whole section ({n} files)",
        "es": "toda la sección ({n} archivos)",
    },
    "admin_tools_none": {"fr": "Aucun outil sur la carte.", "en": "No tools on the card.", "es": "No hay herramientas en la tarjeta."},
    "admin_tools_confirm": {
        "fr": "Effacer de la carte SD les outils cochés ?",
        "en": "Delete the ticked tools from the SD card?",
        "es": "¿Borrar de la tarjeta SD las herramientas marcadas?",
    },
    "admin_theme_site": {"fr": "Thème du site", "en": "Site theme", "es": "Tema del sitio"},
    "admin_theme_amber": {"fr": "Ambre", "en": "Amber", "es": "Ámbar"},
    "admin_theme_phosphor": {"fr": "Phosphore", "en": "Phosphor", "es": "Fósforo"},
    "admin_theme_oled": {"fr": "OLED", "en": "OLED", "es": "OLED"},
    "admin_theme_paper": {"fr": "Papier", "en": "Paper", "es": "Papel"},
    "admin_theme_custom_intro": {
        "fr": "Ou choisis une palette personnalisée :",
        "en": "Or choose a custom palette:",
        "es": "O elige una paleta personalizada:",
    },
    "admin_color_bg": {"fr": "Fond", "en": "Background", "es": "Fondo"},
    "admin_color_panel": {"fr": "Panneau", "en": "Panel", "es": "Panel"},
    "admin_color_text": {"fr": "Texte", "en": "Text", "es": "Texto"},
    "admin_color_ember": {"fr": "Accent", "en": "Accent", "es": "Acento"},
    "admin_color_border": {"fr": "Bordure", "en": "Border", "es": "Borde"},
    "admin_save_palette": {
        "fr": "Enregistrer la palette",
        "en": "Save Custom Palette",
        "es": "Guardar paleta",
    },
    "admin_reset_palette": {
        "fr": "Réinitialiser la palette",
        "en": "Reset Palette",
        "es": "Restablecer paleta",
    },
    "admin_logo_header": {"fr": "Logo", "en": "Logo", "es": "Logo"},
    "admin_save_logo": {
        "fr": "Enregistrer le logo SVG",
        "en": "Save SVG Logo",
        "es": "Guardar logo SVG",
    },
    "admin_reset_logo": {
        "fr": "Rétablir le logo par défaut",
        "en": "Reset Default Logo",
        "es": "Restablecer logo predeterminado",
    },
    "admin_hide_logo": {"fr": "Masquer le logo", "en": "Hide Logo", "es": "Ocultar logo"},
    "admin_logo_reset_notice": {
        "fr": "Recharge la page pour revoir le logo par défaut.",
        "en": "Reload the page to see the default logo again.",
        "es": "Recarga la página para ver de nuevo el logo predeterminado.",
    },

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
    # "What a Stump is": the setups and who connects (as in the README).
    "about_setups_h2": {"fr": "Un Stump, trois montages", "en": "One Stump, three setups", "es": "Un Stump, tres montajes"},
    "about_setups_intro": {
        "fr": "Le même logiciel tourne sur une petite carte ou sur un ordinateur, avec ou sans radio.",
        "en": "The same firmware runs on a small board or on a computer, with or without a radio.",
        "es": "El mismo programa funciona en una placa pequeña o en una computadora, con o sin radio.",
    },
    "about_setup1_desc": {
        "fr": "Le nœud de poche : son propre Wi-Fi, une carte SD pour les fichiers, la radio LoRa.",
        "en": "The pocket node: its own Wi-Fi, an SD card for files, the LoRa radio.",
        "es": "El nodo de bolsillo: su propio Wi-Fi, una tarjeta SD para archivos, la radio LoRa.",
    },
    "about_setup2_desc": {
        "fr": "Un ordinateur en nœud complet, sur le maillage LoRa directement (Heltec en USB).",
        "en": "A computer as a full node, on the LoRa mesh directly (Heltec on USB).",
        "es": "Una computadora como nodo completo, directamente en la malla LoRa (Heltec por USB).",
    },
    "about_setup3_desc": {
        "fr": "Un ordinateur sans radio : il rejoint LoRa par un Stump auquel il est enchaîné.",
        "en": "A computer with no radio of its own: it reaches LoRa through a Stump it's chained to.",
        "es": "Una computadora sin radio propia: llega a LoRa a través de un Stump encadenado.",
    },
    "about_setups_chain": {
        "fr": "Les Stumps s'enchaînent par Wi-Fi et relaient les uns pour les autres. Ils se parlent dans <b>#wifi-mesh</b> ; les gens sur LoRa arrivent dans <b>#lora-mesh</b>.",
        "en": "Stumps chain to each other over Wi-Fi and relay for each other. They talk in <b>#wifi-mesh</b>; people on LoRa land in <b>#lora-mesh</b>.",
        "es": "Los Stumps se encadenan por Wi-Fi y retransmiten unos para otros. Hablan en <b>#wifi-mesh</b>; la gente en LoRa llega a <b>#lora-mesh</b>.",
    },
    "about_clients_h2": {"fr": "Qui s'y connecte", "en": "Who connects", "es": "Quién se conecta"},
    "about_client_android": {
        "fr": "FireFly. Par LoRa avec son propre Heltec V3, ou par le Wi-Fi d'un Stump depuis son panneau Stump.",
        "en": "FireFly. Over LoRa with its own Heltec V3, or over a Stump's Wi-Fi from its Stump panel.",
        "es": "FireFly. Por LoRa con su propio Heltec V3, o por el Wi-Fi de un Stump desde su panel Stump.",
    },
    "about_client_handheld_title": {"fr": "Console portable", "en": "Handheld", "es": "Consola portátil"},
    "about_client_handheld": {
        "fr": "R36S / R36MAX, FireFly. Par LoRa avec son propre Heltec V3.",
        "en": "R36S / R36MAX, FireFly. Over LoRa with its own Heltec V3.",
        "es": "R36S / R36MAX, FireFly. Por LoRa con su propio Heltec V3.",
    },
    "about_client_browser_title": {"fr": "Tout navigateur", "en": "Any browser", "es": "Cualquier navegador"},
    "about_client_browser": {
        "fr": "Téléphone ou ordinateur, sur le Wi-Fi du Stump (ou le réseau où il se trouve).",
        "en": "A phone or a computer, on the Stump's Wi-Fi (or the network it's on).",
        "es": "Un teléfono o una computadora, en el Wi-Fi del Stump (o la red en la que está).",
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
    "about_link_email_title": {"fr": "Courriel", "en": "Email", "es": "Correo"},

    # ---- About page: view tabs (About / Connect / Hardware) ----

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

    # ---- About page: hardware gallery. Images live on the SD card,
    # not baked into the firmware image -- see /about/img route. ----
}
