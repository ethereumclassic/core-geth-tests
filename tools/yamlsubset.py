"""A reader for the part of YAML that the legacy suite's fillers use, and nothing else.

Python's standard library reads no YAML. This reader accepts what the legacy suite's YAML fillers
contain and refuses everything outside it, so a filler it cannot read is reported, never misread:

  - block mappings and block sequences, indented with spaces, with `#` comments;
  - plain, single-quoted and double-quoted scalars, which may continue over several lines, folded
    as YAML folds them: a line break becomes a space, and each empty line a newline;
  - in a double-quoted scalar, one escape: a backslash ending a line, which joins the next;
  - literal block scalars, `|` with no indicator;
  - flow mappings `{}` and flow sequences `[]`, which may span lines;
  - a mapping at the root, one `---` before it, and the tag `!!int` on a plain decimal scalar that
    has no leading zero and fits 32 bits.

Refused: every other escape, anchors and aliases, every other tag, folded and indicated block
scalars, a plain scalar spanning lines inside a flow collection, a plain key that reads as null,
tabs outside a literal block, a literal block whose leading empty line runs past its indentation,
NUL, carriage returns, a byte order mark, more than one document. The legacy suite is frozen, so
the subset never needs to grow, and refusing what it does not use is what keeps this reader from
reading anything differently from yaml-cpp, which read these fillers for retesteth.

`load` returns nodes that keep each scalar's style and tag, which retesteth's conversion needs (see
`retesteth.py`); `parse` also returns the set of FEATURES the text used.
"""

FEATURES = frozenset({
    "block mapping",
    "block sequence",
    "plain scalar",
    "plain scalar spanning lines",
    "single-quoted scalar",
    "double-quoted scalar",
    "quoted scalar spanning lines",
    "escaped line break",
    "literal block",
    "flow mapping",
    "flow sequence",
    "flow spanning lines",
    "document start",
    "tag !!int",
    "comment",
    "no final newline",
    "newline in a string",
    "tab in a string",
    "quote or backslash in a string",
    "non-ASCII in a string",
})

_FLOW_INDICATORS = ",[]{}"
_NULL_KEYS = {"~", "null", "Null", "NULL"}


def _is_decimal(text):
    """A decimal integer that yaml-cpp's 32-bit `as<int>` reads the same way: no leading zero."""
    digits = text[1:] if text.startswith("-") else text
    if digits == "" or not all(c in "0123456789" for c in digits) or (digits[0] == "0" and digits != "0"):
        return False
    return -2 ** 31 <= int(text) < 2 ** 31


class YAMLSubsetError(ValueError):
    """The text is outside the subset this reader accepts, or is not valid YAML."""

    def __init__(self, message, line=None):
        super().__init__(message if line is None else f"line {line + 1}: {message}")


class Scalar:
    __slots__ = ("value", "style", "tag")

    def __init__(self, value, style, tag=None):
        self.value, self.style, self.tag = value, style, tag


class Mapping:
    __slots__ = ("pairs",)

    def __init__(self, pairs):
        self.pairs = pairs  # [(key, node)], in order, a repeated key kept


class Sequence:
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = items


def parse(text):
    """Parse `text` (a str). Returns (root node, the set of FEATURES used)."""
    p = _Parser(text)
    return p.parse(), p.features


def load(text):
    """Parse `text` (a str) and return its root node."""
    return parse(text)[0]


