"""The YAML a GitHub workflow is written in, read without a YAML library.

The checker carries no YAML parser (README.md says why there are no
dependencies), and the Compose file never needed one: `docker compose config
--format json` renders it. Nothing renders a workflow, so this reads the part
of YAML that workflows are written in - block mappings and sequences, flow
sequences and mappings, plain, quoted and block scalars, and comments - into
dicts, lists, strings, numbers, booleans and None.

**What it does not implement, it refuses.** Anchors, aliases, merge keys,
tags, complex keys, directives and a second document raise `YamlError`, as
`schema.py` refuses a keyword it does not implement, rather than being read
as something else: a workflow read wrongly is a workflow judged wrongly, and
the rule reading it is about secrets. GitHub has accepted anchors since 2025,
so a workflow using one is a real workflow this checker cannot read, and
the rule says so in its message instead of passing it.

It is lenient where GitHub is strict: a file GitHub refuses to load runs
nothing, so reading it a little wrong costs nothing.
"""

import re


class YamlError(Exception):
    """A file this reader cannot read, with the line it stopped at."""

    def __init__(self, line, message):
        super().__init__(message)
        self.line = line
        self.message = message

    def __str__(self):
        return "line " + str(self.line) + ": " + self.message


def load(text):
    """One YAML document, as Python values. Raises `YamlError`."""
    reader = _Reader(text)
    value = reader.node(0)
    end = reader.next()
    if end is not None:
        raise YamlError(end + 1, "unexpected text, less indented than what it follows")
    return value


# -- scalars ----------------------------------------------------------------

_INT = re.compile(r"[-+]?[0-9]+")
_FLOAT = re.compile(r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")

# What YAML 1.2's core schema reads a plain scalar as. Not YAML 1.1's: GitHub
# reads `on:` as the key "on", where a 1.1 reader makes it True.
_NULLS = ("", "~", "null", "Null", "NULL")
_TRUE = ("true", "True", "TRUE")
_FALSE = ("false", "False", "FALSE")


def _resolve(text):
    if text in _NULLS:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    return text


_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n",
    "v": "\v", "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"',
    "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
}
_HEX = {"x": 2, "u": 4, "U": 8}


def _quoted(text, start, line):
    """A quoted scalar starting at `text[start]`: (value, index after it).

    `text` may hold line breaks, folded as YAML folds them: one break is a
    space, each blank line a newline, and a backslash before a break in
    double quotes joins the two lines. Raises `YamlError` when it is not
    closed.
    """
    quote = text[start]
    out = []
    i = start + 1
    while i < len(text):
        c = text[i]
        if quote == "'" and c == "'":
            if text.startswith("''", i):
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        if quote == '"' and c == '"':
            return "".join(out), i + 1
        if quote == '"' and c == "\\":
            nxt = text[i + 1:i + 2]
            if nxt == "\n":
                i = _skip_blank(text, i + 2)
                continue
            if nxt in _HEX:
                digits = text[i + 2:i + 2 + _HEX[nxt]]
                if len(digits) != _HEX[nxt] or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                    raise YamlError(line, "a \\" + nxt + " escape without its hexadecimal digits")
                out.append(chr(int(digits, 16)))
                i += 2 + _HEX[nxt]
                continue
            if nxt not in _ESCAPES:
                raise YamlError(line, "an escape YAML does not have: \\" + nxt)
            out.append(_ESCAPES[nxt])
            i += 2
            continue
        if c == "\n":
            while out and out[-1] in " \t":
                out.pop()
            breaks = 0
            while i < len(text) and text[i] == "\n":
                breaks += 1
                i = _skip_blank(text, i + 1)
            out.append(" " if breaks == 1 else "\n" * (breaks - 1))
            continue
        out.append(c)
        i += 1
    raise YamlError(line, "a quoted scalar that is never closed")


def _skip_blank(text, i):
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _comment_at(text, start=0):
    """Where a comment begins in plain text: a # at the start or after a space."""
    for i in range(start, len(text)):
        if text[i] == "#" and (i == 0 or text[i - 1] in " \t"):
            return i
    return len(text)


