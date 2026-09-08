# Project Stump -- node_common.py
#
# Identity + LXMF router bring-up: the part of node setup that has
# nothing to do with being a hub versus a handheld. Extracted out of
# example_node.py specifically because that file is NOT safe to import
# from anywhere else -- its main() runs unconditionally at module level
# (no `if __name__` guard), so importing it just to reach setup_node()
# drags the whole hub boot (captive portal, BarKeep, fserv, the AP)
# onto a board that never wanted any of that.
#
# Confirmed via a real cross-team report: Firefly's firmware needed
# this exact piece and, unable to safely import it, duplicated the
# function instead -- flagged as needing extraction once whoever owns
# example_node.py was ready. This is that extraction.
#
# THIS FILE HAS NO MODULE-LEVEL SIDE EFFECTS. No board bring-up, no
# network calls, nothing runs just by being imported. Every function
# here is inert until called. That's the whole point: a handheld
# importing this to get identity/router setup should never accidentally
# also get a captive portal.
#
# WHAT DIDN'T MOVE, AND WHY
# --------------------------
# setup_node()'s original inbound-message handling was hardcoded to
# `import rrc_mesh; rrc_mesh.on_message(...)` -- Stump-hub-specific
# behaviour that has no business in a shared module a handheld device
# also uses. Rather than move it here (wrong) or strip it silently
# (loses Stump's real behaviour), on_message/on_announce are now
# parameters: each caller supplies its own handler. Stump's
# example_node.py passes the exact rrc_mesh-forwarding callback it
# always has, unchanged; Firefly (or anything else) passes its own.
# Neither is baked into this file. Peripheral processing
# (active_peripherals) is Stump-specific too and stays entirely in the
# caller's own callback, for the identical reason.


def peer_name(router, dest_hash):
    """A display name for a peer, for logging -- falls back to a short
    hex identifier (not a bare '?') if the router doesn't have a name
    on file yet, so a technician reading the log can still tell WHICH
    unnamed peer sent something rather than seeing an undifferentiated
    '?' for every one of them."""
    try:
        entry = router.peers.get(dest_hash)
        if entry and entry.get("name"):
            return entry["name"]
    except Exception:
        pass
    return dest_hash.hex()[:8]


def setup_node(rns, node_name, on_message=None, on_announce=None):
    """Registers the node's LXMF delivery identity and wires up
    whatever callbacks the caller actually wants.

    on_message(message) / on_announce(destination_hash, display_name)
    are the caller's OWN handlers, not assumed. Passing None for either
    simply leaves that callback unregistered on the router -- confirmed
    safe: LXMRouter checks `if self._delivery_callback:` before every
    invocation, defaulting to None, so an unregistered callback is a
    normal, harmless state, not a special case this function needs to
    paper over with a no-op.

    Returns (dest, router), same shape as the original setup_node() --
    existing callers don't need to change how they unpack the result,
    only how they call it.
    """
    from urns.lxmf import LXMRouter
    router = LXMRouter(identity=rns.identity)
    dest = router.register_delivery_identity(rns.identity, display_name=node_name)

    if on_message is not None:
        router.register_delivery_callback(on_message)
    if on_announce is not None:
        router.register_announce_callback(on_announce)

    return dest, router
