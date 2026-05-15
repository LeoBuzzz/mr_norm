from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mr_norm.config.paths import ProjectPaths
from mr_norm.tools.schema import ParagraphRecord, StructuredDocument


POINT_PATTERNS = [
    re.compile(r"^\s*\{([^}]*\d[^}]*)\}"),
    re.compile(r"^\s*(\d+(?:[\.\-]\d+)+)(?=\s)"),
    re.compile(r"^\s*(\d+(?:[\.\-]\d+)*)\."),
    re.compile(r"^\s*пункт[ае]?\s+(\d+(?:[\.\-]\d+)*)", re.IGNORECASE),
]


def extract_point_number(text: str) -> str:
    for pattern in POINT_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip()
    return ""


def clean_paragraph_text(text: str) -> str:
    text = (text or "").replace("\r", "").replace("\n", "").replace("\x07", "")
    text = re.sub(r"[\t ]+", " ", text)
    return text.strip()


def should_skip_paragraph(text: str) -> bool:
    if not text:
        return True
    if re.fullmatch(r"[-–—]?\s*\d+\s*[-–—]?", text):
        return True
    if re.fullmatch(r"Страница\s+\d+", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"Переход к Содержанию документа осуществляется по ссылке", text, re.IGNORECASE):
        return True
    return False


def infer_heading_level(text: str, outline_level: int, style_name: str = "") -> int | None:
    if 1 <= outline_level <= 9:
        return min(outline_level, 6)
    lowered_style = style_name.lower()
    style_match = re.search(r"(heading|заголовок)\s*(\d+)", lowered_style)
    if style_match:
        return min(int(style_match.group(2)), 6)
    stripped = text.strip()
    if len(stripped) < 180 and re.match(r"^ГОСТ\s+(?:Р\s+)?[A-Za-zА-Яа-я0-9.\-]+", stripped, re.IGNORECASE):
        return 1
    if len(stripped) < 180 and re.match(r"^(?:СО|РД)\s+\d+(?:[.\-]\d+)+", stripped, re.IGNORECASE):
        return 1
    if re.match(r"^(Раздел|Глава|Статья)\s+[IVXLCDM0-9]+", stripped, re.IGNORECASE):
        return 1 if stripped.lower().startswith("раздел") else 2
    if len(stripped) < 180 and re.match(r"^[IVXLCDM]+\.\s+\S+", stripped):
        return 1
    if len(stripped) < 160 and re.match(r"^\d+\s+[А-ЯЁ][^.:;]{4,}$", stripped):
        return 2
    if (
        12 <= len(stripped) <= 180
        and stripped.upper() == stripped
        and re.search(r"[А-ЯЁ]{4,}", stripped)
        and not re.search(r"\d{4}|\bN\b|ГОСТ\s+\d", stripped)
    ):
        return 2
    return None


def make_paragraph(
    index: int,
    text: str,
    outline_level: int = 10,
    style_name: str = "",
    char_start: int = 0,
) -> ParagraphRecord | None:
    cleaned = clean_paragraph_text(text)
    if should_skip_paragraph(cleaned):
        return None
    heading_level = infer_heading_level(cleaned, outline_level, style_name)
    return ParagraphRecord(
        paragraph_index=index,
        text=cleaned,
        outline_level=outline_level,
        style_name=style_name,
        is_heading=heading_level is not None,
        heading_level=heading_level,
        point_number=extract_point_number(cleaned),
        char_start=char_start,
        char_end=char_start + len(cleaned),
    )


def marked_text_from_document(document: StructuredDocument) -> str:
    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text
        if paragraph.is_heading and paragraph.heading_level:
            hashes = "#" * min(paragraph.heading_level, 6)
            lines.append(f"{hashes} {text} {hashes}")
        else:
            lines.append(text)
    return "\n".join(lines).strip() + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def atomic_write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


@dataclass
class RtfProcessResult:
    source_file: str
    marked_path: str
    structured_path: str
    paragraphs: int
    error: str = ""


class RtfReadError(RuntimeError):
    """Word COM read failed or produced no extractable paragraphs; outputs must not be written."""


DEFAULT_RTF_PER_FILE_TIMEOUT_SEC = 120.0


def _rtf_process_worker(root: str, source_path: str, result_queue: object) -> None:
    paths = ProjectPaths.from_root(Path(root))
    processor = RtfProcessor(paths)
    try:
        result = processor.process_file(Path(source_path), word=None)
    except Exception as exc:
        result = processor._failed_rtf_result(Path(source_path), f"{source_path}: {type(exc).__name__}: {exc}")
    try:
        result_queue.put(result.__dict__)  # type: ignore[attr-defined]
    except Exception:
        pass


