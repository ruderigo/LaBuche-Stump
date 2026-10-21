# stumpid's own strings (plugins.py: stump.t / ctx.t look here first).
# Moved out of the core's i18n.py: only this plugin uses them.

STRINGS = {
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
}
