# PDF/OCR Markdown to EPUB Workflow

Build a readable EPUB from a PDF-to-Markdown or OCR export.

This repository contains only the conversion workflow. It does not include
books, PDF files, extracted OCR text, extracted images, or generated EPUBs.
Use it only with material you have the right to process.

## What It Does

- Optionally runs PaddleOCR-VL on a machine that has the model installed.
- Splits a Markdown export into EPUB chapters using level-one headings.
- Removes PDF page marker lines such as `--- Page 12 / 300 ---`.
- Joins OCR-wrapped lines back into paragraphs.
- Preserves simple headings, centered lines, and image references.
- Converts Markdown-exported footnote markers like `$ ^{1} $` to superscripts.
- Creates EPUB navigation, OPF metadata, NCX table of contents, CSS, and zip packaging.
- Converts a printed `Contents` / `目录` chapter into real links to chapter and section anchors.
- Preserves fenced code blocks so comment lines are not mistaken for headings.
- Detects numbered page-bottom notes that match nearby superscript references and emits linked EPUB footnotes.
- Keeps page-bottom footnote metadata from splitting a sentence that continues onto the next PDF page.
- Supports tab-separated footnote repairs recovered from the original PDF text layer when OCR omits a note.
- Converts inline and display LaTeX to EPUB-native MathML, including the required OPF `mathml` property.
- Distinguishes LaTeX dollar delimiters from R operators such as `%$%` and `data$column`.
- Removes PDF page markers without forcing a paragraph break when prose clearly continues across pages.
- Supports optional cleanup files for OCR noise, title fixes, and heading promotion.
- Provides a resume-safe page-JPEG runner for large books and 8 GB GPUs.
- Preserves confirmed GPU-OOM pages as full-page images instead of dropping them.
- Supports Markdown image fallbacks, OCR HTML tables, and an optional real cover image.

## Quick Start

Install the lightweight EPUB-builder dependency:

```bash
python3 -m pip install -r requirements.txt
```

Run the EPUB sample:

```bash
python3 src/build_epub.py \
  --input examples/sample.md \
  --output dist/sample.epub \
  --title "A Small Sample" \
  --author "Example Author" \
  --skip-lines examples/skip-lines.txt
```

If OCR captured a superscript reference but dropped its page-bottom note, recover
the note from the original PDF text layer and add a UTF-8 tab-separated repair
file:

```text
# PDF page<TAB>marker<TAB>recovered note text
23	8	One of the things implied by ceteris paribus ...
```

Then add `--footnote-fixes footnote-fixes.tsv`. Repairs are accepted only as
explicit source-backed input; the builder does not invent missing note text.

## OCR Model Downloads

The OCR step uses two PaddlePaddle models:

- PaddleOCR-VL: <https://huggingface.co/PaddlePaddle/PaddleOCR-VL>
- PP-DocLayoutV2: <https://huggingface.co/PaddlePaddle/PP-DocLayoutV2>

Install the Hugging Face CLI, then download the model files into predictable
local directories:

```bash
python3 -m pip install -U "huggingface_hub[cli]"

huggingface-cli download PaddlePaddle/PaddleOCR-VL \
  --local-dir ~/.paddlex/official_models/PaddleOCR-VL

huggingface-cli download PaddlePaddle/PP-DocLayoutV2 \
  --local-dir ~/.paddlex/official_models/PP-DocLayoutV2
```

These model files are large generated artifacts and should not be committed to
your repository.

Run PaddleOCR-VL first, if your PDF needs OCR:

```bash
python3 src/ocr_paddle_vl.py \
  --input ~/my_pdfs/book.pdf \
  --output-dir ~/ocr_results/book \
  --layout-model-dir ~/.paddlex/official_models/PP-DocLayoutV2 \
  --vl-model-dir ~/.paddlex/official_models/PaddleOCR-VL \
  --json
```

For large books, render one JPEG per page and use the resume-safe runner:

