#   MIT License
#
#   Copyright (c) 2026 Toppi
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.

import re
from typing import Any

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z_][\w.]*$")
_FLAGGED_STR_RE = re.compile(r'^[\w+]+:".*"$', re.S)

_ESCAPE_DECODE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "0": "\0"}


def _escape_string(value: str) -> str:
    return (value.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


def _format_key(key: str) -> str:
    return key if _BARE_KEY_RE.match(key) else f'"{_escape_string(key)}"'


def _format_float(value: float) -> str:
    if value != value:
        return "0"
    text = repr(value)
    if "e" in text or "E" in text or "inf" in text:
        text = f"{value:.10f}".rstrip("0").rstrip(".") or "0"
    return text


def _format_value(value: Any, indent: int = 0) -> str:
    if value is None:
        return "null"
    if isinstance(value, KVArray):
        return value._format_multiline(indent)
    if isinstance(value, KVValue):
        return str(value)
    if isinstance(value, KVNode):
        return value._serialize(indent=indent)
    if isinstance(value, str):
        if _FLAGGED_STR_RE.match(value):
            return value
        return f'"{_escape_string(value)}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        if not value:
            return "{}"
        tab = "\t" * indent
        items = "".join(f"{tab}\t{_format_key(k)} = {_format_value(v, indent + 1)}\n"
                        for k, v in value.items())
        return f"{{\n{items}{tab}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if len(value) <= 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return f"[ {', '.join(_format_value(v) for v in value)} ]"
        tab = "\t" * indent
        items = ",\n".join(f"{tab}\t{_format_value(v, indent + 1)}" for v in value)
        return f"[\n{items},\n{tab}]"
    if isinstance(value, float):
        return _format_float(value)
    return str(value)


class KVValue:
    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self})"


class KVVector2(KVValue):
    def __init__(self, x, y):
        self.x, self.y = x, y

    def __str__(self):
        return f"[ {self.x}, {self.y} ]"


class KVVector3(KVValue):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __str__(self):
        return f"[ {self.x}, {self.y}, {self.z} ]"


class KVVector4(KVValue):
    def __init__(self, x, y, z, w):
        self.x, self.y, self.z, self.w = x, y, z, w

    def __str__(self):
        return f"[ {self.x}, {self.y}, {self.z}, {self.w} ]"


class KVBool(KVValue):
    def __init__(self, value: bool):
        self.value = bool(value)

    def __str__(self):
        return "true" if self.value else "false"


class KVFlaggedString(KVValue):
    """Flagged string value, e.g. resource:"models/foo.vmdl"."""

    def __init__(self, flag: str, value: str):
        self.flag = flag
        self.value = value

    def __str__(self):
        return f'{self.flag}:"{_escape_string(self.value)}"'


class KVArray(KVValue):
    def __init__(self, *values):
        self.values = list(values)

    def __str__(self):
        return self._format_multiline(0)

    def _format_multiline(self, indent: int) -> str:
        if not self.values:
            return "[]"
        tab = "\t" * (indent + 1)
        close_tab = "\t" * indent
        items = ",\n".join(f"{tab}{_format_value(v, indent + 1)}" for v in self.values)
        return f"[\n{items},\n{close_tab}]"


class KVHeader:
    DEFAULT_ENCODING_GUID = "{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d}"
    MODEL_DOC_GUID = "{fb63b6ca-f435-4aa0-a2c7-c66ddc651dca}"

    def __init__(self, encoding="text", encoding_version=None,
                 format="modeldoc28", format_version=None):
        self.version = "kv3"
        self.encoding = encoding or "text"
        self.encoding_version = encoding_version or self.DEFAULT_ENCODING_GUID
        self.format = format or "modeldoc28"
        self.format_version = format_version or self.MODEL_DOC_GUID

    def __str__(self):
        return (
            f"<!-- {self.version} encoding:{self.encoding}:version{self.encoding_version}"
            f" format:{self.format}:version{self.format_version} -->"
        )

    def __repr__(self):
        return f"KVHeader(encoding={self.encoding!r}, format={self.format!r})"