class _Parser:
    def __init__(self, text):
        if text.startswith("﻿"):
            raise YAMLSubsetError("a byte order mark")
        if "\r" in text:
            raise YAMLSubsetError("a carriage return")
        if "\0" in text:
            raise YAMLSubsetError("a NUL character")
        self.features = set()
        self.lines = text.split("\n")
        if text.endswith("\n"):
            self.lines.pop()
        elif text:
            self.features.add("no final newline")
        self.final_newline = text.endswith("\n")
        self.i = 0

    # -- lines ---------------------------------------------------------------------------------

    def _indent(self, k):
        line = self.lines[k]
        n = len(line) - len(line.lstrip(" "))
        if line[n:n + 1] == "\t":
            raise YAMLSubsetError("a tab in the indentation", k)
        return n

    def _is_blank(self, k):
        stripped = self.lines[k].lstrip(" ")
        if stripped.startswith("#"):
            self.features.add("comment")
            return True
        if stripped != "" and stripped.strip(" \t") == "":
            raise YAMLSubsetError("a tab outside a literal block", k)
        return stripped == ""

    def _skip_blank(self):
        while self.i < len(self.lines) and self._is_blank(self.i):
            self.i += 1

    def _rest_is_blank(self, k, col):
        """Whether line k holds nothing but spaces or a comment from column `col` on."""
        line = self.lines[k]
        rest = line[col:]
        stripped = rest.lstrip(" ")
        if stripped == "":
            return True
        pos = col + len(rest) - len(stripped)
        if stripped.startswith("#") and (pos == 0 or line[pos - 1] == " "):
            self.features.add("comment")
            return True
        return False

    @staticmethod
    def _is_entry(content):
        return content == "-" or content.startswith("- ")

    # -- document ------------------------------------------------------------------------------

    def parse(self):
        self._skip_blank()
        if self.i < len(self.lines):
            line = self.lines[self.i]
            if line == "---" or line.startswith("--- "):
                if not self._rest_is_blank(self.i, 3):
                    raise YAMLSubsetError("content after ---", self.i)
                self.features.add("document start")
                self.i += 1
        node = self._block_node(-1)
        self._skip_blank()
        if self.i < len(self.lines):
            raise YAMLSubsetError("unexpected content, indentation or a second document", self.i)
        if not isinstance(node, Mapping):
            raise YAMLSubsetError("a root that is not a mapping")
        return node

    # -- block context -------------------------------------------------------------------------

    def _block_node(self, parent_indent):
        self._skip_blank()
        if self.i >= len(self.lines):
            return Scalar("", "plain")
        ind = self._indent(self.i)
        if ind <= parent_indent:
            return Scalar("", "plain")
        content = self.lines[self.i][ind:]
        if self._is_entry(content):
            return self._block_sequence(ind)
        if self._key(self.i, ind, probe=True) is not None:
            return self._block_mapping(ind)
        return self._value(self.i, ind, parent_indent)

    def _block_mapping(self, ind):
        self.features.add("block mapping")
        pairs = []
        while True:
            self._skip_blank()
            if self.i >= len(self.lines):
                break
            li = self._indent(self.i)
            if li < ind:
                break
            if li > ind:
                raise YAMLSubsetError("unexpected indentation", self.i)
            if self._is_entry(self.lines[self.i][li:]):
                raise YAMLSubsetError("a sequence entry where a mapping key was expected", self.i)
            key, col = self._key(self.i, li)
            pairs.append((key, self._after_key(self.i, col, ind)))
        return Mapping(pairs)

    def _block_sequence(self, ind):
        self.features.add("block sequence")
        items = []
        while True:
            self._skip_blank()
            if self.i >= len(self.lines):
                break
            li = self._indent(self.i)
            if li < ind:
                break
            if li > ind:
                raise YAMLSubsetError("unexpected indentation", self.i)
            content = self.lines[self.i][li:]
            if not self._is_entry(content):
                break
            rest = content[1:]
            inner = rest.lstrip(" ")
            col = li + 1 + (len(rest) - len(inner))
            if inner == "" or (inner.startswith("#") and self._rest_is_blank(self.i, li + 1)):
                self.i += 1
                items.append(self._block_node(ind))
            elif self._is_entry(inner) or self._key(self.i, col, probe=True) is not None:
                self.lines[self.i] = " " * col + inner  # the entry's content, at its own column
                items.append(self._block_node(ind))
            else:
                items.append(self._value(self.i, col, ind))
        return Sequence(items)

    def _key(self, k, col, probe=False):
        """A mapping key at (k, col): (key, column after its colon), or None when probing a non-key."""
        line = self.lines[k]
        text = line[col:]
        try:
            if text[:1] in ("'", '"'):
                key, end_k, end = self._quoted(k, col, multiline=False)
                j = end
                while j < len(line) and line[j] == " ":
                    j += 1
                if line[j:j + 1] != ":" or line[j + 1:j + 2] not in ("", " "):
                    raise YAMLSubsetError("a quoted scalar where a key was expected", k)
                return key, j + 1
            if text[:1] in ("-", "?", ":") and text[1:2] in ("", " "):
                raise YAMLSubsetError("an explicit key or an empty node", k)
            if text[:1] and text[:1] in "#&*!|>%@`,[]{}":
                raise YAMLSubsetError(f"a key beginning with {text[:1]!r}", k)
            j = 0
            while True:
                j = text.find(":", j)
                if j < 0:
                    raise YAMLSubsetError("no mapping key", k)
                if text[j + 1:j + 2] in ("", " "):
                    break
                j += 1
            key = text[:j]
            if " #" in key or "\t" in key:
                raise YAMLSubsetError("a comment or tab inside a key", k)
            key = key.rstrip(" ")
            if key == "":
                raise YAMLSubsetError("an empty key", k)
            if key in _NULL_KEYS:
                raise YAMLSubsetError(f"the plain key {key!r}, which yaml-cpp reads as null", k)
            return key, col + j + 1
        except YAMLSubsetError:
            if probe:
                return None
            raise

    def _after_key(self, k, col, ind):
        if self._rest_is_blank(k, col):
            self.i = k + 1
            self._skip_blank()
            if self.i < len(self.lines):
                ni = self._indent(self.i)
                if ni > ind:
                    return self._block_node(ind)
                if ni == ind and self._is_entry(self.lines[self.i][ni:]):
                    return self._block_sequence(ind)
            return Scalar("", "plain")
        line = self.lines[k]
        if line[col:col + 1] != " ":
            raise YAMLSubsetError("no space after a key's colon", k)
        while line[col:col + 1] == " ":
            col += 1
        return self._value(k, col, ind)

    def _value(self, k, col, owner_indent):
        """A node written inline at (k, col), owned by a key or entry indented `owner_indent`."""
        line = self.lines[k]
        text = line[col:]
        first = text[:1]
        if text.startswith("!"):
            tag = text.split(" ", 1)[0]
            if tag != "!!int":
                raise YAMLSubsetError(f"the tag {tag}", k)
            self.features.add("tag !!int")
            j = col + len(tag)
            while line[j:j + 1] == " ":
                j += 1
            node = self._plain_block(k, j, owner_indent)
            if not _is_decimal(node.value):
                raise YAMLSubsetError(f"!!int on {node.value!r}, not a decimal integer", k)
            node.tag = "!!int"
            return node
        if first == "|":
            header = text[1:]
            if header.strip(" ") != "" and not (header.startswith(" ") and header.lstrip(" ").startswith("#")):
                raise YAMLSubsetError("a block scalar indicator", k)
            return self._literal(k, owner_indent)
        if first == ">":
            raise YAMLSubsetError("a folded block scalar", k)
        if first in ("[", "{"):
            return self._flow(k, col)
        if first in ("'", '"'):
            value, end_k, end = self._quoted(k, col)
            if not self._rest_is_blank(end_k, end):
                raise YAMLSubsetError("content after a quoted scalar", end_k)
            self.i = end_k + 1
            return self._scalar(value, "single" if first == "'" else "double")
        if first in ("&", "*"):
            raise YAMLSubsetError("an anchor or alias", k)
        return self._plain_block(k, col, owner_indent)

    def _plain_line(self, k, text):
        """One line's piece of a plain scalar: (text, whether a comment ended it)."""
        cut = text.find(" #")
        if cut >= 0:
            self.features.add("comment")
            text = text[:cut]
        piece = text.rstrip(" ")
        if "\t" in piece:
            raise YAMLSubsetError("a tab outside a literal block", k)
        if ": " in piece or piece.endswith(":"):
            raise YAMLSubsetError("a colon and space inside a plain scalar", k)
        return piece, cut >= 0

    def _plain_block(self, k, col, owner_indent):
        """A plain scalar from (k, col); later lines indented past `owner_indent` continue it."""
        text = self.lines[k][col:]
        if text[:1] and text[:1] in "#&*!|>%@`,[]{}'\"":
            raise YAMLSubsetError(f"a plain scalar beginning with {text[:1]!r}", k)
        if text[:1] in ("-", "?", ":") and text[1:2] in ("", " "):
            raise YAMLSubsetError("a plain scalar that is an indicator", k)
        piece, ended = self._plain_line(k, text)
        parts = [piece]
        j = k + 1
        while not ended:
            m, breaks = j, 0
            while m < len(self.lines) and self.lines[m].strip(" ") == "":
                m, breaks = m + 1, breaks + 1
            if m >= len(self.lines) or self._indent(m) <= owner_indent:
                break
            rest = self.lines[m][self._indent(m):]
            if rest.startswith("#"):
                break
            piece, ended = self._plain_line(m, rest)
            parts.append("\n" * breaks if breaks else " ")
            parts.append(piece)
            self.features.add("plain scalar spanning lines")
            j = m + 1
        self.i = j
        return self._scalar("".join(parts), "plain")

    def _literal(self, k, owner_indent):
        lines = self.lines
        j = k + 1
        first = j
        while first < len(lines) and lines[first].strip(" ") == "":
            first += 1
        if first >= len(lines):
            self.i = j
            return self._scalar("", "literal")
        content_indent = len(lines[first]) - len(lines[first].lstrip(" "))
        if lines[first][content_indent:content_indent + 1] == "\t":
            raise YAMLSubsetError("a tab in a literal block's indentation", first)
        if any(len(lines[x]) > content_indent for x in range(j, first)):
            raise YAMLSubsetError("a leading empty line longer than the literal block's indentation", j)
        if content_indent <= owner_indent:
            self.i = j
            return self._scalar("", "literal")
        out = []
        last = None
        while j < len(lines):
            line = lines[j]
            if line.strip(" ") == "":
                out.append(line[content_indent:])  # spaces past the indentation are content
                if out[-1]:
                    last = j
                j += 1
                continue
            if len(line) - len(line.lstrip(" ")) < content_indent:
                break
            out.append(line[content_indent:])
            last = j
            j += 1
        out = out[:last - k]  # clip: trailing empty lines are not content
        value = "\n".join(out)
        if self.final_newline or last != len(lines) - 1:
            value += "\n"
        self.features.add("literal block")
        self.i = j
        return self._scalar(value, "literal")

    # -- scalars -------------------------------------------------------------------------------

    def _fold(self, k):
        """After a line break inside a quoted scalar: skip empty lines and the next line's leading
        white space. Returns (that line, its first column, the number of empty lines)."""
        k += 1
        breaks = 0
        while k < len(self.lines) and self.lines[k].strip(" \t") == "":
            k, breaks = k + 1, breaks + 1
        if k >= len(self.lines):
            raise YAMLSubsetError("an unclosed quoted scalar", k - 1)
        line = self.lines[k]
        if line.startswith("---") or line.startswith("..."):
            raise YAMLSubsetError("a document marker inside a quoted scalar", k)
        return k, len(line) - len(line.lstrip(" \t")), breaks

    def _quoted(self, k, col, multiline=True):
        """A quoted scalar starting at (k, col): (value, its last line, the column after it)."""
        line = self.lines[k]
        quote = line[col]
        out = []
        spaces = ""  # white space written in the text, dropped if a line break follows it
        j = col + 1
        while True:
            if j >= len(line):
                if not multiline:
                    raise YAMLSubsetError("a quoted scalar that spans lines", k)
                k, j, breaks = self._fold(k)
                line = self.lines[k]
                spaces = ""
                out.append("\n" * breaks if breaks else " ")
                self.features.add("quoted scalar spanning lines")
                continue
            c = line[j]
            if c in " \t":
                spaces += c
                j += 1
                continue
            out.append(spaces)
            spaces = ""
            if quote == "'":
                if c == "'":
                    if line[j + 1:j + 2] == "'":
                        out.append("'")
                        j += 2
                        continue
                    break
                out.append(c)
                j += 1
                continue
            if c == '"':
                break
            if c != "\\":
                out.append(c)
                j += 1
                continue
            if line[j + 1:j + 2] != "":
                raise YAMLSubsetError(f"the escape \\{line[j + 1:j + 2]}, which no filler uses", k)
            if not multiline:
                raise YAMLSubsetError("an escaped line break", k)
            k, j, breaks = self._fold(k)
            line = self.lines[k]
            out.append("\n" * breaks)
            self.features.add("escaped line break")
        out.append(spaces)
        self.features.add("single-quoted scalar" if quote == "'" else "double-quoted scalar")
        return "".join(out), k, j + 1

    def _scalar(self, value, style):
        if style == "plain":
            self.features.add("plain scalar")
        if "\n" in value:
            self.features.add("newline in a string")
        if "\t" in value:
            self.features.add("tab in a string")
        if '"' in value or "\\" in value:
            self.features.add("quote or backslash in a string")
        if any(ord(c) > 127 for c in value):
            self.features.add("non-ASCII in a string")
        return Scalar(value, style)

    # -- flow context --------------------------------------------------------------------------

    def _flow(self, k, col):
        cur = _FlowCursor(self, k, col)
        node = cur.node()
        if cur.k != k:
            self.features.add("flow spanning lines")
        if not self._rest_is_blank(cur.k, cur.col):
            raise YAMLSubsetError("content after a flow collection", cur.k)
        self.i = cur.k + 1
        return node


