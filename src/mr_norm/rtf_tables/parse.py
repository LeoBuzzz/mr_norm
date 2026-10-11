"""Read tables from Kodeks RTF.

These files do not use Word merge flags (\\clmgf, \\clvmgf). A table is a run of
\\trowd rows. Each cell has a right edge (\\cellx) and any of the four borders.
A wide cell is a horizontal merge. A vertical merge is a cell with text and no
bottom border, empty cells under it, and a bottom border on the last of them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from mr_norm.rtf_tables.glyphs import glyph_text

_SPECIAL = re.compile(r"[{}\\\r\n]")

_SKIP_DEST = frozenset(
    {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "header",
        "footer",
        "headerl",
        "headerr",
        "headerf",
        "footerl",
        "footerr",
        "footerf",
        "footnote",
        "ftnsep",
        "ftnsepc",
        "ftncn",
        "fldinst",
        "listtable",
        "listoverridetable",
        "revtbl",
        "rsidtbl",
        "generator",
        "pgdsctbl",
        "latentstyles",
        "xmlnstbl",
        "themedata",
        "colorschememapping",
        "datastore",
        "object",
        "objdata",
        "userprops",
        "docvar",
        "nonshppict",
    }
)

_SYMBOL_WORDS = {
    "emdash": "—",
    "endash": "–",
    "bullet": "•",
    "lquote": "‘",
    "rquote": "’",
    "ldblquote": "“",
    "rdblquote": "”",
    "tab": "\t",
    "line": "\n",
    "par": "\n",
}


@dataclass
class Picture:
    sha: str
    data: bytes
    mime: str


@dataclass
class PhysicalCell:
    x1: int
    x2: int
    borders: str
    text: str
    parts: list[tuple] = field(default_factory=list)
    synthetic: bool = False

    @property
    def pictures(self) -> list[Picture]:
        return [value for kind, value in self.parts if kind == "pict"]


@dataclass
class PhysicalRow:
    cells: list[PhysicalCell]


@dataclass
class PhysicalTable:
    rows: list[PhysicalRow]
    caption: str
    start: int
    end: int


@dataclass
class GridCell:
    text: str
    parts: list[tuple]
    rowspan: int
    colspan: int
    covered: bool = False
    synthetic: bool = False

    @property
    def pictures(self) -> list[Picture]:
        return [value for kind, value in self.parts if kind == "pict"]


def plain_text(parts: list[tuple]) -> str:
    chunks: list[str] = []
    for kind, value in parts:
        if kind == "text":
            chunks.append(value)
        else:
            mapped = glyph_text(value.sha)
            if mapped:
                chunks.append(mapped)
    return "".join(chunks)


def _mime(data: bytes) -> str:
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "application/octet-stream"


def _picture(data: bytes) -> Picture | None:
    if len(data) < 6:
        return None
    return Picture(hashlib.sha1(data).hexdigest()[:12], data, _mime(data))


def _meaningful(text: str) -> bool:
    return bool(text.replace("\xa0", " ").strip())


class _Parser:
    def __init__(self, source: str, codepage: str = "cp1251"):
        self.s = source
        self.n = len(source)
        self.i = 0
        self.codepage = codepage
        self.skip = [False]
        self.started = [True]
        self.star = [False]
        self.uc = 1
        self.fallback = 0
        self.tables: list[PhysicalTable] = []
        self.flow: list[tuple[str, Any]] = []
        self.rows: list[PhysicalRow] = []
        self.table_start = 0
        self.pending: list[str] = []
        self.body: list[str] = []
        self.split_next = False
        self.in_row = False
        self.defining = False
        self.trleft = 0
        self.saw_trleft = False
        self.cur_borders: set[str] = set()
        self.defs: list[tuple[int, str]] = []
        self.cell_parts: list[tuple] = []
        self.cell_index = 0
        self.built: list[PhysicalCell] = []
        self.pict: list[str] | None = None
        self.pict_depth = 0
        self.slash_at = 0
        self.last_row_end = 0
        self._caption_text = ""
        self._edges: list[tuple[int, int, str]] = []
        self._last_edge: str | None = None

    def parse(self) -> list[PhysicalTable]:
        special = _SPECIAL
        source = self.s
        while self.i < self.n:
            match = special.search(source, self.i)
            if match is None:
                self._text(source[self.i :])
                break
            if match.start() > self.i:
                self._text(source[self.i : match.start()])
            mark = source[match.start()]
            self.i = match.start() + 1
            if mark in "\r\n":
                continue
            if mark == "{":
                self._push()
            elif mark == "}":
                self._pop()
            else:
                self.slash_at = match.start()
                self._control()
        if self.body:
            self._flush_paragraph()
        self._finish_table()
        for paragraph in self.pending:
            self.flow.append(("prose", paragraph))
        self.pending = []
        return self.tables

    def _push(self) -> None:
        self.skip.append(self.skip[-1])
        self.started.append(False)
        self.star.append(False)

    def _pop(self) -> None:
        if self.pict is not None and len(self.skip) == self.pict_depth:
            self._finish_pict()
        if len(self.skip) > 1:
            self.skip.pop()
            self.started.pop()
            self.star.pop()

    def _control(self) -> None:
        source = self.s
        if self.i >= self.n:
            return
        char = source[self.i]
        if char == "'":
            hex_digits = source[self.i + 1 : self.i + 3]
            self.i += 3
            try:
                decoded = bytes([int(hex_digits, 16)]).decode(self.codepage)
            except ValueError:
                return
            self._chars(decoded)
            return
        if not char.isalpha():
            self.i += 1
            self._symbol(char)
            return
        start = self.i
        while self.i < self.n and source[self.i].isalpha():
            self.i += 1
        word = source[start : self.i]
        number: int | None = None
        if self.i < self.n and source[self.i] in "+-":
            sign = -1 if source[self.i] == "-" else 1
            self.i += 1
            num_start = self.i
            while self.i < self.n and source[self.i].isdigit():
                self.i += 1
            if self.i > num_start:
                number = sign * int(source[num_start : self.i])
        elif self.i < self.n and source[self.i].isdigit():
            num_start = self.i
            while self.i < self.n and source[self.i].isdigit():
                self.i += 1
            number = int(source[num_start : self.i])
        if self.i < self.n and source[self.i] == " ":
            self.i += 1
        self._word(word, number)

    def _word(self, word: str, number: int | None) -> None:
        if not self.started[-1]:
            self.started[-1] = True
            if word == "*":
                self.star[-1] = True
                self.started[-1] = False
                return
            if word == "shppict":
                return
            if self.star[-1] or word in _SKIP_DEST:
                self.skip[-1] = True
            if word == "pict" and not self.skip[-1]:
                self.pict = []
                self.pict_depth = len(self.skip)
                return
        if self.skip[-1] or self.pict is not None:
            if word == "uc" and number is not None:
                self.uc = number
            return
        if word == "uc" and number is not None:
            self.uc = number
            return
        if word == "u" and number is not None:
            code = number if number >= 0 else number + 65536
            self._chars(chr(code))
            self.fallback = self.uc
            return
        if word == "trowd":
            self._begin_row()
            return
        if word == "row":
            self._end_row()
            return
        if word == "cell":
            self._end_cell()
            return
        if word == "trleft" and number is not None and self.defining:
            self.trleft = number
            self.saw_trleft = True
            return
        if word == "cellx" and number is not None and self.defining:
            self.defs.append((number, "".join(border for border in "tlbr" if border in self.cur_borders)))
            self.cur_borders = set()
            return
        if len(word) == 7 and word.startswith("clbrdr") and word[-1] in "tlbr" and self.defining:
            self.cur_borders.add(word[-1])
            self._last_edge = word[-1]
            return
        if word in {"brdrnone", "brdrnil"} and self.defining and self._last_edge:
            self.cur_borders.discard(self._last_edge)
            self._last_edge = None
            return
        if word == "intbl" and not self.in_row:
            self._ensure_open_row()
            return
        if word == "par":
            self._par()
            return
        if word == "line":
            self._chars("\n")
            return
        if word == "tab":
            self._chars("\t")
            return
        mapped = _SYMBOL_WORDS.get(word)
        if mapped:
            self._chars(mapped)

    def _symbol(self, char: str) -> None:
        if not self.started[-1]:
            self.started[-1] = True
            if char == "*":
                self.star[-1] = True
                self.started[-1] = False
                return
        if self.skip[-1] or self.pict is not None:
            return
        if char == "~":
            self._chars("\xa0")
        elif char == "_":
            self._chars("-")
        elif char == "-":
            return
        elif char in "\\{}":
            self._chars(char)

    def _chars(self, text: str) -> None:
        if self.fallback:
            self.fallback = max(0, self.fallback - len(text))
            return
        if self.skip[-1]:
            return
        if self.pict is not None:
            return
        if self.in_row:
            if self.defining:
                self._open_cells()
            self.cell_parts.append(("text", text))
            return
        self.body.append(text)

    def _text(self, text: str) -> None:
        if self.pict is not None:
            if len(self.skip) == self.pict_depth:
                self.pict.append(text)
            return
        if self.fallback:
            # Fallback characters occupy one slot each, not one per letter of a run
            # when the run is the single replacement character after \u.
            keep = text[self.fallback :]
            self.fallback = max(0, self.fallback - len(text))
            if not keep or self.skip[-1]:
                return
            text = keep
        if self.skip[-1] or not text:
            return
        if self.in_row:
            if self.defining and any(not ch.isspace() for ch in text):
                self._open_cells()
            if not self.defining:
                self.cell_parts.append(("text", text))
            return
        self.body.append(text)

    def _par(self) -> None:
        if self.in_row:
            if self.defining:
                self._open_cells()
            self.cell_parts.append(("text", "\n"))
            return
        self._flush_paragraph()

    def _flush_paragraph(self) -> None:
        text = "".join(self.body)
        self.body = []
        if _meaningful(text):
            self.pending.append(text.strip())
            self.split_next = True

    def _caption(self) -> str:
        start = None
        for index, paragraph in enumerate(self.pending):
            if "табл" in paragraph.lower():
                start = index
        if start is None:
            for paragraph in self.pending:
                self.flow.append(("prose", paragraph))
            self.pending = []
            return ""
        for paragraph in self.pending[:start]:
            self.flow.append(("prose", paragraph))
        chosen = [part.strip() for part in self.pending[start:] if part.strip()]
        self.pending = []
        return " ".join(chosen)

    def _open_table(self) -> None:
        if self.body:
            self._flush_paragraph()
        if self.rows and self.split_next:
            self._finish_table()
        if not self.rows:
            self.table_start = getattr(self, "slash_at", self.i)
            self._caption_text = self._caption()
        self.split_next = False

    def _ensure_open_row(self) -> None:
        """Word sometimes writes cell text before the \\trowd that describes it."""
        if self.in_row or self.skip[-1]:
            return
        self._open_table()
        self.in_row = True
        self.defining = False
        self.trleft = 0
        self.saw_trleft = False
        self.cur_borders = set()
        self.defs = []
        self._edges = []
        self.cell_parts = []
        self.cell_index = 0
        self.built = []

    def _begin_row(self) -> None:
        if self.in_row:
            # A second \\trowd in the same row is Word repeating the geometry
            # after the cells, or a Kodeks \\intbl that arrived just before it.
            self.defining = True
            self.trleft = 0
            self.saw_trleft = False
            self.cur_borders = set()
            self.defs = []
            self._edges = []
            self._last_edge = None
            return
        self._open_table()
        self.in_row = True
        self.defining = True
        self.trleft = 0
        self.saw_trleft = False
        self.cur_borders = set()
        self.defs = []
        self._edges = []
        self._last_edge = None
        self.cell_parts = []
        self.cell_index = 0
        self.built = []

    def _open_cells(self) -> None:
        self.defining = False
        left = self.trleft if self.saw_trleft else 0
        self._edges = []
        for right, borders in self.defs:
            self._edges.append((left, right, borders))
            left = right

    def _end_cell(self) -> None:
        if self.skip[-1]:
            return
        if not self.in_row:
            self._ensure_open_row()
        if self.defining:
            self._open_cells()
        if self.cell_index < len(getattr(self, "_edges", [])):
            left, right, borders = self._edges[self.cell_index]
        else:
            left, right, borders = 0, 0, ""
        self.built.append(
            PhysicalCell(
                x1=left,
                x2=right,
                borders=borders,
                text="".join(value for kind, value in self.cell_parts if kind == "text"),
                parts=self.cell_parts,
            )
        )
        self.cell_parts = []
        self.cell_index += 1

    def _end_row(self) -> None:
        if not self.in_row or self.skip[-1]:
            return
        if self.cell_parts:
            self._end_cell()
        self._apply_defs()
        edges = getattr(self, "_edges", [])
        while self.cell_index < len(edges):
            left, right, borders = edges[self.cell_index]
            self.built.append(PhysicalCell(left, right, borders, "", [], synthetic=True))
            self.cell_index += 1
        self.rows.append(PhysicalRow(self.built))
        self.last_row_end = self.i
        self.in_row = False
        self.defining = False
        self.built = []
        self.cell_parts = []

    def _apply_defs(self) -> None:
        if not self.defs:
            return
        left = self.trleft if self.saw_trleft else 0
        edges: list[tuple[int, int, str]] = []
        for right, borders in self.defs:
            edges.append((left, right, borders))
            left = right
        self._edges = edges
        for index, cell in enumerate(self.built):
            if index >= len(edges):
                break
            if cell.x1 == 0 and cell.x2 == 0:
                cell.x1, cell.x2, cell.borders = edges[index]

    def _finish_table(self) -> None:
        if not self.rows:
            return
        self.flow.append(("table", len(self.tables)))
        self.tables.append(
            PhysicalTable(
                rows=self.rows,
                caption=getattr(self, "_caption_text", ""),
                start=self.table_start,
                end=getattr(self, "last_row_end", self.i),
            )
        )
        self.rows = []
        self._caption_text = ""

    def _finish_pict(self) -> None:
        raw = "".join(self.pict or [])
        self.pict = None
        hex_digits = re.sub(r"[^0-9A-Fa-f]", "", raw)
        if len(hex_digits) < 12:
            return
        if len(hex_digits) % 2:
            hex_digits = hex_digits[:-1]
        try:
            data = bytes.fromhex(hex_digits)
        except ValueError:
            return
        picture = _picture(data)
        if picture is None:
            return
        if self.in_row:
            if self.defining:
                self._open_cells()
            self.cell_parts.append(("pict", picture))


def parse_tables(source: str) -> list[PhysicalTable]:
    return _parser(source).parse()


def parse_flow(source: str) -> tuple[list[tuple[str, Any]], list[PhysicalTable]]:
    """Prose paragraphs and tables in document order.

    A flow item is ("prose", text) or ("table", index into the table list).
    Paragraphs used as a table caption stay on the table and are not repeated here.
    """
    parser = _parser(source)
    tables = parser.parse()
    return parser.flow, tables


def _parser(source: str) -> _Parser:
    codepage = "cp1251"
    match = re.search(r"\\ansicpg(\d+)", source[:400])
    if match:
        codepage = {1251: "cp1251", 1252: "cp1252"}.get(int(match.group(1)), "cp1251")
    return _Parser(source, codepage)


def column_centers(table: PhysicalTable) -> list[int]:
    return _column_edges(table)


def _column_edges(table: PhysicalTable, tolerance: int = 140) -> list[int]:
    """One vertical line, even when a later row writes its \\cellx a padding-width away.

    Kodeks sometimes repeats the same grid about 114 twips to the right (the cell's
    left padding). Nearby edges merge only while no cell has its two ends in the
    two groups: a real column, however narrow, stays. Merging is not transitive
    across that cell, so interleaved grids do not collapse into one column.
    """
    points: list[int] = []
    spans: list[tuple[int, int]] = []
    for row in table.rows:
        for cell in row.cells:
            points.extend((cell.x1, cell.x2))
            if cell.x2 > cell.x1:
                spans.append((cell.x1, cell.x2))
    ordered = sorted(set(points))
    if not ordered:
        return []
    clusters: list[set[int]] = [{value} for value in ordered]
    index = 0
    while index < len(clusters) - 1:
        gap = min(clusters[index + 1]) - max(clusters[index])
        if gap <= tolerance and not _cell_bridges(clusters[index], clusters[index + 1], spans):
            clusters[index].update(clusters[index + 1])
            del clusters[index + 1]
            continue
        index += 1
    frequency: dict[int, int] = {}
    for value in points:
        frequency[value] = frequency.get(value, 0) + 1
    return sorted(max(group, key=lambda value: (frequency[value], -value)) for group in clusters)


def _cell_bridges(left: set[int], right: set[int], spans: list[tuple[int, int]]) -> bool:
    for start, end in spans:
        if (start in left and end in right) or (start in right and end in left):
            return True
    return False


def _nearest(centers: list[int], value: int) -> int:
    return min(range(len(centers)), key=lambda index: abs(centers[index] - value))


def _blank(cell: PhysicalCell) -> bool:
    if cell.pictures:
        return False
    return not _meaningful(cell.text)


def _row_is_full(table: PhysicalTable, centers: list[int], row_index: int) -> bool:
    width = len(centers) - 1
    if width <= 0:
        return False
    seen = [False] * width
    for cell in table.rows[row_index].cells:
        if _blank(cell):
            continue
        start = _nearest(centers, cell.x1)
        end = max(start + 1, _nearest(centers, cell.x2))
        for column in range(start, min(width, end)):
            seen[column] = True
    return all(seen)


def _join_parts(pieces: list[GridCell]) -> list[tuple]:
    joined: list[tuple] = []
    for index, piece in enumerate(pieces):
        parts = list(piece.parts)
        if index and joined and parts:
            prev = joined[-1][1] if joined[-1][0] == "text" else ""
            nxt = parts[0][1] if parts[0][0] == "text" else ""
            if prev and nxt and not prev[-1].isspace() and not nxt[0].isspace():
                joined.append(("text", " "))
        joined.extend(parts)
    return joined


def logical_grid(table: PhysicalTable) -> list[list[GridCell]]:
    centers = _column_edges(table)
    if len(centers) < 2:
        return []
    width = len(centers) - 1
    height = len(table.rows)
    grid: list[list[GridCell | None]] = [[None] * width for _ in range(height)]
    anchors: list[GridCell] = []
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            start = _nearest(centers, cell.x1)
            end = _nearest(centers, cell.x2)
            if end <= start:
                end = min(width, start + 1)
            if grid[row_index][start] is not None:
                continue
            item = GridCell(cell.text, cell.parts, 1, end - start, synthetic=cell.synthetic)
            grid[row_index][start] = item
            anchors.append(item)
            for column in range(start + 1, end):
                if grid[row_index][column] is None:
                    grid[row_index][column] = GridCell("", [], 1, 1, covered=True)
    for row_index, row in enumerate(table.rows):
        for cell in row.cells:
            start = _nearest(centers, cell.x1)
            item = grid[row_index][start]
            if item is None or item.covered or "b" in cell.borders:
                continue
            if "t" not in cell.borders and _blank(cell):
                continue
            span = 1
            pieces = [] if _blank(cell) else [item]
            probe = row_index + 1
            closed = False
            while probe < height:
                below_cell = _cell_at(table, centers, probe, start)
                below = grid[probe][start]
                if below is None or below.covered or below_cell is None:
                    break
                if below.colspan != item.colspan:
                    break
                if "t" in below_cell.borders:
                    # The line under the merge is the next cell's top border.
                    closed = span > 1
                    break
                if _row_is_full(table, centers, probe):
                    # A borderless row that fills every column is the next record,
                    # not the next line of this cell.
                    closed = span > 1
                    break
                if not _blank(below_cell):
                    pieces.append(below)
                span += 1
                probe += 1
                if "b" in below_cell.borders:
                    closed = True
                    break
            if not closed or span <= 1:
                continue
            if len(pieces) == 1 and pieces[0] is not item:
                item.text = pieces[0].text
                item.parts = pieces[0].parts
            elif len(pieces) > 1:
                # One visual cell written as a line per row. Keep every line.
                item.parts = _join_parts(pieces)
                item.text = "".join(value for kind, value in item.parts if kind == "text")
            item.rowspan = span
            for extra in range(1, span):
                for column in range(start, start + item.colspan):
                    grid[row_index + extra][column] = GridCell("", [], 1, 1, covered=True)
    return [[cell if cell is not None else GridCell("", [], 1, 1) for cell in row] for row in grid]


def _cell_at(table: PhysicalTable, centers: list[int], row_index: int, column: int) -> PhysicalCell | None:
    for cell in table.rows[row_index].cells:
        start = _nearest(centers, cell.x1)
        if start == column:
            return cell
    return None


def column_headers(grid: list[list[GridCell]], header_rows: int) -> list[str]:
    if not grid:
        return []
    width = len(grid[0])
    headers: list[str] = []
    for column in range(width):
        pieces: list[str] = []
        seen: set[int] = set()
        for row_index in range(header_rows):
            origin = _origin(grid, row_index, column)
            if origin is None or id(origin) in seen:
                continue
            seen.add(id(origin))
            text = plain_text(origin.parts).replace("\xa0", " ")
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                pieces.append(text)
        headers.append(", ".join(pieces))
    return headers


def header_row_count(grid: list[list[GridCell]]) -> int:
    """Rows at the top that belong to a multi-column head, including its vertical span.

    A following row of column numbers (1, 2, 3, …) is still the head. A category
    label merged down the body is not: only a horizontal merge opens the head.
    """
    last = 0
    open_depth = 0
    for row_index, row in enumerate(grid):
        merged = [cell for cell in row if not cell.covered and cell.colspan > 1]
        if merged:
            last = row_index + 1
            for cell in row:
                if not cell.covered:
                    open_depth = max(open_depth, row_index + cell.rowspan)
            continue
        if row_index < open_depth:
            last = row_index + 1
            continue
        break
    while last < len(grid) and _is_index_row(grid[last]):
        last += 1
    return last


def _is_index_row(row: list[GridCell]) -> bool:
    numbers: list[int] = []
    for cell in row:
        if cell.covered:
            continue
        text = re.sub(r"\s+", "", plain_text(cell.parts))
        if not text:
            continue
        if not re.fullmatch(r"\d{1,3}", text):
            return False
        numbers.append(int(text))
    return len(numbers) >= 3 and numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def _origin(grid: list[list[GridCell]], row_index: int, column: int) -> GridCell | None:
    for probe in range(row_index, -1, -1):
        for left in range(column, -1, -1):
            candidate = grid[probe][left]
            if candidate.covered:
                continue
            if left + candidate.colspan > column and probe + candidate.rowspan > row_index:
                return candidate
            break
    return None


def search_lines(table: PhysicalTable, grid: list[list[GridCell]], limit: int = 4) -> list[str]:
    header_rows = header_row_count(grid)
    if header_rows == 0 and grid:
        header_rows = 1
    headers = column_headers(grid, header_rows)
    caption = re.sub(r"\s+", " ", table.caption).strip()
    lines: list[str] = []
    seen: set[int] = set()
    for row in grid[header_rows:]:
        for column, cell in enumerate(row):
            if cell.covered or cell.synthetic or id(cell) in seen:
                continue
            seen.add(id(cell))
            value = re.sub(r"\s+", " ", plain_text(cell.parts)).strip()
            if not value or set(value) <= set("-–—"):
                continue
            label = headers[column] if column < len(headers) else ""
            pieces = [piece for piece in (caption, label, value) if piece]
            lines.append(" — ".join(pieces))
            if len(lines) >= limit:
                return lines
    return lines

