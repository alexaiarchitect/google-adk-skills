"""A small YAML reader for the documents this plugin has to read, stdlib only.

Two kinds of file are YAML and matter here: the frontmatter of an Agent Skill's
``SKILL.md`` (which ADK parses with ``yaml.safe_load``) and an Agent-Plugins
``EVAL.yaml``. Both use the block subset of YAML - nested mappings, ``-``
sequences, plain / quoted scalars, ``|`` and ``>`` block scalars, comments and
the odd one-line ``[a, b]`` or ``{k: v}`` - so that is what this reads, with the
same scalar resolution ``safe_load`` applies (``true``/``yes``/``on`` are
booleans, ``~`` and ``null`` are None, numbers are numbers).

Anchors, aliases, tags, multi-document streams and complex keys are not
supported; they raise :class:`YamlError` or come back as plain strings rather
than being guessed at. Pulling in PyYAML instead would break the plugin's rule
that every script runs in a bare interpreter.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["YamlError", "loads"]


class YamlError(ValueError):
    """The text is not YAML this reader understands."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


_NULLS = {"", "~", "null", "Null", "NULL"}
_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON", "y", "Y"}
_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF", "n", "N"}
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$")
_HEX = re.compile(r"^0x[0-9a-fA-F_]+$")
_OCT = re.compile(r"^0o[0-7_]+$")
_FLOAT = re.compile(r"^[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*(?:[eE][-+]?[0-9]+)?$|^[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+$")


def _resolve_plain(text: str) -> Any:
    """`safe_load`'s implicit typing for an unquoted scalar."""
    value = text.strip()
    if value in _NULLS:
        return None
    if value in _TRUE and value not in ("y", "Y"):
        return True
    if value in _FALSE and value not in ("n", "N"):
        return False
    if _INT.match(value):
        return int(value.replace("_", ""))
    if _HEX.match(value):
        return int(value.replace("_", ""), 16)
    if _OCT.match(value):
        return int(value[2:].replace("_", ""), 8)
    if _FLOAT.match(value) and any(ch.isdigit() for ch in value):
        return float(value.replace("_", ""))
    if value.lower() in (".inf", "+.inf"):
        return float("inf")
    if value.lower() == "-.inf":
        return float("-inf")
    if value.lower() == ".nan":
        return float("nan")
    return value


_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
    "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
    "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
}


def _double_quoted(body: str, line: int) -> str:
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise YamlError("dangling backslash in a double-quoted scalar", line)
        code = body[index]
        if code in _ESCAPES:
            out.append(_ESCAPES[code])
            index += 1
        elif code in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[code]
            digits = body[index + 1:index + 1 + width]
            try:
                out.append(chr(int(digits, 16)))
            except ValueError as exc:
                raise YamlError(f"bad \\{code} escape", line) from exc
            index += 1 + width
        else:
            raise YamlError(f"unknown escape \\{code}", line)
    return "".join(out)


def _fold_quoted(raw: str) -> str:
    """Line folding inside a multi-line quoted scalar: a single break becomes a
    space, an empty line becomes a newline."""
    if "\n" not in raw:
        return raw
    pieces = raw.split("\n")
    out = pieces[0].rstrip()
    blank = 0
    for piece in pieces[1:]:
        stripped = piece.strip()
        if not stripped:
            blank += 1
            continue
        out += ("\n" * blank) if blank else " "
        out += stripped
        blank = 0
    return out


def _find_closing_quote(text: str, start: int, quote: str) -> int:
    """Index of the quote closing the scalar opened at `start`, or -1."""
    index = start + 1
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            return index
        index += 1
    return -1


def _strip_comment(text: str) -> str:
    """Drop a trailing ` # comment` from a plain value."""
    if text.startswith("#"):
        return ""
    match = re.search(r"\s#", text)
    return text[:match.start()] if match else text


