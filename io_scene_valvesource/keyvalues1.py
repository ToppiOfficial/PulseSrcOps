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

"""KeyValues1 lexing for Valve script formats (QC/QCI, VMT, VDF).

Provides a token stream rather than a value tree. QC is `$directive arg arg`
- positional arguments with no key/value pairing - so a tree would wrap every
directive in a synthetic node. Formats that *are* key/value pairs can still be
built on top of `Cursor` cheaply.

Tokens carry 1-based line numbers so a caller can go back to the raw source
line, which QC needs for the handlers that regex the line directly.
"""

from dataclasses import dataclass

# `//` is universal; QC's reader also honours these two.
LINE_COMMENTS = ('//', '#', ';')


@dataclass
class Token:
    text: str
    line: int
    quoted: bool  # a quoted "loop" is a filename, not the keyword

    def is_(self, text: str) -> bool:
        """Keyword test - never true for a quoted token."""
        return not self.quoted and self.text == text


def tokenize(text: str, line_comments=LINE_COMMENTS, block_comments: bool = True) -> list[Token]:
    """Whitespace-separated words, "quoted strings", braces as standalone tokens
    even when jammed against a word, comments stripped.

    A `\\\\` line continuation is emitted as a token so callers that care about
    line structure (QC's $definemacro) can see it.
    """
    out: list[Token] = []
    line = 1
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        if c == '\n':
            line += 1
            i += 1
            continue

        if c.isspace():
            i += 1
            continue

        if block_comments and c == '/' and text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end == -1:
                line += text.count('\n', i)
                break
            line += text.count('\n', i, end)
            i = end + 2
            continue

        comment = next((k for k in line_comments if text.startswith(k, i)), None)
        if comment:
            nl = text.find('\n', i)
            i = n if nl == -1 else nl
            continue

        if c == '"':
            start_line = line
            i += 1
            buf = []
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n and text[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                if text[i] == '\n':
                    line += 1
                buf.append(text[i])
                i += 1
            i += 1  # closing quote, or past the end if unterminated
            out.append(Token(''.join(buf), start_line, True))
            continue

        if c in '{}':
            out.append(Token(c, line, False))
            i += 1
            continue

        start = i
        while i < n:
            d = text[i]
            if d.isspace() or d in '{}"':
                break
            if block_comments and d == '/' and text.startswith('/*', i):
                break
            if any(text.startswith(k, i) for k in line_comments):
                break
            i += 1
        if i > start:
            out.append(Token(text[start:i], line, False))
        else:
            i += 1  # never stall

    return out


class Cursor:
    """Forward cursor over a token list. Block nesting is the cursor's position,
    which is the point - no in_bodygroup / in_lod / in_sequence flags."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def eof(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self, offset: int = 0) -> Token | None:
        i = self.pos + offset
        return self.tokens[i] if 0 <= i < len(self.tokens) else None

    def next(self) -> Token | None:
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        return tok

    def accept(self, text: str) -> bool:
        """Consume the next token if it is this unquoted keyword."""
        tok = self.peek()
        if tok is not None and tok.is_(text):
            self.pos += 1
            return True
        return False

    def words(self, count: int) -> list[str]:
        """Next `count` token texts, stopping early at a brace or EOF."""
        out = []
        while len(out) < count:
            tok = self.peek()
            if tok is None or (not tok.quoted and tok.text in '{}'):
                break
            out.append(tok.text)
            self.pos += 1
        return out

    def block(self):
        """Yield tokens inside a balanced `{ ... }`, consuming both braces.

        Yields nothing when the next token is not `{`, so a directive that takes
        either an inline form or a braced body needs no lookahead branch.
        """
        if not self.accept('{'):
            return
        depth = 1
        while not self.eof():
            tok = self.next()
            if not tok.quoted:
                if tok.text == '{':
                    depth += 1
                elif tok.text == '}':
                    depth -= 1
                    if depth == 0:
                        return
            yield tok

    def skip_block(self) -> None:
        for _ in self.block():
            pass