def _close_open_word_documents(word: object) -> None:
    """Close every open document in this Word.Application (SaveChanges=False)."""
    try:
        docs = word.Documents
        count = int(docs.Count)
    except Exception:
        return
    for idx in range(count, 0, -1):
        try:
            docs(idx).Close(SaveChanges=False)
        except Exception:
            pass


def _word_quit_safe(word: object) -> None:
    """Quit Word; may raise COM errors even when Word is already closing."""
    word.Quit(SaveChanges=False)


def _safe_shutdown_word(word: object, timeout_sec: float = 25.0) -> dict[str, Any]:
    """Close all documents, then Quit on the same thread as Dispatch (Word COM is not thread-safe).

    If ``Quit`` raises (common with pywin32 after a successful shutdown), we treat that as **ok**
    when there are no open documents or the application object is already invalid.
    ``timeout_sec`` is reserved for future use if a non-blocking Quit strategy is needed again.
    """
    del timeout_sec  # API compatibility; synchronous Quit below.
    _close_open_word_documents(word)
    try:
        _word_quit_safe(word)
        return {"status": "ok"}
    except Exception as exc:
        try:
            n = int(word.Documents.Count)
        except Exception:
            return {
                "status": "ok",
                "message": "Word COM session ended after Quit (handle invalid or app closed)",
                "quit_exception": repr(exc),
            }
        if n == 0:
            return {
                "status": "ok",
                "message": "Quit raised a benign COM exception; no documents remain open",
                "quit_exception": repr(exc),
            }
        _close_open_word_documents(word)
        try:
            n2 = int(word.Documents.Count)
        except Exception:
            return {
                "status": "ok",
                "message": "Quit raised but Word no longer responds on COM (likely closed)",
                "quit_exception": repr(exc),
            }
        return {
            "status": "error",
            "message": f"Quit failed with {n2} document(s) still open: {exc}",
        }


def pick_size_diverse_rtf_paths(input_dir: Path, count: int = 10) -> list[Path]:
    """Pick up to *count* RTF paths spread across file size (smallest … largest).

    Used for smoke runs so one session exercises small and large documents.
    """
    all_rtf = sorted(p for p in input_dir.glob("*.rtf") if not p.name.startswith("~$"))
    if not all_rtf or count <= 0:
        return []
    if len(all_rtf) <= count:
        return all_rtf
    by_size = sorted(all_rtf, key=lambda p: p.stat().st_size)
    n = len(by_size)
    slots = count
    chosen: list[Path] = []
    seen: set[Path] = set()
    for k in range(slots):
        idx = (k * (n - 1)) // max(1, slots - 1)
        p = by_size[idx]
        if p not in seen:
            seen.add(p)
            chosen.append(p)
            continue
        for delta in range(1, n):
            for j in (idx + delta, idx - delta):
                if 0 <= j < n and by_size[j] not in seen:
                    p = by_size[j]
                    seen.add(p)
                    chosen.append(p)
                    break
            if len(chosen) > k:
                break
        else:
            for p in by_size:
                if p not in seen:
                    seen.add(p)
                    chosen.append(p)
                    break
    return chosen[:count]


