"""
The firmware's own build switches, flipped in a throwaway copy of onlykey.h.

WHY EVERY FLAG IS SET EXPLICITLY, ALWAYS

The obvious design is to build each pin "as it is" and only override what you
need. That is wrong here, and measuring the nine pins in ok-versions.json shows
why - the committed state of the flags is not a statement about the release:

    release       DEBUG   STD_VERSION   DEFINED_HWID
    v3.0.4        ON      ON            //OK_HW_DUO
    v3.0.3        ON      ON            //OK_HW_DUO
    v3.0.2        off     ON            //OK_HW_DUO
    v3.0.1        off     ON            //OK_HW_DUO
    v3.0.0        off     ON            //OK_HW_DUO
    v2.1.2        ON      ON            //OK_HW_COLOR
    v2.1.1        off     off           absent
    v2.1.0        ON      ON            absent
    v0.2-beta.8   ON      ON            absent

DEBUG is on in five of nine and off in four, in no pattern - v3.0.3 and v3.0.4
would ship with a serial console open, and v2.1.1 would ship as the IN TRVL
edition. Those are not release decisions; they are whatever happened to be in
the working tree when the tag was cut. The releases were never finalised to a
standard set of build flags.

So there is no "as shipped" build here, and this module never reads a flag in
order to keep it. Every variant states what it wants and gets it. What a pin
contributes is SOURCE, not configuration.

MATCHED ON THE DEFINE, NEVER ON THE COMMENT AFTER IT

The same define carries different trailing comments across the line - the 2019
beta writes `#define STD_VERSION //Define for US Version Firmare` where the 3.0
line writes `//Define for STD edition firmare, undefine for IN TRVL edition
firmware`. Comparing whole lines reads that as ABSENT and reports the build as
travel, which is a different firmware: no FIDO, no encrypted profile,
set_private returns early. A comment is not a build option.

Commented-out is always tested first, because `//#define X` contains
`#define X`.

This mirrors gateDefine() in ok-rn/android/okemu/scripts/stage.js, which learned
all of the above the expensive way. It is reimplemented rather than imported
because that file is overwhelmingly about running the firmware HOSTED - it drops
mk20dx128.c, pins_teensy.c and the usb stack, rebases flash, stubs the irq
intrinsics - none of which a real Teensy build may do.
"""

import re


class GateError(Exception):
    """A gate could not be set. Always fatal: a silently wrong flag is a
    different firmware wearing the right version number."""


def _find(text, name):
    """(state, whole_line) where state is True, False, or None for absent."""
    off = re.search(r"^[ \t]*//[ \t]*#define[ \t]+%s\b.*$" % re.escape(name),
                    text, re.M)
    if off:
        return False, off.group(0)
    on = re.search(r"^[ \t]*#define[ \t]+%s\b.*$" % re.escape(name), text, re.M)
    if on:
        return True, on.group(0)
    return None, None


def read(text, name):
    """True / False / None, without changing anything."""
    return _find(text, name)[0]


def value(text, name):
    """Whatever follows the define, comment stripped. Empty string if none,
    None if the define is absent. Used to tell OK_HW_DUO from OK_HW_COLOR."""
    state, line = _find(text, name)
    if state is None:
        return None
    body = re.sub(r"^[ \t]*(//[ \t]*)?#define[ \t]+%s\b" % re.escape(name),
                  "", line)
    return body.split("//")[0].strip()


def set_define(text, name, want, required=True, where="onlykey.h"):
    """Turn a define on or off, KEEPING THE LINE'S OWN COMMENT.

    Rewriting the line to some canonical spelling would replace one release's
    comment with another's, which is how a staged tree quietly stops being the
    release it claims to be. So the flip is done in place: strip the slashes,
    or add them.

    Returns (text, state) where state is True, False, or None for "the define
    is not in this tree at all". None is not the same as False and callers must
    not conflate them: a switch that predates a release is a fact about that
    release, while a switch that is present and off is a decision.
    """
    state, line = _find(text, name)

    if state is None:
        if required:
            raise GateError(
                "%s is absent from %s, so it cannot be set %s. Either it was "
                "renamed at this pin, or this release predates it."
                % (name, where, "on" if want else "off"))
        # Optional and absent: this pin predates the switch. Nothing to do and
        # nothing wrong - it is reported, not raised.
        return text, None

    if state == want:
        return text, state

    if want:
        new = re.sub(r"^([ \t]*)//[ \t]*", r"\1", line)
    else:
        new = re.sub(r"^([ \t]*)", r"\g<1>//", line)
    return text.replace(line, new, 1), want