def _split_key(content: str) -> tuple[str, str] | None:
    """(key, rest) when `content` is `key: value`, else None."""
    if not content:
        return None
    first = content[0]
    if first in "[{|>!&*%@`#?":
        return None
    if first == "-" and (len(content) == 1 or content[1] in " \t"):
        return None
    if content[0] in "\"'":
        end = _find_closing_quote(content, 0, content[0])
        if end < 0:
            return None
        after = content[end + 1:]
        match = re.match(r"\s*:(?:\s|$)", after)
        if not match:
            return None
        raw = content[1:end]
        key = _double_quoted(raw, 0) if content[0] == '"' else raw.replace("''", "'")
        return key, after[match.end():].strip()
    for match in re.finditer(r":(?:\s|$)", content):
        key = content[:match.start()]
        if re.search(r"\s#", key) or key.startswith("#"):
            return None
        if key.strip():
            return key.strip(), content[match.end():].strip()
    return None


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # -- line helpers ----------------------------------------------------
    @staticmethod
    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def is_skippable(self, line: str) -> bool:
        stripped = line.strip()
        return not stripped or stripped.startswith("#")

    def next_content(self, index: int) -> int:
        while index < len(self.lines) and self.is_skippable(self.lines[index]):
            index += 1
        return index

    # -- document --------------------------------------------------------
    def parse(self) -> Any:
        index = self.next_content(0)
        if index < len(self.lines) and self.lines[index].rstrip() == "---":
            index = self.next_content(index + 1)
        if index >= len(self.lines):
            return None
        value, index = self.node(index, self.indent_of(self.lines[index]))
        index = self.next_content(index)
        if index < len(self.lines):
            tail = self.lines[index].strip()
            if tail not in ("---", "..."):
                raise YamlError(f"unexpected content {tail[:40]!r}", index + 1)
            if tail == "---" and self.next_content(index + 1) < len(self.lines):
                raise YamlError("multi-document streams are not supported", index + 1)
        return value

    def node(self, index: int, indent: int) -> tuple[Any, int]:
        line = self.lines[index]
        content = line.strip()
        if content == "-" or content.startswith("- ") or content.startswith("-\t"):
            return self.sequence(index, self.indent_of(line))
        if _split_key(content) is not None:
            return self.mapping(index, self.indent_of(line))
        return self.inline_value(content, index, indent - 1)

    # -- collections -----------------------------------------------------
    def mapping(self, index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while True:
            index = self.next_content(index)
            if index >= len(self.lines):
                break
            line = self.lines[index]
            own = self.indent_of(line)
            if own < indent:
                break
            content = line.strip()
            if content in ("---", "..."):
                break
            if own > indent:
                raise YamlError(f"unexpected indentation before {content[:40]!r}", index + 1)
            split = _split_key(content)
            if split is None:
                if content.startswith("- "):
                    break
                raise YamlError(f"expected `key: value`, found {content[:40]!r}", index + 1)
            key, rest = split
            value, index = self.value_after_key(rest, index, indent)
            result[key] = value
        return result, index

    def sequence(self, index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while True:
            index = self.next_content(index)
            if index >= len(self.lines):
                break
            line = self.lines[index]
            own = self.indent_of(line)
            content = line.strip()
            if own < indent or not (content == "-" or content.startswith(("- ", "-\t"))):
                if own > indent:
                    raise YamlError(f"unexpected indentation before {content[:40]!r}", index + 1)
                break
            if own > indent:
                raise YamlError("inconsistent sequence indentation", index + 1)
            rest = content[1:]
            offset = len(rest) - len(rest.lstrip())
            rest = rest.strip()
            if not rest or rest.startswith("#"):
                child = self.next_content(index + 1)
                if child < len(self.lines) and self.indent_of(self.lines[child]) > indent:
                    value, index = self.node(child, self.indent_of(self.lines[child]))
                else:
                    value, index = None, index + 1
                result.append(value)
                continue
            # `- key: value` opens a mapping whose keys line up after the dash:
            # rewrite the dash as spaces and read it as a node at that column.
            column = own + 1 + offset
            self.lines[index] = " " * column + rest
            if rest.startswith(("- ", "-\t")) or rest == "-" or _split_key(rest) is not None:
                value, index = self.node(index, column)
            else:
                value, index = self.inline_value(rest, index, indent)
            result.append(value)
        return result, index

    def value_after_key(self, rest: str, index: int, indent: int) -> tuple[Any, int]:
        if not rest or rest.startswith("#"):
            child = self.next_content(index + 1)
            if child < len(self.lines):
                line = self.lines[child]
                own = self.indent_of(line)
                content = line.strip()
                if own > indent:
                    return self.node(child, own)
                if own == indent and (content == "-" or content.startswith(("- ", "-\t"))):
                    return self.sequence(child, own)
            return None, index + 1
        return self.inline_value(rest, index, indent)

    # -- scalars ---------------------------------------------------------
    def inline_value(self, rest: str, index: int, parent_indent: int) -> tuple[Any, int]:
        """A value that starts on line `index` after a key or a dash."""
        if rest[0] in "|>":
            return self.block_scalar(rest, index, parent_indent)
        if rest[0] in "\"'":
            return self.quoted(rest, index, parent_indent)
        if rest[0] in "[{":
            text, end = self.gather_flow(rest, index)
            value, position = _Flow(text, index + 1).parse_value(0)
            trailing = _strip_comment(text[position:]).strip()
            if trailing:
                raise YamlError(f"unexpected text after a flow collection: {trailing[:30]!r}",
                                index + 1)
            return value, end
        if rest[0] in "&*!":
            if rest[0] == "*" or rest[0] == "&":
                raise YamlError("anchors and aliases are not supported", index + 1)
        plain = _strip_comment(rest).rstrip()
        index += 1
        # A plain scalar may continue on more-indented lines.
        pieces = [plain]
        blank = 0
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip():
                blank += 1
                index += 1
                continue
            if self.indent_of(line) <= parent_indent or line.strip().startswith("#"):
                break
            content = line.strip()
            if _split_key(content) is not None or content.startswith("- "):
                break
            pieces.append(("\n" * blank) if blank else " ")
            pieces.append(_strip_comment(content).rstrip())
            blank = 0
            index += 1
        if len(pieces) == 1:
            return _resolve_plain(plain), index
        return "".join(pieces), index

    def quoted(self, rest: str, index: int, parent_indent: int) -> tuple[Any, int]:
        quote = rest[0]
        text = rest
        start = index
        end = _find_closing_quote(text, 0, quote)
        while end < 0:
            index += 1
            if index >= len(self.lines):
                raise YamlError("unterminated quoted scalar", start + 1)
            text += "\n" + self.lines[index]
            end = _find_closing_quote(text, 0, quote)
        raw = _fold_quoted(text[1:end])
        trailing = _strip_comment(text[end + 1:]).strip()
        if trailing:
            raise YamlError(f"unexpected text after a quoted scalar: {trailing[:30]!r}", index + 1)
        value = _double_quoted(raw, start + 1) if quote == '"' else raw.replace("''", "'")
        return value, index + 1

    def block_scalar(self, header: str, index: int, parent_indent: int) -> tuple[str, int]:
        match = re.match(r"^([|>])([-+]?)([1-9]?)([-+]?)\s*(?:#.*)?$", header)
        if not match:
            raise YamlError(f"bad block scalar header {header!r}", index + 1)
        style = match.group(1)
        chomp = match.group(2) or match.group(4)
        explicit = int(match.group(3)) if match.group(3) else None
        index += 1
        block_indent = parent_indent + explicit if explicit else None
        body: list[str] = []
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip():
                body.append("")
                index += 1
                continue
            own = self.indent_of(line)
            if block_indent is None:
                if own <= parent_indent:
                    break
                block_indent = own
            if own < block_indent:
                break
            body.append(line[block_indent:])
            index += 1
        # Trailing blank lines belong to the chomping decision, not the text.
        trailing = 0
        while body and body[-1] == "":
            body.pop()
            trailing += 1
        if style == "|":
            text = "\n".join(body)
        else:
            pieces: list[str] = []
            for position, line in enumerate(body):
                previous = body[position - 1] if position else None
                if previous is None:
                    pieces.append(line)
                elif line == "":
                    pieces.append("\n")
                elif previous == "":
                    pieces.append(line)
                elif line.startswith((" ", "\t")) or previous.startswith((" ", "\t")):
                    pieces.append("\n" + line)
                else:
                    pieces.append(" " + line)
            text = "".join(pieces)
        if not body:
            return ("\n" * trailing if chomp == "+" else ""), index
        if chomp == "-":
            return text, index
        if chomp == "+":
            return text + "\n" + "\n" * trailing, index
        return text + "\n", index

    def gather_flow(self, rest: str, index: int) -> tuple[str, int]:
        text = rest
        start = index
        while not _balanced(text):
            index += 1
            if index >= len(self.lines):
                raise YamlError("unterminated flow collection", start + 1)
            text += " " + self.lines[index].strip()
        return text, index + 1


def _balanced(text: str) -> bool:
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return True
        index += 1
    return depth == 0 and quote is None


class _Flow:
    """`[a, "b", {k: v}]` and `{a: 1, b: [x]}` on one logical line."""

    def __init__(self, text: str, line: int) -> None:
        self.text = text
        self.line = line

    def skip(self, position: int) -> int:
        while position < len(self.text) and self.text[position] in " \t":
            position += 1
        return position

    def parse_value(self, position: int) -> tuple[Any, int]:
        position = self.skip(position)
        if position >= len(self.text):
            raise YamlError("flow collection ended early", self.line)
        char = self.text[position]
        if char == "[":
            return self.parse_sequence(position + 1)
        if char == "{":
            return self.parse_mapping(position + 1)
        if char in "\"'":
            end = _find_closing_quote(self.text, position, char)
            if end < 0:
                raise YamlError("unterminated quoted scalar", self.line)
            raw = self.text[position + 1:end]
            value = _double_quoted(raw, self.line) if char == '"' else raw.replace("''", "'")
            return value, end + 1
        end = position
        while end < len(self.text) and self.text[end] not in ",]}":
            if self.text[end] == ":" and (end + 1 >= len(self.text) or self.text[end + 1] in " ,]}"):
                break
            end += 1
        return _resolve_plain(self.text[position:end]), end

    def parse_sequence(self, position: int) -> tuple[list[Any], int]:
        items: list[Any] = []
        while True:
            position = self.skip(position)
            if position < len(self.text) and self.text[position] == "]":
                return items, position + 1
            value, position = self.parse_value(position)
            items.append(value)
            position = self.skip(position)
            if position < len(self.text) and self.text[position] == ",":
                position += 1
                continue
            if position < len(self.text) and self.text[position] == "]":
                return items, position + 1
            raise YamlError("expected `,` or `]` in a flow sequence", self.line)

    def parse_mapping(self, position: int) -> tuple[dict[str, Any], int]:
        items: dict[str, Any] = {}
        while True:
            position = self.skip(position)
            if position < len(self.text) and self.text[position] == "}":
                return items, position + 1
            key, position = self.parse_value(position)
            position = self.skip(position)
            value: Any = None
            if position < len(self.text) and self.text[position] == ":":
                value, position = self.parse_value(position + 1)
            items[str(key) if not isinstance(key, str) else key] = value
            position = self.skip(position)
            if position < len(self.text) and self.text[position] == ",":
                position += 1
                continue
            if position < len(self.text) and self.text[position] == "}":
                return items, position + 1
            raise YamlError("expected `,` or `}` in a flow mapping", self.line)


def loads(text: str) -> Any:
    """Parse one YAML document. Raises :class:`YamlError` on what it cannot read."""
    if "\t" in "".join(line[: len(line) - len(line.lstrip())] for line in text.splitlines()):
        for number, line in enumerate(text.splitlines(), start=1):
            leading = line[: len(line) - len(line.lstrip())]
            if "\t" in leading and line.strip():
                raise YamlError("tabs are not allowed for indentation", number)
    return _Parser(text).parse()
