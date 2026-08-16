"""Markdown spans where a convention renders as visible text.

Two gates in this repository read a text and look for something. Both must
ignore what the text merely *displays*: every document that teaches a
convention quotes it, so a checker that counted a quoted occurrence would
refuse the documents that describe its own rule. #2057 would otherwise have
satisfied its gate off its own spec comment.

This module holds those spans once. ``check_disposition.py`` looks for a marker;
``check_citations.py`` looks for citations.

**The two want different spans, and the difference is not cosmetic.** A marker
is HTML that works by rendering as nothing, so backticks around it change what
it does: a quoted marker is a display. A citation is a path, and backticks are
the ordinary way to write one — ``CLAUDE.md:113`` is a citation, not a display
of the citation form. Measured on #2131: six bare citations were published
inside code spans, and a code-span-stripping scan found none of them.

So a marker gate strips both spans and a citation checker strips fences only.
"""

from __future__ import annotations

import re

# Every span where content renders as visible text. The fence pattern
# backreferences its own opening run, so a ``` block cannot be closed by a ~~~
# one; an unclosed fence runs to the end of the text, which is how GitHub
# renders it too.
#
# The closing run may be LONGER than the opening one — CommonMark allows it, and
# a bare backreference does not. That mismatch made the pattern miss the close
# and strip to end of text, which removed a real table posted after the block
# and refused an author who had complied. The error direction decides the shape
# here: stripping too much is a false refusal, stripping too little is only a
# false pass, and refusing correct work is the costlier error.
_FENCED = re.compile(
    r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def strip_fenced(text: str) -> str:
    """Remove fenced blocks only."""
    return _FENCED.sub("", text)


def strip_quoted(text: str) -> str:
    """Remove every span where content would render as visible text.

    Fences first. A code-span pass run first would eat backtick runs out of the
    fence delimiters and leave the block body exposed.
    """
    return _CODE_SPAN.sub("", strip_fenced(text))