def apply(onlykey_h, debug, std, duo):
    """Set all three gates on the text of libraries/onlykey/onlykey.h.

    Returns (text, notes). Raises GateError if a variant is not buildable at
    this pin - which is the point: refusing beats emitting a classic image
    under a filename that says DUO.
    """
    notes = []
    text = onlykey_h

    # DEBUG also decides the version keyword the firmware reports, with no help
    # from us:
    #
    #     #ifdef DEBUG
    #     #define OKversionkeyword "-test"
    #     #else
    #     #define OKversionkeyword "-prod"
    #     #endif
    #
    # and node-onlykey-lib reads exactly that to decide capabilities()
    # .debugConsole. So a build that lies about this is visible from the app -
    # a good property, and the reason not to stamp the version by hand. The
    # stock in-docker-build.sh substitutes ".0-test" for the commit hash, which
    # on these pins matches nothing at all: the version is composed from
    # separate defines. That substitution is dead code and is not carried over.
    #
    # v0.2-beta.8 has no OKversionkeyword anywhere - it predates the scheme,
    # and the library treats such firmware as debugConsole: null, unknown.
    text, got = set_define(text, "DEBUG", debug)
    notes.append("DEBUG %s" % ("on" if got else "off"))

    # DEBUG_CTAP_VERBOSE follows DEBUG down and is never turned on: it fires on
    # every presence-test poll and floods the console. Only newer trees have it.
    if not debug:
        text, _ = set_define(text, "DEBUG_CTAP_VERBOSE", False, required=False)

    text, got = set_define(text, "STD_VERSION", std)
    notes.append("edition %s" % ("standard" if got else "IN TRVL"))

    # The model override. onlykey.h carries `//#define DEFINED_HWID OK_HW_DUO`
    # with "override auto hw detection, hardcoded" at its use site in
    # okcore.cpp - the firmware's own way of saying which model a build is.
    #
    # On hardware the model is normally read from the chip: HW_ID is
    # SIM_SDID_PINID, the package-type field, combined with an analog reading.
    # The override exists precisely so a build need not guess.
    #
    # MEASURED: it names OK_HW_DUO on the 3.0 line, OK_HW_COLOR at v2.1.2, and
    # is absent at v2.1.1, v2.1.0 and v0.2-beta.8 - the DUO postdates them.
    # Asking those to be a DUO is asking for a device that never existed, so
    # this refuses rather than quietly producing a classic.
    if duo:
        v = value(text, "DEFINED_HWID")
        if v is None:
            raise GateError(
                "DEFINED_HWID is absent at this pin, so there is no DUO build "
                "of this release - the DUO postdates it")
        if "OK_HW_DUO" not in v:
            raise GateError(
                "DEFINED_HWID names %r at this pin, not OK_HW_DUO. Changing "
                "the value would invent a configuration that never shipped."
                % v)
        text, _ = set_define(text, "DEFINED_HWID", True)
        notes.append("model DUO")
    else:
        # Classic is the ordinary detection path: the override off, or simply
        # not present on releases that never had one.
        text, _ = set_define(text, "DEFINED_HWID", False, required=False)
        notes.append("model classic")

    return text, notes


def sync_keylayouts(keylayouts_h, debug):
    """Keep keylayouts.h on the same side of the gate as onlykey.h.

    The header asks for this itself - "keep it in sync manually" sits above a
    define whose comment is "comment this out (to match #undef DEBUG in
    onlykey.h) for a release build". Two switches for one decision, and the
    second is in a file nobody edits.

    What hangs off it: with KEYLAYOUTS_DEBUG_BUILD defined, all twenty-six
    SUPPORT_LAYOUT_* lines are commented out and ONLY US ENGLISH COMPILES - its
    block is the one with no guard. Every other layout takes an empty branch and
    types nothing at all. A debug build that silently cannot type a German
    password is a confusing thing to chase.

    Not required: a release old enough to predate the switch is a fact about
    that release, not a failure.
    """
    text, got = set_define(keylayouts_h, "KEYLAYOUTS_DEBUG_BUILD", debug,
                           required=False, where="keylayouts.h")
    if got is None:
        return text, "no KEYLAYOUTS_DEBUG_BUILD at this pin"
    return text, ("US English only" if got else "all layouts")
