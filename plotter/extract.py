"""Extract plain text from an uploaded document (.txt/.pdf/.docx) so it
can be run through the same layout/pagination pipeline as typed text.
"""

from __future__ import annotations

from pathlib import Path


def extract_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)

    if suffix == ".docx":
        import docx

        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)

    raise ValueError(f"Unsupported document type '{suffix}'. Use .txt, .pdf, or .docx.")
