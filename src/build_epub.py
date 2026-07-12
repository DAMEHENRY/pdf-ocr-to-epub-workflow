#!/usr/bin/env python3
"""Build a readable EPUB from a PDF-to-Markdown export.

This script is intentionally content-neutral. It expects you to provide your
own source Markdown and optional extracted image folder.
"""

from __future__ import annotations

import argparse
import html
import mimetypes
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


PAGE_RE = re.compile(r"^--- Page (\d+) / \d+ ---$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
HTML_IMAGE_RE = re.compile(
    r'<div style="text-align: center;"><img src="([^"]+)" alt="([^"]*)" width="([^"]+)" /></div>'
)
HTML_CENTER_RE = re.compile(r'<div style="text-align: center;">(.*?)</div>')
IMAGE_SRC_RE = re.compile(r'src="([^"]+)"')
MARKDOWN_IMAGE_RE = re.compile(r'^!\[([^]]*)\]\(([^)]+)\)$')
FOOTNOTE_RE = re.compile(r"\$\s*\^\{([^}]+)\}\s*\$")
FOOTNOTE_BODY_RE = re.compile(r"^\s*(\d{1,2}|[①②③④⑤⑥⑦⑧⑨⑩*])\s+(.{20,})$")
SPACED_INITIAL_RE = re.compile(r"^(#{2,6}\s+)([A-ZI]) ([a-z].*)$")


@dataclass
class Chapter:
    title: str
    filename: str
    body: list[str] = field(default_factory=list)
    headings: list[tuple[str, str, int]] = field(default_factory=list)


def slugify(value: str, fallback: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:56] or fallback


def normalize_footnote_number(value: str) -> str:
    return value.strip().strip("[]")


def looks_like_footnote_prose(value: str) -> bool:
    value = value.lstrip('"“‘( ')
    if not value or value.startswith("*"):
        return False
    first_alpha = next((char for char in value if char.isalpha()), "")
    return bool(first_alpha and (first_alpha.isupper() or ord(first_alpha) > 127))


def next_nonblank_line(lines: list[str], start: int) -> str:
    return next((line.strip() for line in lines[start:] if line.strip()), "")


def should_join_page_break(pending: list[str], next_line: str) -> bool:
    if not pending or not next_line or HEADING_RE.match(next_line):
        return False
    next_alpha = next((char for char in next_line if char.isalpha()), "")
    starts_lower = bool(next_alpha and next_alpha.islower())
    previous = pending[-1].rstrip()
    previous_unfinished = bool(previous and previous[-1] not in ".!?。！？:：")
    return starts_lower or previous_unfinished


def clean_inline(value: str, footnote_links: dict[str, str] | None = None) -> str:
    replacements: list[str] = []

    def replace_marker(match: re.Match[str]) -> str:
        shown = match.group(1).strip()
        number = normalize_footnote_number(shown)
        key = (footnote_links or {}).get(number)
        if key:
            markup = (
                f'<a epub:type="noteref" class="noteref" id="fnref-{key}" '
                f'href="#fn-{key}"><sup>{html.escape(number)}</sup></a>'
            )
        else:
            markup = f"<sup>{html.escape(shown)}</sup>"
        token = f"@@EPUB-INLINE-{len(replacements)}@@"
        replacements.append(markup)
        return token

    escaped = html.escape(FOOTNOTE_RE.sub(replace_marker, value), quote=False)
    for index, markup in enumerate(replacements):
        escaped = escaped.replace(f"@@EPUB-INLINE-{index}@@", markup)
    return escaped


def analyze_footnotes(lines: list[str]) -> tuple[dict[int, tuple[str, str, str]], dict[int, dict[str, str]]]:
    page = 0
    references: dict[int, set[str]] = {}
    candidates: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        marker = PAGE_RE.match(line.strip())
        if marker:
            page = int(marker.group(1))
            continue
        references.setdefault(page, set()).update(
            normalize_footnote_number(match.group(1)) for match in FOOTNOTE_RE.finditer(line)
        )
        body = FOOTNOTE_BODY_RE.match(line)
        if body and looks_like_footnote_prose(body.group(2)):
            candidates.append((index, page, normalize_footnote_number(body.group(1)), body.group(2)))

    bodies: dict[int, tuple[str, str, str]] = {}
    links: dict[int, dict[str, str]] = {}
    used_refs: set[tuple[int, str]] = set()
    for index, body_page, number, text in candidates:
        ref_page = next(
            (
                candidate_page
                for candidate_page in (body_page, body_page - 1)
                if number in references.get(candidate_page, set())
                and (candidate_page, number) not in used_refs
            ),
            None,
        )
        if ref_page is None:
            continue
        key = f"p{ref_page}-{slugify(number, 'note')}"
        used_refs.add((ref_page, number))
        links.setdefault(ref_page, {})[number] = key
        bodies[index] = (key, number, text)
    return bodies, links


def is_ocr_noise(value: str, skip_lines: set[str]) -> bool:
    if value in skip_lines:
        return True
    if "\ufffd" in value:
        return True
    if len(value) > 160:
        dominant = max(value.count(char) for char in set(value)) / len(value)
        return dominant > 0.55
    return False


def add_paragraph(
    target: list[str], pending: list[str], footnote_links: dict[str, str] | None = None
) -> None:
    if not pending:
        return
    text = " ".join(part.strip() for part in pending if part.strip())
    pending.clear()
    if text:
        target.append(f"<p>{clean_inline(text, footnote_links)}</p>")


def normalize_image_src(src: str, image_prefix: str) -> str:
    if image_prefix and src.startswith(f"{image_prefix}/"):
        return src[len(image_prefix) + 1 :]
    return src


def convert_lines(
    lines: list[str],
    *,
    image_prefix: str,
    skip_lines: set[str],
    title_fixes: dict[str, str],
    promote_to_chapter: set[str],
) -> tuple[list[Chapter], set[str]]:
    footnote_bodies, footnote_links_by_page = analyze_footnotes(lines)
    chapters: list[Chapter] = []
    image_refs: set[str] = set()
    current = Chapter("Front Matter", "chapter-000-front-matter.xhtml")
    chapters.append(current)
    pending_para: list[str] = []
    in_code_block = False
    code_language = ""
    code_lines: list[str] = []
    used_ids: dict[str, int] = {}
    current_page = 0
    pending_footnote_links: dict[str, str] = {}
    carrying_page_break = False

    def flush_paragraph() -> None:
        add_paragraph(current.body, pending_para, pending_footnote_links)
        pending_footnote_links.clear()

    def heading_id(title: str) -> str:
        base = slugify(title, "section")
        used_ids[base] = used_ids.get(base, 0) + 1
        return base if used_ids[base] == 1 else f"{base}-{used_ids[base]}"

    def add_code_block() -> None:
        nonlocal in_code_block, code_language
        if not in_code_block:
            return
        language_class = (
            f' class="language-{html.escape(code_language, quote=True)}"' if code_language else ""
        )
        current.body.append(
            f"<pre><code{language_class}>{html.escape(chr(10).join(code_lines))}</code></pre>"
        )
        code_lines.clear()
        code_language = ""
        in_code_block = False

    def start_chapter(title: str) -> None:
        nonlocal current
        flush_paragraph()
        filename = f"chapter-{len(chapters):03d}-{slugify(title, f'chapter-{len(chapters):03d}')}.xhtml"
        current = Chapter(title=html.unescape(title), filename=filename)
        anchor = heading_id(title)
        current.body.append(f'<h1 id="{anchor}">{clean_inline(title)}</h1>')
        current.headings.append((title, anchor, 1))
        chapters.append(current)

    for line_index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            if in_code_block:
                add_code_block()
            else:
                in_code_block = True
                code_language = stripped[3:].strip()
            continue
        if in_code_block:
            code_lines.append(line)
            continue
        page_marker = PAGE_RE.match(stripped)
        if page_marker:
            next_line = next_nonblank_line(lines, line_index + 1)
            carrying_page_break = should_join_page_break(pending_para, next_line)
            if not carrying_page_break:
                flush_paragraph()
            current_page = int(page_marker.group(1))
            continue
        if not stripped:
            if PAGE_RE.match(next_nonblank_line(lines, line_index + 1)) or carrying_page_break:
                continue
            flush_paragraph()
            continue
        carrying_page_break = False
        if is_ocr_noise(stripped, skip_lines):
            flush_paragraph()
            continue
        if line_index in footnote_bodies:
            flush_paragraph()
            key, number, footnote_text = footnote_bodies[line_index]
            current.body.append(
                f'<aside epub:type="footnote" id="fn-{key}" class="footnote"><p>'
                f'<a class="footnote-back" href="#fnref-{key}">{html.escape(number)}</a> '
                f'{clean_inline(footnote_text)}</p></aside>'
            )
            continue
        stripped = SPACED_INITIAL_RE.sub(r"\1\2\3", stripped)

        heading = HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            title = title_fixes.get(heading.group(2).strip(), heading.group(2).strip())
            if title in skip_lines:
                flush_paragraph()
                continue
            if title in promote_to_chapter or level == 1:
                start_chapter(title)
            else:
                flush_paragraph()
                tag = f"h{min(level, 6)}"
                anchor = heading_id(title)
                current.body.append(f'<{tag} id="{anchor}">{clean_inline(title)}</{tag}>')
                current.headings.append((title, anchor, level))
            continue

        image_match = HTML_IMAGE_RE.match(stripped)
        if image_match:
            flush_paragraph()
            src, alt, width = image_match.groups()
            src = normalize_image_src(src, image_prefix)
            image_refs.add(src)
            current.body.append(
                '<figure class="image">'
                f'<img src="../images/{html.escape(src, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}" style="max-width:{html.escape(width, quote=True)};" />'
                "</figure>"
            )
            continue

        markdown_image = MARKDOWN_IMAGE_RE.match(stripped)
        if markdown_image:
            flush_paragraph()
            alt, src = markdown_image.groups()
            src = normalize_image_src(src, image_prefix)
            image_refs.add(src)
            current.body.append(
                '<figure class="image">'
                f'<img src="../images/{html.escape(src, quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}" />'
                "</figure>"
            )
            continue

        if stripped.startswith("<table") and stripped.endswith("</table>"):
            flush_paragraph()
            current.body.append(re.sub(r"\bborder=([0-9]+)", r'border="\1"', stripped))
            continue

        center_match = HTML_CENTER_RE.match(stripped)
        if center_match:
            flush_paragraph()
            current.body.append(f'<p class="center">{clean_inline(center_match.group(1))}</p>')
            continue

        for src in IMAGE_SRC_RE.findall(stripped):
            image_refs.add(normalize_image_src(src, image_prefix))
        if current.title.casefold() in {"contents", "table of contents", "目录"}:
            flush_paragraph()
            current.body.append(f'<p class="printed-toc-entry">{clean_inline(stripped)}</p>')
        else:
            pending_footnote_links.update(footnote_links_by_page.get(current_page, {}))
            pending_para.append(stripped)

    add_code_block()
    flush_paragraph()
    return [chapter for chapter in chapters if chapter.body], image_refs


def normalize_toc_label(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    value = re.sub(r"\s*[.…·]{2,}\s*(?:[ivxlcdm]+|\d+)\s*$", "", value, flags=re.I)
    value = re.sub(r"\s+(?:[ivxlcdm]+|\d+)\s*$", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def link_printed_contents(chapters: list[Chapter]) -> None:
    targets: list[tuple[str, str, int]] = []
    for chapter in chapters:
        if chapter.title.casefold() in {"contents", "table of contents", "目录"}:
            continue
        targets.extend(
            (normalize_toc_label(title), f"{chapter.filename}#{anchor}", level)
            for title, anchor, level in chapter.headings
        )

    cursor = 0
    entry_re = re.compile(r'^<p class="printed-toc-entry">(.*?)</p>$')
    page_suffix_re = re.compile(r"(?:[.…·]{2,}\s*|\s+)(?:[ivxlcdm]+|\d+)\s*$", re.I)
    for chapter in chapters:
        if chapter.title.casefold() not in {"contents", "table of contents", "目录"}:
            continue
        linked: list[str] = []
        for block in chapter.body:
            match = entry_re.match(block)
            if not match:
                linked.append(block)
                continue
            rendered = match.group(1)
            label = normalize_toc_label(rendered)
            found = next(
                ((index, href, level) for index, (title, href, level) in enumerate(targets[cursor:], cursor) if title == label),
                None,
            )
            if found is None:
                found = next(((index, href, level) for index, (title, href, level) in enumerate(targets) if title == label), None)
            if found is None:
                plain = html.unescape(re.sub(r"<[^>]+>", "", rendered))
                if page_suffix_re.search(plain):
                    linked.append(f'<p class="printed-toc-entry unlinked">{clean_inline(label)}</p>')
                continue
            index, href, level = found
            cursor = index + 1
            linked.append(
                f'<p class="printed-toc-entry toc-level-{min(level, 3)}">'
                f'<a href="{html.escape(href, quote=True)}">{clean_inline(label)}</a></p>'
            )
        chapter.body = linked


def chapter_xhtml(chapter: Chapter, language: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}" xml:lang="{language}">
<head>
  <title>{html.escape(chapter.title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles.css" />
</head>
<body>
{chr(10).join(chapter.body)}
</body>
</html>
'''


def build_nav(chapters: list[Chapter], language: str) -> str:
    nav_chapters = [chapter for chapter in chapters if chapter.title != "Front Matter"]
    items = "\n".join(
        f'    <li><a href="xhtml/{chapter.filename}">{html.escape(chapter.title)}</a></li>'
        for chapter in nav_chapters
    )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}" xml:lang="{language}">
<head>
  <title>Contents</title>
  <link rel="stylesheet" type="text/css" href="styles.css" />
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Contents</h1>
    <ol>
{items}
    </ol>
  </nav>
</body>
</html>
'''


def build_cover_page(title: str, language: str, cover_image: str | None = None) -> str:
    content = (
        f'<img class="cover-image" src="../images/{html.escape(cover_image, quote=True)}" '
        f'alt="{html.escape(title, quote=True)}" />'
        if cover_image
        else f'<section class="generated-cover"><h1>{html.escape(title)}</h1></section>'
    )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}" xml:lang="{language}">
<head>
  <title>Cover</title>
  <link rel="stylesheet" type="text/css" href="../styles.css" />
</head>
<body class="cover-page">
  {content}
</body>
</html>
'''


def build_ncx(chapters: list[Chapter], book_id: str, title: str, author: str) -> str:
    nav_points = []
    nav_chapters = [chapter for chapter in chapters if chapter.title != "Front Matter"]
    for idx, chapter in enumerate(nav_chapters, 1):
        nav_points.append(
            f'''    <navPoint id="navPoint-{idx}" playOrder="{idx}">
      <navLabel><text>{html.escape(chapter.title)}</text></navLabel>
      <content src="xhtml/{chapter.filename}"/>
    </navPoint>'''
        )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <docAuthor><text>{html.escape(author)}</text></docAuthor>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>
'''


def media_type(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def build_opf(
    chapters: list[Chapter],
    image_refs: set[str],
    book_id: str,
    *,
    title: str,
    author: str,
    language: str,
    image_dir: Path | None,
    cover_image: str | None,
) -> str:
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="styles.css" media-type="text/css"/>',
        '<item id="cover-page" href="xhtml/cover.xhtml" media-type="application/xhtml+xml"/>',
    ]
    spine = ['<itemref idref="cover-page" linear="no"/>']
    for idx, chapter in enumerate(chapters):
        item_id = f"chapter-{idx:03d}"
        manifest.append(
            f'<item id="{item_id}" href="xhtml/{chapter.filename}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{item_id}"/>')
    for idx, src in enumerate(sorted(image_refs)):
        source_path = image_dir / src if image_dir else Path(src)
        properties = ' properties="cover-image"' if src == cover_image else ""
        manifest.append(
            f'<item id="image-{idx:03d}" href="images/{html.escape(src, quote=True)}" '
            f'media-type="{media_type(source_path)}"{properties}/>'
        )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(author)}</dc:creator>
    <dc:language>{language}</dc:language>
  </metadata>
  <manifest>
    {chr(10).join(manifest)}
  </manifest>
  <spine toc="ncx">
    {chr(10).join(spine)}
  </spine>
</package>
'''


def write_epub(build_dir: Path, output: Path) -> None:
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w") as epub:
        epub.write(build_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(build_dir.rglob("*")):
            if path.is_file() and path.name != "mimetype":
                epub.write(path, path.relative_to(build_dir), compress_type=zipfile.ZIP_DEFLATED)


def read_line_set(path: Path | None) -> set[str]:
    if not path:
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def read_title_fixes(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    fixes: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        old, sep, new = line.partition("=>")
        if sep:
            fixes[old.strip()] = new.strip()
    return fixes


def build(args: argparse.Namespace) -> None:
    source_md = args.input.resolve()
    output = args.output.resolve()
    build_dir = args.build_dir.resolve()
    oebps = build_dir / "OEBPS"
    xhtml_dir = oebps / "xhtml"
    images_dir = oebps / "images"
    meta_inf = build_dir / "META-INF"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    xhtml_dir.mkdir(parents=True)
    images_dir.mkdir(parents=True)
    meta_inf.mkdir(parents=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    chapters, image_refs = convert_lines(
        source_md.read_text(encoding="utf-8").splitlines(),
        image_prefix=args.image_prefix,
        skip_lines=read_line_set(args.skip_lines),
        title_fixes=read_title_fixes(args.title_fixes),
        promote_to_chapter=read_line_set(args.promote_to_chapter),
    )
    link_printed_contents(chapters)

    cover_src: str | None = None
    if args.cover_image:
        cover_src = f"cover{args.cover_image.suffix.lower()}"
        shutil.copy2(args.cover_image.resolve(), images_dir / cover_src)
        image_refs.add(cover_src)

    (xhtml_dir / "cover.xhtml").write_text(
        build_cover_page(args.title, args.language, cover_src), encoding="utf-8"
    )
    for chapter in chapters:
        (xhtml_dir / chapter.filename).write_text(chapter_xhtml(chapter, args.language), encoding="utf-8")

    if args.image_dir:
        for src in image_refs:
            src_path = args.image_dir / src
            if src_path.exists():
                target = images_dir / src
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, target)

    (build_dir / "mimetype").write_text("application/epub+zip", encoding="ascii")
    (meta_inf / "container.xml").write_text(
        '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
''',
        encoding="utf-8",
    )
    (oebps / "styles.css").write_text(Path(args.css).read_text(encoding="utf-8"), encoding="utf-8")
    book_id = f"urn:uuid:{uuid.uuid4()}"
    (oebps / "nav.xhtml").write_text(build_nav(chapters, args.language), encoding="utf-8")
    (oebps / "toc.ncx").write_text(build_ncx(chapters, book_id, args.title, args.author), encoding="utf-8")
    (oebps / "content.opf").write_text(
        build_opf(
            chapters,
            image_refs,
            book_id,
            title=args.title,
            author=args.author,
            language=args.language,
            image_dir=args.image_dir,
            cover_image=cover_src,
        ),
        encoding="utf-8",
    )
    write_epub(build_dir, output)
    print(f"Wrote {output}")
    print(f"Chapters: {len(chapters)}")
    print(f"Images referenced: {len(image_refs)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="PDF-to-Markdown export to convert")
    parser.add_argument("--output", required=True, type=Path, help="Output EPUB path")
    parser.add_argument("--title", required=True, help="Book title metadata")
    parser.add_argument("--author", default="Unknown", help="Book author metadata")
    parser.add_argument("--language", default="en", help="BCP 47 language code, e.g. en or zh-CN")
    parser.add_argument("--image-dir", type=Path, help="Directory containing extracted images")
    parser.add_argument("--cover-image", type=Path, help="Optional cover image")
    parser.add_argument("--image-prefix", default="imgs", help="Markdown image path prefix to strip")
    parser.add_argument("--build-dir", default=Path("build/epub"), type=Path, help="Temporary EPUB build directory")
    parser.add_argument("--css", default=Path("assets/default.css"), type=Path, help="CSS file to embed")
    parser.add_argument("--skip-lines", type=Path, help="Optional newline-delimited OCR noise lines to drop")
    parser.add_argument("--title-fixes", type=Path, help="Optional title fixes file: old => new")
    parser.add_argument("--promote-to-chapter", type=Path, help="Optional newline-delimited headings to promote")
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
