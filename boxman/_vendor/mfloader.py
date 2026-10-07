"""miniformat -- a tiny, strict config format with YAML syntax.  Single file,
stdlib only.

This is the loader.  It is self-contained: to vendor it, copy just this file
(``from miniformat import mfloader``, or rename it as you like).  The optional writer is mfdumper.py.

The promise: any YAML parser can parse a document this module accepts (and
any editor's YAML highlighting works on it).  What it *means* is up to this
module: *every scalar is a string* (``no``, ``8080`` and ``1.10`` stay as
written; convert them where you use them, e.g. ``int(cfg["db"]["port"])``)
and ``#+include`` is expanded.

The format
----------
* block maps (``key: value``) and block lists (``- item``), nested by
  indenting with spaces (tabs are an error);
* a list under a key is always indented below it (never at the key's column);
* tab characters only appear in comments and ``|`` blocks (``\\t`` in quotes);
* scalars: plain (``text``) or double-quoted with JSON escapes (``"a\\tb"``);
* multi-line text: ``|`` literal blocks only (clip chomping: one final newline);
* ``{}`` and ``[]`` as the only flow syntax, for empty containers;
* ``# comments``; an optional leading ``---``.
* an empty value (``key:``) loads as ``""``.
* ``#+name args`` on a line of its own is a *pragma*: a comment to any YAML
  parser, but meaningful here.  ``#+`` immediately followed by a character
  (no space) is reserved for pragmas, so an unknown or malformed one is an
  error (a typo never silently vanishes); ``#+`` alone or ``#+ text`` (with a
  space) is an ordinary comment.  The only pragma so far:

  ``#+include path`` is replaced by the text of that file (relative to the
  including file), indented to the ``#`` column.  A path with ``*``, ``?`` or
  ``[`` is a glob (``conf.d/*.yaml``, ``**`` for any depth): every matching
  file, sorted by path; matching nothing is fine.  Included files are trusted input: there is no
  sandboxing.  ``load(fp)`` uses the file's directory; for
  ``loads(text)`` pass ``base=``.

Rejected with a line-numbered error: tabs for indentation, anchors/aliases/
tags, ``>`` folded scalars, chomp indicators, single quotes, other flow syntax,
duplicate keys, multiple documents, ``a: b: c`` (quote it), and
multi-line plain scalars.

API: ``loads``, ``load``, ``MiniFormatError`` (``.file``, ``.line``).
(``dumps`` / ``dump`` are in mfdumper.py.)
"""

import glob
import json
import os
import re

__version__ = "0.2.0"
__all__ = ["loads", "load", "MiniFormatError"]

# MIT License -- see LICENSE.  Copy this file into your project freely;
# keep this notice.  https://github.com/vivainio/miniformat


class MiniFormatError(ValueError):
    """Raised for input outside the format.

    ``.line`` is 1-based (or None); ``.file`` names an included file (or None
    for the main document).
    """

    def __init__(self, msg, line=None, source=None, file=None):
        self.msg = msg
        self.line = line
        self.file = file
        text = msg if line is None else "line %d: %s" % (line, msg)
        if file is not None:
            text = "%s: %s" % (file, text)
        if source is not None:
            text += "\n    " + source.strip()
        super().__init__(text)


