import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_TOP_LEVEL_HEADING = "##"


class FullDocumentReplacementError(Exception):
    pass


def extract_section(content_md: str, section_key: str) -> str | None:
    """Return the body (without heading) of the section matching section_key."""
    lines = content_md.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and match.group(2).strip().lower() == section_key.strip().lower():
            level = len(match.group(1))
            body = []
            for subsequent in lines[index + 1 :]:
                next_match = _HEADING_RE.match(subsequent)
                if next_match and len(next_match.group(1)) <= level:
                    break
                body.append(subsequent)
            return "\n".join(body).strip()
    return None


def apply_section_update(content_md: str, section_key: str, new_content: str) -> str:
    """Replace only the target section body, never the whole document.

    A section body may legally contain nested (##) subsections, so the match is
    bounded by the next heading of the same or higher level. If the section does
    not exist yet it is appended at the end.
    """
    top_level_heads = [
        match
        for match in _HEADING_RE.finditer(new_content)
        if len(match.group(1)) == len(_TOP_LEVEL_HEADING)
    ]
    if len(top_level_heads) >= 3:
        raise FullDocumentReplacementError(
            "model returned content containing multiple top-level sections; "
            "refusing to replace the whole document"
        )

    lines = content_md.splitlines()
    start: int | None = None
    level: int = 0
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match and match.group(2).strip().lower() == section_key.strip().lower():
            start = index
            level = len(match.group(1))
            break

    body = new_content.strip()

    if start is None:
        appended = f"\n\n{_TOP_LEVEL_HEADING} {section_key}\n\n{body}\n"
        return content_md.rstrip() + appended

    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = _HEADING_RE.match(lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break

    heading = lines[start]
    new_lines = lines[:start] + [heading, "", body] + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"
