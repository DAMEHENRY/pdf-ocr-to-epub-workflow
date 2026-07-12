from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_epub", ROOT / "src" / "build_epub.py")
assert SPEC and SPEC.loader
BUILD_EPUB = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILD_EPUB
SPEC.loader.exec_module(BUILD_EPUB)


class BuilderRegressionTests(unittest.TestCase):
    def test_printed_contents_links_and_code_fences(self) -> None:
        markdown = """# Contents

Chapter One 1
Section A 2

# Chapter One

## Section A

```r
# This is code, not a heading
x <- 1
```
"""
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "book.md"
            source.write_text(markdown, encoding="utf-8")
            args = type("Args", (), {
                "input": source,
                "output": temp_path / "book.epub",
                "build_dir": temp_path / "build",
                "title": "Book",
                "author": "Author",
                "language": "en",
                "image_dir": None,
                "cover_image": None,
                "image_prefix": "imgs",
                "css": ROOT / "assets" / "default.css",
                "skip_lines": None,
                "title_fixes": None,
                "promote_to_chapter": None,
            })()
            BUILD_EPUB.build(args)

            with zipfile.ZipFile(args.output) as epub:
                contents = epub.read("OEBPS/xhtml/chapter-001-contents.xhtml").decode()
                chapter = epub.read("OEBPS/xhtml/chapter-002-chapter-one.xhtml").decode()
                nav = epub.read("OEBPS/nav.xhtml").decode()
                self.assertIn('href="chapter-002-chapter-one.xhtml#chapter-one"', contents)
                self.assertIn('href="chapter-002-chapter-one.xhtml#section-a"', contents)
                self.assertIn('class="printed-toc-entry toc-level-1"', contents)
                self.assertIn('class="printed-toc-entry toc-level-2"', contents)
                self.assertNotIn("Chapter One 1", contents)
                self.assertNotIn("Section A 2", contents)
                self.assertIn('<pre><code class="language-r"># This is code, not a heading', chapter)
                self.assertNotIn("This is code, not a heading</a>", nav)
                for name in epub.namelist():
                    if name.endswith((".xhtml", ".opf", ".ncx")):
                        ET.fromstring(epub.read(name))

    def test_page_footnote_is_linked_and_not_body_text(self) -> None:
        markdown = """--- Page 1 / 2 ---

# Chapter One

Body text with a note. $ ^{4} $

4 This is the page-bottom footnote, not ordinary body text.

--- Page 2 / 2 ---

More body text.
"""
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source = temp_path / "book.md"
            source.write_text(markdown, encoding="utf-8")
            args = type("Args", (), {
                "input": source,
                "output": temp_path / "book.epub",
                "build_dir": temp_path / "build",
                "title": "Book", "author": "Author", "language": "en",
                "image_dir": None, "cover_image": None, "image_prefix": "imgs",
                "css": ROOT / "assets" / "default.css", "skip_lines": None,
                "title_fixes": None, "promote_to_chapter": None,
            })()
            BUILD_EPUB.build(args)
            with zipfile.ZipFile(args.output) as epub:
                chapter = epub.read("OEBPS/xhtml/chapter-001-chapter-one.xhtml").decode()
                self.assertIn('epub:type="noteref"', chapter)
                self.assertIn('href="#fn-p1-4"', chapter)
                self.assertIn('epub:type="footnote" id="fn-p1-4"', chapter)
                self.assertIn('href="#fnref-p1-4"', chapter)
                self.assertNotIn("<p>4 This is the page-bottom footnote", chapter)
                ET.fromstring(chapter)

    def test_numbered_code_is_not_misclassified_as_footnote(self) -> None:
        lines = ["--- Page 1 / 1 ---", "Body reference. $ ^{9} $", "9 egen mean_x=mean(x), by(id)"]
        bodies, links = BUILD_EPUB.analyze_footnotes(lines)
        self.assertEqual(bodies, {})
        self.assertEqual(links, {})


if __name__ == "__main__":
    unittest.main()