class KVNode:
    def __init__(self, **kwargs):
        self.children: list["KVNode"] = []
        self.properties: dict[str, Any] = kwargs

    def add_child(self, child: "KVNode"):
        self.children.append(child)

    def remove_child(self, child: "KVNode") -> bool:
        try:
            self.children.remove(child)
            return True
        except ValueError:
            return False

    def get(self, recursive: bool = False, **conditions) -> "KVNode | None":
        for child in self.children:
            if not isinstance(child, KVNode):
                continue
            if all(child.properties.get(k) == v for k, v in conditions.items()):
                return child
            if recursive:
                result = child.get(recursive=True, **conditions)
                if result is not None:
                    return result
        return None

    def find_all(self, recursive: bool = False, **conditions) -> list["KVNode"]:
        results = []
        for child in self.children:
            if not isinstance(child, KVNode):
                continue
            if all(child.properties.get(k) == v for k, v in conditions.items()):
                results.append(child)
            if recursive:
                results.extend(child.find_all(recursive=True, **conditions))
        return results

    def _serialize(self, indent: int = 0) -> str:
        tab = "\t" * indent
        out = f"{tab}{{\n"

        for key, value in self.properties.items():
            key = _format_key(key)
            if isinstance(value, (KVNode, dict)):
                block = value._serialize(indent + 1) if isinstance(value, KVNode) \
                    else f"{tab}\t{_format_value(value, indent + 1)}"
                out += f"{tab}\t{key} =\n{block}\n"
            else:
                out += f"{tab}\t{key} = {_format_value(value, indent + 1)}\n"

        if self.children:
            out += f"{tab}\tchildren =\n{tab}\t[\n"
            for child in self.children:
                if isinstance(child, KVNode):
                    out += f"{child._serialize(indent + 2)},\n"
                else:
                    out += f"{tab}\t\t{_format_value(child, indent + 2)},\n"
            out += f"{tab}\t]\n"

        out += f"{tab}}}"
        return out

    def __repr__(self):
        props = ", ".join(f"{k}={v!r}" for k, v in self.properties.items())
        return f"KVNode({props}, children={len(self.children)})"


class KVDocument:
    def __init__(self, format="modeldoc28", format_version=None,
                 encoding="text", encoding_version=None):
        self.header = KVHeader(
            encoding=encoding, encoding_version=encoding_version,
            format=format, format_version=format_version,
        )
        self.roots: dict[str, KVNode] = {}

    @classmethod
    def from_text(cls, text: str) -> "KVDocument":
        return KVParser(text).parse()

    def add_root(self, key: str, node: KVNode):
        self.roots[key] = node

    def remove_root(self, key: str) -> bool:
        return self.roots.pop(key, None) is not None

    def to_text(self) -> str:
        out = str(self.header) + "\n{\n"
        for key, node in self.roots.items():
            out += f"\t{_format_key(key)} =\n{node._serialize(indent=1)}\n"
        out += "}\n"
        return out

    def __repr__(self):
        return f"KVDocument(roots={list(self.roots.keys())})"


class KVParserError(Exception):
    pass