def _plain(text):
    """Plain text with its comment, and the spaces before it, removed."""
    return text[:_comment_at(text)].rstrip()


def _refuse_indicator(text, line):
    """A node beginning with a character this reader does not implement."""
    c = text[:1]
    if c == "&":
        raise YamlError(line, "an anchor (&), which this reader does not implement")
    if c == "*":
        raise YamlError(line, "an alias (*), which this reader does not implement")
    if c == "!":
        raise YamlError(line, "a tag (!), which this reader does not implement")
    if c in ("@", "`"):
        raise YamlError(line, "a scalar may not begin with " + c)


# -- flow collections ---------------------------------------------------------


class _Flow:
    """`[a, b]` and `{a: b}`, possibly nested, possibly over several lines."""

    def __init__(self, text, line):
        self.text = text
        self.at = 0
        self.line = line

    def error(self, message):
        return YamlError(self.line, message)

    def space(self):
        while self.at < len(self.text) and self.text[self.at] in " \t\n":
            self.at += 1

    def peek(self):
        self.space()
        return self.text[self.at:self.at + 1]

    def node(self):
        c = self.peek()
        if c == "[":
            return self.sequence()
        if c == "{":
            return self.mapping()
        if c in ("'", '"'):
            value, self.at = _quoted(self.text, self.at, self.line)
            return value
        _refuse_indicator(c, self.line)
        return _resolve(self.plain(",]}"))

    def plain(self, stops, key=False):
        start = self.at
        while self.at < len(self.text):
            c = self.text[self.at]
            if c in stops:
                break
            if c == ":" and self.text[self.at + 1:self.at + 2] in ("", " ", "\t", "\n", ",", "]", "}"):
                if key:
                    break
                raise self.error("a key: value pair inside a flow sequence, "
                                 "which this reader does not implement")
            self.at += 1
        return " ".join(self.text[start:self.at].split())

    def sequence(self):
        self.at += 1
        items = []
        while True:
            if self.peek() == "]":
                self.at += 1
                return items
            if self.peek() == "":
                raise self.error("a flow sequence that is never closed")
            items.append(self.node())
            c = self.peek()
            if c == ",":
                self.at += 1
            elif c != "]":
                raise self.error("expected , or ] in a flow sequence")

    def mapping(self):
        self.at += 1
        result = {}
        while True:
            c = self.peek()
            if c == "}":
                self.at += 1
                return result
            if c == "":
                raise self.error("a flow mapping that is never closed")
            if c in ("'", '"'):
                key, self.at = _quoted(self.text, self.at, self.line)
            else:
                _refuse_indicator(c, self.line)
                key = self.plain(",}", key=True)
            value = None
            if self.peek() == ":":
                self.at += 1
                if self.peek() not in (",", "}"):
                    value = self.node()
            if key in result:
                raise self.error("the key " + key + " appears twice")
            result[key] = value
            c = self.peek()
            if c == ",":
                self.at += 1
            elif c != "}":
                raise self.error("expected , or } in a flow mapping")


def _flow_depth(text):
    """How many brackets `text` leaves open, quotes and comments considered."""
    depth = 0
    i = 0
    while i < len(text):
        c = text[i]
        if c in ("'", '"') and (i == 0 or text[i - 1] in " \t[{,:"):
            close = text.find(c, i + 1)
            while c == "'" and close != -1 and text.startswith("''", close):
                close = text.find(c, close + 2)
            if close == -1:
                return depth
            i = close + 1
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t"):
            break
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth


def _flow_comment(text):
    """A flow line with its comment removed, quotes considered."""
    i = 0
    while i < len(text):
        c = text[i]
        if c in ("'", '"') and (i == 0 or text[i - 1] in " \t[{,:"):
            close = text.find(c, i + 1)
            if close == -1:
                return text
            i = close + 1
            continue
        if c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


# -- block structure ----------------------------------------------------------


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _is_item(content):
    return content == "-" or content.startswith("- ") or content.startswith("-\t")