class _FlowCursor:
    """Reads one flow collection, which may continue over several lines."""

    def __init__(self, parser, k, col):
        self.p, self.k, self.col = parser, k, col

    def _peek(self):
        line = self.p.lines[self.k]
        return line[self.col] if self.col < len(line) else "\n"

    def _skip(self):
        """Skip spaces, line ends and comments between flow tokens."""
        while True:
            if self.k >= len(self.p.lines):
                raise YAMLSubsetError("an unclosed flow collection")
            line = self.p.lines[self.k]
            while self.col < len(line) and line[self.col] == " ":
                self.col += 1
            if self.col < len(line) and line[self.col] == "\t":
                raise YAMLSubsetError("a tab inside a flow collection", self.k)
            if self.col < len(line) and line[self.col] == "#":
                if self.col > 0 and line[self.col - 1] != " ":
                    raise YAMLSubsetError("a # not after a space", self.k)
                self.p.features.add("comment")
                self.col = len(line)
            if self.col < len(line):
                return
            self.k += 1
            self.col = 0

    def node(self):
        self._skip()
        c = self._peek()
        if c == "[":
            return self._sequence()
        if c == "{":
            return self._mapping()
        if c in ("'", '"'):
            value, self.k, self.col = self.p._quoted(self.k, self.col)
            return self.p._scalar(value, "single" if c == "'" else "double")
        line = self.p.lines[self.k]
        if line.startswith("!", self.col):
            tag = line[self.col:].split(" ", 1)[0]
            if tag != "!!int":
                raise YAMLSubsetError(f"the tag {tag}", self.k)
            self.p.features.add("tag !!int")
            self.col += len(tag)
            self._skip()
            node = self._plain()
            if not _is_decimal(node.value):
                raise YAMLSubsetError(f"!!int on {node.value!r}, not a decimal integer", self.k)
            node.tag = "!!int"
            return node
        if c in "&*|>%@`#,]}":
            raise YAMLSubsetError(f"{c!r} where a flow node was expected", self.k)
        return self._plain()

    def _plain(self):
        line = self.p.lines[self.k]
        start = j = self.col
        while j < len(line):
            c = line[j]
            if c in _FLOW_INDICATORS:
                break
            if c == ":" and (j + 1 >= len(line) or line[j + 1] in " " + _FLOW_INDICATORS):
                break
            if c == "#" and j > start and line[j - 1] == " ":
                break
            if c == "\t":
                raise YAMLSubsetError("a tab inside a flow collection", self.k)
            j += 1
        value = line[start:j].rstrip(" ")
        if value == "":
            raise YAMLSubsetError("an empty flow node", self.k)
        self.col = start + len(value)
        if self._continues(j):
            raise YAMLSubsetError("a plain scalar that spans lines inside a flow collection", self.k)
        return self.p._scalar(value, "plain")

    def _continues(self, j):
        """Whether a plain scalar that stopped at column j would go on: more text on its line, or a
        later line that does not begin with an indicator (a multi-line plain scalar)."""
        rest = self.p.lines[self.k][j:].lstrip(" ")
        if rest:
            return rest[0] not in _FLOW_INDICATORS + ":#"
        k = self.k + 1
        while k < len(self.p.lines):
            stripped = self.p.lines[k].lstrip(" ")
            if stripped == "" or stripped.startswith("#"):
                k += 1
                continue
            return stripped[0] not in _FLOW_INDICATORS + ":"
        return False

    def _sequence(self):
        self.p.features.add("flow sequence")
        self.col += 1
        items = []
        while True:
            self._skip()
            if self._peek() == "]":
                self.col += 1
                return Sequence(items)
            items.append(self.node())
            self._skip()
            c = self._peek()
            if c == ",":
                self.col += 1
            elif c != "]":
                raise YAMLSubsetError(f"{c!r} inside a flow sequence", self.k)

    def _mapping(self):
        self.p.features.add("flow mapping")
        self.col += 1
        pairs = []
        while True:
            self._skip()
            if self._peek() == "}":
                self.col += 1
                return Mapping(pairs)
            if self._peek() in ("'", '"'):
                key, _, self.col = self.p._quoted(self.k, self.col, multiline=False)
            else:
                key = self._plain().value
                if key in _NULL_KEYS:
                    raise YAMLSubsetError(f"the plain key {key!r}, which yaml-cpp reads as null", self.k)
            self._skip()
            if self._peek() != ":":
                raise YAMLSubsetError("a flow mapping entry without a value", self.k)
            self.col += 1
            pairs.append((key, self.node()))
            self._skip()
            c = self._peek()
            if c == ",":
                self.col += 1
            elif c != "}":
                raise YAMLSubsetError(f"{c!r} inside a flow mapping", self.k)
