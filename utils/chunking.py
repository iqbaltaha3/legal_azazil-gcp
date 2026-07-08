# utils/chunking.py
import re

def detect_sections(text):
    """
    Split text into sections based on common legal headers.
    Returns list of (section_title, section_text).
    """
    header_patterns = [
        r'^ARTICLE\s+[IVX]+\s*[.:]?\s*(.*?)$',
        r'^Section\s+[\d.]+[.:]?\s*(.*?)$',
        r'^Clause\s+[\d.]+[.:]?\s*(.*?)$',
        r'^SCHEDULE\s+[A-Z]?\s*[.:]?\s*(.*?)$',
        r'^EXHIBIT\s+[A-Z]?\s*[.:]?\s*(.*?)$',
        r'^\d+\.\s*[A-Z][A-Za-z\s]{2,60}$',
        r'^[A-Z][A-Z\s]{4,}$',
        r'^[A-Z][a-z]+\s+[A-Z][a-z]+$',
    ]
    compiled = [re.compile(p, re.MULTILINE | re.IGNORECASE) for p in header_patterns]
    
    lines = text.split('\n')
    sections = []
    current_title = "PREAMBLE"
    current_text = []
    for line in lines:
        matched = False
        for pattern in compiled:
            if pattern.match(line.strip()):
                if current_text:
                    sections.append((current_title, '\n'.join(current_text)))
                current_title = line.strip()
                current_text = []
                matched = True
                break
        if not matched:
            current_text.append(line)
    if current_text:
        sections.append((current_title, '\n'.join(current_text)))
    return sections

def chunk_section(text, max_chunk_size=5000, overlap=500):
    """
    Split a section into overlapping chunks if it exceeds max_chunk_size.
    Returns list of chunk strings.
    """
    if len(text) <= max_chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        if end < len(text):
            last_period = text.rfind('. ', start, end)
            if last_period != -1 and last_period > start + max_chunk_size - 200:
                end = last_period + 2
        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else end
    return chunks