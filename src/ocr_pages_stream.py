#!/usr/bin/env python3
"""Resume-safe PaddleOCR-VL OCR for a directory of one-page JPEGs."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--layout-model-dir", required=True, type=Path)
    parser.add_argument("--vl-model-dir", required=True, type=Path)
    return parser.parse_args()


def rebuild_combined(output_dir: Path, total: int) -> None:
    parts: list[str] = []
    for page_md in sorted((output_dir / "markdown").glob("page-*.md")):
        page_number = int(page_md.stem.split("-")[-1])
        parts.append(f"\n\n--- Page {page_number} / {total} ---\n\n")
        parts.append(page_md.read_text(encoding="utf-8"))
    (output_dir / "combined.md").write_text("".join(parts), encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    markdown_dir = output_dir / "markdown"
    json_dir = output_dir / "json"
    image_dir = output_dir / "imgs"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    pages = sorted(input_dir.glob("page-*.jpg"))
    if not pages:
        raise SystemExit(f"No page JPEGs found in {input_dir}")

    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(
        layout_detection_model_dir=str(args.layout_model_dir.expanduser().resolve()),
        vl_rec_model_dir=str(args.vl_model_dir.expanduser().resolve()),
    )

    total = len(pages)
    completed = 0
    for page_number, page_path in enumerate(pages, 1):
        page_md = markdown_dir / f"page-{page_number:04d}.md"
        page_json = json_dir / f"page-{page_number:04d}.json"
        if page_md.exists() and page_json.exists():
            completed += 1
            print(f"SKIP {page_number}/{total}", flush=True)
            continue

        print(f"START {page_number}/{total} {page_path.name}", flush=True)
        try:
            results = list(
                pipeline.predict(
                    str(page_path),
                    use_doc_preprocessor=False,
                    use_layout_detection=True,
                    use_chart_recognition=False,
                )
            )
        except RuntimeError as exc:
            message = str(exc)
            if "Out of memory" not in message and "ResourceExhaustedError" not in message:
                raise
            fallback_image = image_dir / f"page-{page_number:04d}.jpg"
            shutil.copy2(page_path, fallback_image)
            page_md.write_text(
                f"![Page {page_number}](imgs/{fallback_image.name})\n",
                encoding="utf-8",
            )
            page_json.write_text(
                json.dumps(
                    {
                        "page": page_number,
                        "fallback": "full-page-image",
                        "reason": "paddle-gpu-oom",
                        "source": page_path.name,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            completed += 1
            print(f"FALLBACK {page_number}/{total} full-page-image (GPU OOM)", flush=True)
            if completed % 10 == 0:
                rebuild_combined(output_dir, total)
            continue

        if len(results) != 1:
            raise RuntimeError(f"Expected one result for {page_path}, got {len(results)}")

        result = results[0]
        temp_md = output_dir / f"_page-{page_number:04d}.md"
        temp_json = output_dir / f"_page-{page_number:04d}.json"
        result.save_to_markdown(save_path=str(temp_md), pretty=True)
        result.save_to_json(save_path=str(temp_json))
        temp_md.replace(page_md)
        raw_json = json.loads(temp_json.read_text(encoding="utf-8"))
        page_json.write_text(
            json.dumps(raw_json, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temp_json.unlink(missing_ok=True)
        completed += 1
        print(f"DONE {page_number}/{total}", flush=True)
        if completed % 10 == 0:
            rebuild_combined(output_dir, total)

    rebuild_combined(output_dir, total)
    print(f"COMPLETE {completed}/{total}", flush=True)
    print(f"Combined Markdown: {output_dir / 'combined.md'}", flush=True)


if __name__ == "__main__":
    main()