class KVParser:
    WORD_STOP = ' \t\r\n={}[],"'

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def parse(self) -> KVDocument:
        header_data = self._parse_header()
        self._consume_whitespace()
        self._expect("{")
        roots = self._parse_roots()
        self._expect("}")
        doc = KVDocument(
            format=header_data.get("format"),
            format_version=header_data.get("format_version"),
            encoding=header_data.get("encoding"),
            encoding_version=header_data.get("encoding_version"),
        )
        doc.roots = roots
        return doc

    def _parse_header(self) -> dict:
        match = re.search(
            r"<!--\s*kv3\s+encoding:(\w+):version([^\s]+)\s+format:(\w+):version([^\s]+)\s*-->",
            self.text,
        )
        if not match:
            return {}
        self.pos = match.end()
        return {
            "encoding": match.group(1),
            "encoding_version": match.group(2),
            "format": match.group(3),
            "format_version": match.group(4),
        }

    def _parse_roots(self) -> dict:
        roots = {}
        while True:
            self._consume_whitespace()
            if self._peek() == "}":
                break
            key = self._parse_identifier()
            self._consume_whitespace()
            self._expect("=")
            self._consume_whitespace()
            roots[key] = self._parse_node()
        return roots

    def _parse_node(self) -> KVNode:
        self._expect("{")
        props = {}
        children = []

        while True:
            self._consume_whitespace()
            c = self._peek()

            if c == "}":
                self._advance()
                break

            if c == "{":
                children.append(self._parse_node())
                continue

            key = self._parse_identifier()
            self._consume_whitespace()

            if self._peek() == "=":
                self._advance()
                self._consume_whitespace()

            if key == "children":
                children = self._parse_children()
            else:
                props[key] = self._parse_value()

        node = KVNode(**props)
        node.children = children
        return node

    def _parse_children(self) -> list:
        self._expect("[")
        children = []
        while True:
            self._consume_whitespace()
            if self._peek() == "]":
                self._advance()
                break
            child = self._parse_node() if self._peek() == "{" else self._parse_value()
            children.append(child)
            self._consume_whitespace()
            if self._peek() == ",":
                self._advance()
        return children

    def _parse_value(self) -> Any:
        c = self._peek()
        if c == "{":
            return self._parse_node()
        if c == "[":
            return self._parse_array()
        if c == '"':
            return self._parse_string()

        word = self._parse_word()
        if not word:
            raise self._error("a value")

        if word.endswith(":") and self._peek() == '"':
            return KVFlaggedString(word[:-1], self._parse_string())

        if word == "true":
            return True
        if word == "false":
            return False
        if word == "null":
            return None
        if _NUMBER_RE.match(word):
            return int(word) if _INT_RE.match(word) else float(word)
        return word

    def _parse_array(self) -> list:
        self._expect("[")
        values = []
        while True:
            self._consume_whitespace()
            if self._peek() == "]":
                self._advance()
                break
            values.append(self._parse_value())
            self._consume_whitespace()
            if self._peek() == ",":
                self._advance()
        return values

    def _parse_identifier(self) -> str:
        if self._peek() == '"':
            return self._parse_string()
        word = self._parse_word()
        if not word:
            raise self._error("an identifier")
        return word

    def _parse_word(self) -> str:
        self._consume_whitespace()
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in self.WORD_STOP:
            self.pos += 1
        return self.text[start:self.pos]

    def _parse_string(self) -> str:
        self._expect('"')
        if self.text.startswith('""', self.pos):
            self.pos += 2
            end = self.text.find('"""', self.pos)
            if end == -1:
                raise KVParserError(f"Unterminated multi-line string at pos {self.pos}")
            s = self.text[self.pos:end]
            self.pos = end + 3
            return s

        chars = []
        while self.pos < self.length:
            c = self.text[self.pos]
            if c == '"':
                break
            if c == "\\" and self.pos + 1 < self.length:
                nxt = self.text[self.pos + 1]
                if nxt in _ESCAPE_DECODE:
                    chars.append(_ESCAPE_DECODE[nxt])
                    self.pos += 2
                    continue
            chars.append(c)
            self.pos += 1
        if self.pos >= self.length:
            raise KVParserError(f"Unterminated string at pos {self.pos}")
        self.pos += 1
        return "".join(chars)

    def _consume_whitespace(self):
        while self.pos < self.length:
            c = self.text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
            elif c == "/" and self.text.startswith("//", self.pos):
                nl = self.text.find("\n", self.pos)
                self.pos = self.length if nl == -1 else nl + 1
            elif c == "/" and self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end == -1:
                    raise KVParserError(f"Unterminated block comment at pos {self.pos}")
                self.pos = end + 2
            else:
                break

    def _peek(self) -> str:
        self._consume_whitespace()
        return self.text[self.pos] if self.pos < self.length else ""

    def _advance(self):
        self.pos += 1

    def _error(self, expected: str) -> KVParserError:
        got = repr(self.text[self.pos]) if self.pos < self.length else "EOF"
        return KVParserError(f"Expected {expected} at pos {self.pos}, got {got}")

    def _expect(self, char: str):
        self._consume_whitespace()
        if self.pos >= self.length or self.text[self.pos] != char:
            raise self._error(f"'{char}'")
        self.pos += 1