```bash
pdftoppm -jpeg -r 170 -jpegopt quality=88,progressive=y,optimize=y \
  book.pdf ~/book-images/page

python3 src/ocr_pages_stream.py \
  --input-dir ~/book-images \
  --output-dir ~/ocr_results/book \
  --layout-model-dir ~/.paddlex/official_models/PP-DocLayoutV2 \
  --vl-model-dir ~/.paddlex/official_models/PaddleOCR-VL
```

The runner writes each page immediately, resumes from existing Markdown/JSON pairs,
and uses a full-page image only when Paddle reports a GPU out-of-memory error.

## OS Support Notes

The EPUB builder is plain Python and should work on macOS, Linux, and Windows.

The local PaddleOCR-VL OCR step is more restrictive. For GPU OCR, prefer Linux,
WSL2 on Windows, or a Docker container with NVIDIA GPU access. Native Windows
is not the recommended path for this doc-parser workflow; the PP-DocLayoutV2
model card specifically tells Windows users to use WSL or Docker. macOS can run
the EPUB builder comfortably, but it is usually not the right machine for this
PaddlePaddle GPU OCR stack.

Before running heavy OCR, check whether the PDF already has a usable text layer:

```bash
pdfinfo book.pdf | sed -n '1,80p'
pdftotext -layout -f 1 -l 3 book.pdf - | sed -n '1,120p'
```

If this produces clean text, you may only need text cleanup plus EPUB packaging.

Run your own export:

```bash
python3 src/build_epub.py \
  --input ~/ocr_results/book/combined.md \
  --output dist/book.epub \
  --title "Book Title" \
  --author "Author Name" \
  --image-dir path/to/imgs \
  --cover-image path/to/cover.jpg
```

## Optional Cleanup Files

`--skip-lines` accepts one exact line per row. Matching lines are dropped.

```text
OCR NOISE LINE
Another repeated artifact
```

`--title-fixes` accepts one replacement per row:

```text
Bad OCR Chapter Tltle => Bad OCR Chapter Title
```

`--promote-to-chapter` accepts one heading title per row. Matching non-H1
headings are promoted into EPUB chapters.

```text
Appendix A
Bibliography
```

## Repository Hygiene

The included `.gitignore` is intentionally strict. It excludes common source
book formats, generated EPUBs, OCR exports, extracted images, and build
directories.

Before publishing a repository, check that no copyrighted source material is
tracked:

```bash
git status --short
git ls-files
```

## Reader Acceptance Checks

XML validity is necessary but not sufficient. Before delivery, verify all of the following:

- The reader's navigation panel lists the intended chapters and omits synthetic `Front Matter` labels.
- The printed Contents page is visually separated into entries, strips obsolete print page numbers/leaders,
  uses consistent chapter/subsection indentation, and makes every expected entry clickable.
- Every internal link resolves to an existing XHTML file and `id` anchor.
- Fenced code remains a code block; `#` comments do not become headings.
- Page-bottom notes render smaller than body text and their reference/back links work. Audit unresolved
  superscripts; recover OCR-omitted note text from the source PDF into `--footnote-fixes` rather than inventing it.
- Mid-paragraph PDF page boundaries do not create artificial blank lines in reflowable text, including when
  page-bottom note text appears between the two halves of the sentence.
- Inline, fractional, and multiline formulas render as MathML rather than visible `$` delimiters and LaTeX commands;
  formula-bearing XHTML manifest items declare the `mathml` property.
- Tables fit the viewport, images are not clipped, and long code can wrap or scroll.
- Delete an older import before checking in Apple Books because it can cache prior navigation and covers.

Run the regression test before publishing changes:

```bash
python3 -m unittest discover -s tests -v
```

## Notes

Different PDF-to-Markdown tools emit different HTML snippets and image paths.
This workflow is a small, hackable baseline rather than a universal EPUB
typesetting engine. The safest extension pattern is to add converter-specific
normalizers while keeping source books and generated outputs out of Git.

For a Windows GPU backend, see [docs/windows-wsl-ocr.md](docs/windows-wsl-ocr.md).