class RtfProcessor:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.last_word_cleanup: dict[str, Any] = {}

    def process_all(
        self,
        limit: int | None = None,
        only_paths: list[Path] | None = None,
        per_file_timeout_sec: float = DEFAULT_RTF_PER_FILE_TIMEOUT_SEC,
    ) -> list[RtfProcessResult]:
        if only_paths is not None:
            files = [Path(p).resolve() for p in only_paths]
        else:
            files = sorted(path for path in self.paths.input_dir.glob("*.rtf") if not path.name.startswith("~$"))
        if limit is not None:
            files = files[:limit]
        if not files:
            self.last_word_cleanup = {"status": "not_started", "mode": "isolated_per_file", "files_total": 0}
            return []
        results: list[RtfProcessResult] = []
        timed_out = 0
        for path in files:
            result = self._process_file_isolated(path, per_file_timeout_sec=per_file_timeout_sec)
            if result.error and "timed out" in result.error:
                timed_out += 1
            results.append(result)
        self.last_word_cleanup = {
            "status": "ok" if timed_out == 0 else "timeouts",
            "mode": "isolated_per_file",
            "files_total": len(files),
            "files_timed_out": timed_out,
            "per_file_timeout_sec": per_file_timeout_sec,
        }
        return results

    def _process_file_isolated(self, path: Path, per_file_timeout_sec: float) -> RtfProcessResult:
        ctx = mp.get_context("spawn")
        result_queue = ctx.Queue(maxsize=1)
        process = ctx.Process(
            target=_rtf_process_worker,
            args=(str(self.paths.root), str(path), result_queue),
            daemon=False,
        )
        process.start()
        process.join(per_file_timeout_sec)
        if process.is_alive():
            process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join(5)
            result_queue.close()
            result_queue.join_thread()
            return self._failed_rtf_result(
                path,
                f"{path}: Word COM read timed out after {per_file_timeout_sec:g}s",
            )
        try:
            data = result_queue.get_nowait()
        except queue.Empty:
            exit_code = process.exitcode
            return self._failed_rtf_result(path, f"{path}: isolated Word worker exited without result (exit_code={exit_code})")
        finally:
            result_queue.close()
            result_queue.join_thread()
        return RtfProcessResult(**data)

    def _failed_rtf_result(self, path: Path, message: str) -> RtfProcessResult:
        """Per-file failure: do not write marked/structured outputs (see RtfReadError docstring)."""
        return RtfProcessResult(
            source_file=str(path),
            marked_path="",
            structured_path="",
            paragraphs=0,
            error=message,
        )

    def process_file(self, path: Path, word: object | None = None) -> RtfProcessResult:
        self.paths.ensure_output_dirs()
        filename = f"{path.stem}.txt"
        marked_path = self.paths.marked_docs_dir / filename
        structured_path = self.paths.marked_docs_dir / f"{path.stem}.structured.json"
        max_attempts = 2 if word is not None else 3
        shared_word = word is not None
        try:
            for attempt in range(max_attempts):
                try:
                    document = self._read_with_word(path, word=word)
                    if not document.paragraphs:
                        return self._failed_rtf_result(
                            path, f"{path}: Word returned no extractable paragraphs after filtering"
                        )
                    atomic_write_text(marked_path, marked_text_from_document(document))
                    atomic_write_json(structured_path, document.to_dict())
                    return RtfProcessResult(
                        source_file=str(path),
                        marked_path=str(marked_path),
                        structured_path=str(structured_path),
                        paragraphs=len(document.paragraphs),
                        error="",
                    )
                except RtfReadError as exc:
                    return self._failed_rtf_result(path, str(exc))
                except Exception as exc:
                    if attempt + 1 >= max_attempts:
                        return self._failed_rtf_result(path, f"{path}: {exc}")
            return self._failed_rtf_result(path, f"{path}: exhausted COM read attempts")
        finally:
            if shared_word:
                _close_open_word_documents(word)

    def _open_word(self) -> object:
        try:
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("pywin32 is not installed") from exc

        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        try:
            word.DisplayAlerts = 0
        except Exception:
            pass
        try:
            word.ScreenUpdating = False
        except Exception:
            pass
        try:
            word.AutomationSecurity = 3
        except Exception:
            pass
        return word

    def _read_with_word(self, path: Path, word: object | None = None) -> StructuredDocument:
        """Read RTF via Word. Opens a unique temp copy of *path* so Office's «disabled file»
        list (crash recovery prompt) keyed on the original path does not block automation.
        """
        own_word = word is None
        if word is None:
            word = self._open_word()
        doc = None
        tmp_path: Path | None = None
        try:
            suffix = path.suffix if path.suffix else ".rtf"
            fd, tmp_name = tempfile.mkstemp(prefix="mr_norm_word_", suffix=suffix)
            os.close(fd)
            tmp_path = Path(tmp_name)
            shutil.copy2(path, tmp_path)
            doc = self._open_word_document(word, tmp_path)
            paragraphs: list[ParagraphRecord] = []
            char_pos = 0
            for raw_index, para in enumerate(doc.Paragraphs):
                text = para.Range.Text
                outline_level = int(para.OutlineLevel)
                try:
                    style_name = str(para.Style.NameLocal)
                except Exception:
                    style_name = ""
                paragraph = make_paragraph(
                    len(paragraphs),
                    text,
                    outline_level=outline_level,
                    style_name=style_name,
                    char_start=char_pos,
                )
                if paragraph:
                    paragraphs.append(paragraph)
                    char_pos = paragraph.char_end + 1
                elif text:
                    char_pos += len(text)
            return StructuredDocument(source_file=str(path), filename=f"{path.stem}.txt", paragraphs=paragraphs)
        finally:
            if doc is not None:
                try:
                    doc.Close(SaveChanges=False)
                except Exception:
                    # Closing can fail after successful extraction on some Word COM sessions.
                    # The extracted document is still valid; process cleanup is handled by Quit.
                    pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if word is not None:
                _close_open_word_documents(word)
            if own_word and word is not None:
                try:
                    _word_quit_safe(word)
                except Exception:
                    pass

    def _open_word_document(self, word: object, path: Path) -> object:
        return word.Documents.Open(
            FileName=str(path),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Revert=True,
            NoEncodingDialog=True,
            OpenAndRepair=False,
        )