class _Reader:
    def __init__(self, text):
        self.lines = text.lstrip("﻿").splitlines()
        self.at = 0
        # One `---` may open the document; a second one starts another.
        for i, line in enumerate(self.lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("%"):
                raise YamlError(i + 1, "a directive, which this reader does not implement")
            if _plain(stripped) == "---":
                self.at = i + 1
            break

    def next(self):
        """The index of the next line holding something, without consuming it."""
        i = self.at
        while i < len(self.lines):
            line = self.lines[i]
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                if line[:len(line) - len(line.lstrip())].count("\t"):
                    raise YamlError(i + 1, "a tab in the indentation, which YAML forbids")
                if _indent(line) == 0 and _plain(stripped) in ("---", "..."):
                    raise YamlError(i + 1, "a second document, which this reader does not implement")
                return i
            i += 1
        return None

    def node(self, minimum):
        """Whatever starts on the next line, if it is indented at least `minimum`."""
        i = self.next()
        if i is None:
            return None
        line = self.lines[i]
        indent = _indent(line)
        if indent < minimum:
            return None
        content = line[indent:]
        if _is_item(content):
            return self.sequence(indent)
        if self.key(content, i) is not None:
            return self.mapping(indent)
        self.at = i + 1
        return self.value(content, minimum - 1, i)

    def key(self, content, i):
        """`key: rest` -> (key, rest); anything else -> None."""
        if content[:1] in ("'", '"'):
            try:
                key, end = _quoted(content, 0, i + 1)
            except YamlError:
                return None
            rest = content[end:].lstrip(" \t")
            if rest == ":" or rest.startswith((": ", ":\t")):
                return key, rest[1:]
            return None
        if content[:1] in ("[", "{", "|", ">", "#"):
            return None
        if content.startswith("? ") or content == "?":
            raise YamlError(i + 1, "a complex key (?), which this reader does not implement")
        end = _comment_at(content)
        for at in range(end):
            if content[at] == ":" and (at + 1 == len(content) or content[at + 1] in " \t"):
                key = content[:at].rstrip()
                _refuse_indicator(key, i + 1)
                if key == "<<":
                    raise YamlError(i + 1, "a merge key (<<), which this reader does not implement")
                return key, content[at + 1:]
        return None

    def mapping(self, indent):
        result = {}
        while True:
            i = self.next()
            if i is None:
                return result
            line = self.lines[i]
            here = _indent(line)
            if here < indent:
                return result
            if here > indent:
                raise YamlError(i + 1, "unexpected indentation")
            content = line[here:]
            if _is_item(content):
                raise YamlError(i + 1, "a sequence item where a key was expected")
            pair = self.key(content, i)
            if pair is None:
                raise YamlError(i + 1, "expected a key followed by a colon")
            key, rest = pair
            if key in result:
                raise YamlError(i + 1, "the key " + key + " appears twice")
            self.at = i + 1
            result[key] = self.after_key(rest, indent, i)

    def after_key(self, rest, indent, i):
        if _plain(rest.strip()) == "":
            j = self.next()
            if j is not None:
                below = self.lines[j]
                if _indent(below) == indent and _is_item(below[indent:]):
                    return self.sequence(indent)
            return self.node(indent + 1)
        return self.value(rest.strip(), indent, i)

    def sequence(self, indent):
        items = []
        while True:
            i = self.next()
            if i is None:
                return items
            line = self.lines[i]
            here = _indent(line)
            if here < indent:
                return items
            if here > indent:
                raise YamlError(i + 1, "unexpected indentation")
            content = line[here:]
            if not _is_item(content):
                return items
            after = content[1:]
            inner = after.lstrip(" \t")
            if _plain(inner) == "":
                self.at = i + 1
                items.append(self.node(indent + 1))
                continue
            column = here + 1 + len(after) - len(inner)
            if _is_item(inner) or self.key(inner, i) is not None:
                # The item's own content starts a block of its own, at its own
                # column: the line is rewritten as if it began there.
                self.lines[i] = " " * column + inner
                self.at = i
                items.append(self.sequence(column) if _is_item(inner) else self.mapping(column))
                continue
            self.at = i + 1
            items.append(self.value(inner, indent, i))

    def value(self, text, parent, i):
        """A value written after a key or a dash on line `i`, its node indented past `parent`."""
        if text[:1] in ("|", ">"):
            return self.block_scalar(_plain(text), parent, i)
        if text[:1] in ("[", "{"):
            return self.flow(text, i)
        if text[:1] in ("'", '"'):
            return self.quoted(text, i)
        _refuse_indicator(text, i + 1)
        if text.startswith("%"):
            raise YamlError(i + 1, "a scalar may not begin with %")
        return _resolve(self.continued(_plain(text), parent))

    def continued(self, first, parent):
        """A plain scalar, and the lines that continue it below, folded."""
        parts = [first]
        blank = 0
        j = self.at
        while j < len(self.lines):
            line = self.lines[j]
            stripped = line.strip()
            if not stripped:
                blank += 1
                j += 1
                continue
            if _indent(line) <= parent or stripped.startswith("#"):
                break
            content = line.strip()
            if _is_item(content) or self.key(content, j) is not None:
                break
            parts.append("\n" * blank if blank else " ")
            parts.append(_plain(content))
            blank = 0
            j += 1
            self.at = j
        return "".join(parts)

    def quoted(self, text, i):
        gathered = text
        j = i
        while True:
            try:
                value, end = _quoted(gathered, 0, i + 1)
                break
            except YamlError:
                j += 1
                if j >= len(self.lines):
                    raise
                gathered += "\n" + self.lines[j].strip()
        if _plain(gathered[end:].strip()):
            raise YamlError(j + 1, "text after a quoted scalar")
        self.at = j + 1
        return value

    def flow(self, text, i):
        gathered = _flow_comment(text)
        j = i
        while _flow_depth(gathered) > 0:
            j += 1
            if j >= len(self.lines):
                raise YamlError(i + 1, "a flow collection that is never closed")
            gathered += "\n" + _flow_comment(self.lines[j].strip())
        reader = _Flow(gathered, i + 1)
        value = reader.node()
        if reader.peek():
            raise YamlError(j + 1, "text after a flow collection")
        self.at = j + 1
        return value

    def block_scalar(self, header, parent, i):
        style, flags = header[0], header[1:]
        chomp = ""
        explicit = None
        for flag in flags:
            if flag in "+-" and not chomp:
                chomp = flag
            elif flag in "123456789" and explicit is None:
                explicit = int(flag)
            else:
                raise YamlError(i + 1, "a block scalar header YAML does not have: " + header)
        body = []
        indent = parent + explicit if explicit else None
        j = i + 1
        while j < len(self.lines):
            line = self.lines[j]
            if not line.strip():
                body.append(line[indent:] if indent is not None and len(line) > indent else "")
                j += 1
                continue
            here = _indent(line)
            if indent is None:
                if here <= parent:
                    break
                indent = here
            if here < indent:
                break
            body.append(line[indent:])
            j += 1
        self.at = j

        trailing = 0
        while body and not body[-1].strip():
            body.pop()
            trailing += 1
        if style == "|":
            text = "\n".join(body)
        else:
            text = _fold(body)
        if chomp == "-":
            return text
        if chomp == "+":
            return text + "\n" * ((1 if body else 0) + trailing)
        return text + "\n" if body else ""


def _fold(body):
    """A folded block scalar's lines: one break is a space, a blank line a break.

    A more-indented line keeps its breaks, as YAML has it.
    """
    lead = 0
    while lead < len(body) and not body[lead].strip():
        lead += 1
    if lead == len(body):
        return ""
    out = "\n" * lead + body[lead]
    previous = body[lead]
    k = lead + 1
    while k < len(body):
        blanks = 0
        while k < len(body) and not body[k].strip():
            blanks += 1
            k += 1
        line = body[k]
        indented = previous[:1] in (" ", "\t") or line[:1] in (" ", "\t")
        if blanks == 0:
            out += ("\n" if indented else " ") + line
        else:
            out += "\n" * (blanks + (1 if indented else 0)) + line
        previous = line
        k += 1
    return out
