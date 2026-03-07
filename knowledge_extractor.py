"""Extract plain text from uploaded knowledge source files."""
import io

import pypdf
import docx


def extract_text(content: bytes, file_type: str) -> str:
    """Extract plain text from file bytes.

    Args:
        content: Raw file bytes.
        file_type: One of 'txt', 'md', 'pdf', 'docx'.

    Returns:
        Extracted plain text string.

    Raises:
        ValueError: If file_type is not supported.
    """
    if file_type in ("txt", "md"):
        return content.decode("utf-8", errors="replace")

    if file_type == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)

    if file_type == "docx":
        doc = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    raise ValueError(f"Unsupported file type: {file_type}")