# Characters YAML parsers accept, minus the exotic line breaks (NEL, LS, PS).
_BAD_CHAR = re.compile(
    "[^\t\n\x20-\x7e\xa0-\u2027\u202a-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_COMMENT = re.compile(r" #")
_COLON = re.compile(r":(?:[ \t]|$)")
_DOC_MARK = re.compile(r"(?:---|\.\.\.)(?:[ \t]|$)")
_PLAIN_BAD_START = set("[]{}&*!|>'\"%@`#,")
_PRAGMA = re.compile(r"#\+([a-z][a-z0-9-]*)(?: +(\S.*?))? *$")
_MAX_INCLUDES = 1000


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def loads(text, base=None):
    """Parse a document into nested ``dict`` / ``list`` / ``str``.

    ``base`` is the directory that ``#+include`` paths are relative to; without
    it, ``#+include`` is an error.
    """
    return _Parser(_prepare(text), base).document()


def load(fp):
    """Like :func:`loads`, reading text from a file object.  ``#+include``
    paths are relative to the file's directory when it has a name."""
    name = getattr(fp, "name", None)
    base = os.path.dirname(os.path.abspath(name)) if isinstance(name, str) else None
    return loads(fp.read(), base)


def _prepare(text, file=None):
    """Split text into lines, normalising line ends and checking characters."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for n, line in enumerate(lines, 1):
        m = _BAD_CHAR.search(line)
        if m:
            raise MiniFormatError("unsupported character %r" % m.group(), n, line, file)
    return lines


def _split_comment(s):
    """Drop a trailing ``# comment`` (``#`` must follow whitespace)."""
    m = _COMMENT.search(s)
    return s[: m.start()] if m else s


def _is_blank(line):
    s = line.lstrip(" ")  # a tab-only "blank" line is an error, caught in skip()
    return not s or s[0] == "#"


class _Parser:
    def __init__(self, lines, base=None):
        self.lines = lines
        self.base = base
        # (file, line number) of every line; kept in step with ``lines`` when
        # a #+include splices text in, so errors name the right place
        self.origin = [(None, n) for n in range(1, len(lines) + 1)]
        self.includes = 0
        self.i = 0

    def err(self, msg, i=None):
        i = self.i if i is None else i
        if i >= len(self.lines):
            return MiniFormatError(msg, len(self.lines))
        file, n = self.origin[i]
        return MiniFormatError(msg, n, self.lines[i], file)

    def pragma(self, content):
        m = _PRAGMA.match(content)
        if not m:
            raise self.err("malformed pragma (expected '#+name args', name in a-z)")
        name, arg = m.groups()
        if name != "include":
            raise self.err("unknown pragma '#+%s'" % name)
        if not arg:
            raise self.err("#+include needs a path")
        self.include(arg)

    def include(self, pattern):
        """Replace the #+include line at self.i by the named file's lines (or,
        for a glob, the lines of every matching file in sorted order)."""
        i = self.i
        file = self.origin[i][0]
        base = os.path.dirname(file) if file else self.base
        if base is None:
            raise self.err("#+include needs a base directory (pass base= to loads)")
        is_glob = any(c in pattern for c in "*?[")
        if is_glob:  # no match is fine: an empty conf.d is not an error
            found = glob.glob(os.path.join(glob.escape(base), pattern), recursive=True)
            paths = sorted(p for p in found if os.path.isfile(p))
        else:
            paths = [os.path.join(base, pattern)]
        pad = " " * self.indent_of(i)
        lines, origin = [], []
        for path in paths:
            self.includes += 1
            if self.includes > _MAX_INCLUDES:
                raise self.err("too many includes (is there a loop?)")
            try:
                with open(path, encoding="utf-8") as f:
                    new = _prepare(f.read(), path)
            except (OSError, UnicodeDecodeError) as e:
                shown = path if is_glob else pattern
                raise self.err("cannot include %r: %s" % (shown, e)) from None
            got = [(path, n) for n in range(1, len(new) + 1)]
            k = next((k for k, ln in enumerate(new) if not _is_blank(ln)), None)
            if k is not None and re.match(r"---(?:[ \t]+#.*)?$", new[k]):
                del new[k], got[k]  # a leading '---' of the included document
            lines += [pad + ln if ln else ln for ln in new]
            origin += got
        self.lines[i : i + 1] = lines
        self.origin[i : i + 1] = origin

    # -- cursor helpers ----------------------------------------------------

    def skip(self):
        """Advance to the next structural line; return its index or None."""
        while self.i < len(self.lines) and _is_blank(self.lines[self.i]):
            content = self.lines[self.i].lstrip(" ")
            if content.startswith("#+") and content[2:3] not in ("", " ", "\t"):
                self.pragma(content)  # may splice; look at the same index again
            else:
                self.i += 1
        if self.i >= len(self.lines):
            return None
        line = self.lines[self.i]
        content = line.lstrip(" ")
        if content[0] == "\t":
            raise self.err("tabs are not allowed for indentation")
        if line == content and _DOC_MARK.match(content):
            raise self.err("document markers are only allowed as a first-line '---'")
        if "\t" in _split_comment(content):
            raise self.err(
                "tab character outside a comment or '|' block (use \\t in quotes)"
            )
        return self.i

    def indent_of(self, i):
        line = self.lines[i]
        return len(line) - len(line.lstrip(" "))

    # -- grammar -------------------------------------------------------------

    def document(self):
        k = self.i
        while k < len(self.lines) and _is_blank(self.lines[k]):
            k += 1
        if k < len(self.lines) and re.match(r"---(?:[ \t]+#.*)?$", self.lines[k]):
            self.i = k + 1
        j = self.skip()
        if j is None:
            raise MiniFormatError("empty document")
        if self.indent_of(j) != 0:
            raise self.err("document must start at column 0")
        content = self.lines[j]
        if _split_comment(content).rstrip(" ") in ("{}", "[]"):
            result = {} if content.startswith("{") else []
            self.i += 1
        else:
            result = self.block(-1)
        if self.skip() is not None:
            raise self.err("unexpected content (inconsistent indentation?)")
        return result

    def block(self, parent):
        """Parse the map/list starting at the next line, indented > parent."""
        j = self.skip()
        if j is None or self.indent_of(j) <= parent:
            return None
        ind = self.indent_of(j)
        content = self.lines[j][ind:]
        if content == "-" or content.startswith("- "):
            return self.list_(ind)
        return self.map_(ind)

    def map_(self, ind):
        result = {}
        while True:
            j = self.skip()
            if j is None:
                break
            cur = self.indent_of(j)
            if cur < ind:
                break
            if cur > ind:
                raise self.err("unexpected indentation")
            content = self.lines[j][ind:]
            if content == "-" or content.startswith("- "):
                raise self.err(
                    "list item inside a map; indent list items below their key"
                )
            entry = self.split_entry(content)
            if entry is None:
                if content[0] in self._HINTS:
                    raise self.bad_start(content[0])
                raise self.err("expected 'key: value'")
            key, rest = entry
            if key in result:
                raise self.err("duplicate key %r" % key)
            result[key] = self.value(rest, ind)
        return result

    def list_(self, ind):
        result = []
        while True:
            j = self.skip()
            if j is None:
                break
            cur = self.indent_of(j)
            if cur < ind:
                break
            if cur > ind:
                raise self.err("unexpected indentation")
            content = self.lines[j][ind:]
            if content == "-":
                rest = ""
            elif content.startswith("- "):
                rest = content[2:]
                if rest.startswith(" ") and not _is_blank(rest):
                    raise self.err("exactly one space is allowed after '-'")
            else:
                raise self.err("expected '- ' list item")
            if (
                rest
                and not _is_blank(rest)
                and rest[0] != "|"
                and self.split_entry(rest) is not None
            ):
                # '- key: v' -> rewrite as a map line indented under the dash
                self.lines[j] = " " * (ind + 2) + rest
                result.append(self.map_(ind + 2))
            else:
                result.append(self.value(rest, ind))
        return result

    # -- values ----------------------------------------------------------------

    def value(self, rest, parent):
        """Value after 'key:' or '-'; the current line is self.i."""
        rest = rest.strip(" ")
        if rest == "" or rest[0] == "#":
            self.i += 1
            nested = self.block(parent)
            return "" if nested is None else nested
        if rest[0] == "|":
            if _split_comment(rest).rstrip(" ") != "|":
                raise self.err("only a plain '|' block scalar header is supported")
            return self.block_scalar(parent)
        v = self.inline(rest)
        self.i += 1
        return v

    def inline(self, rest):
        if rest[0] == '"':
            m = _QUOTED.match(rest)
            if not m:
                raise self.err("unterminated or multi-line quoted string")
            tail = rest[m.end() :]
            if not re.fullmatch(r" *| +#.*", tail):
                raise self.err("unexpected text after quoted string")
            return self.unquote(m.group())
        cut = _split_comment(rest).rstrip(" ")
        if cut == "{}":
            return {}
        if cut == "[]":
            return []
        s = cut.rstrip("\t ")
        self.check_plain(s)
        return s

    def unquote(self, token):
        try:
            s = json.loads(token)
        except ValueError as e:
            raise self.err("bad escape in quoted string (%s)" % e) from None
        if any("\ud800" <= c <= "\udfff" for c in s):
            raise self.err("lone surrogate in quoted string")
        return s

    _HINTS = {
        "[": "flow syntax is not supported (only [] and {})",
        "{": "flow syntax is not supported (only [] and {})",
        "&": "anchors are not supported",
        "*": "aliases are not supported",
        "!": "tags are not supported",
        ">": "folded scalars are not supported; use '|'",
        "'": "single quotes are not supported; use double quotes",
        "%": "directives are not supported",
    }

    def bad_start(self, c):
        hint = self._HINTS.get(c, "quote it with double quotes")
        return self.err("a plain scalar cannot start with %r: %s" % (c, hint))

    def check_plain(self, s):
        if not s:
            raise self.err("empty value")
        c = s[0]
        if c in _PLAIN_BAD_START:
            raise self.bad_start(c)
        if c in "-?:" and (len(s) == 1 or s[1] in " \t"):
            raise self.err("a plain scalar cannot start with %r; quote it" % c)
        if _COLON.search(s):
            raise self.err("': ' inside a plain value; quote it with double quotes")
        if "\t" in s:
            raise self.err("tab inside a plain value; quote it and use \\t")

    def split_entry(self, content):
        """Split 'key: rest' -> (key, rest), or None if not a map entry."""
        if content[0] == '"':
            m = _QUOTED.match(content)
            if not m or not _COLON.match(content, m.end()):
                return None
            return self.unquote(m.group()), content[m.end() + 1 :]
        colon = _COLON.search(content)
        if not colon:
            return None
        comment = _COMMENT.search(content)
        if comment and comment.start() < colon.start():
            return None
        key = content[: colon.start()].rstrip(" \t")
        self.check_plain(key)
        return key, content[colon.end() :]

    def block_scalar(self, parent):
        """Read a '|' block whose header is on the current line."""
        self.i += 1
        body = []
        content_indent = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip(" "):
                if content_indent is not None and len(line) > content_indent:
                    raise self.err("whitespace-only line longer than the block indent")
                if content_indent is None and line:
                    # leading blank line; any width is fine only if it is not
                    # wider than the (not yet known) indent -- checked below
                    body.append(line)
                else:
                    body.append("")
                self.i += 1
                continue
            ind = len(line) - len(line.lstrip(" "))
            if content_indent is None:
                if ind <= parent:
                    break
                content_indent = ind
                for k, b in enumerate(body):
                    if len(b) > content_indent:
                        raise self.err(
                            "whitespace-only line longer than the block indent",
                            self.i - len(body) + k,
                        )
                    body[k] = ""
            elif ind < content_indent:
                break
            if ind == content_indent and line[ind] == "\t":
                raise self.err(
                    "a block line cannot start with a tab; use a quoted string"
                )
            body.append(line[content_indent:])
            self.i += 1
        if content_indent is None:
            return ""
        no_final_newline = self.i >= len(self.lines) and body[-1] != ""
        while body and body[-1] == "":
            body.pop()
        if not body:
            return ""
        return "\n".join(body) + ("" if no_final_newline else "\n")
