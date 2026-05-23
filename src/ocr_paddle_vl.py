#!/usr/bin/env python3
"""Convert a PDF or image folder to Markdown with PaddleOCR-VL.

Run this inside the machine that has PaddleOCR-VL installed, commonly a Linux
or WSL2 environment with NVIDIA GPU access. The script does not download or
ship any model weights; pass local model directories when you want fully
offline inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="PDF, image, or directory to parse")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for Markdown/JSON/images")
    parser.add_argument("--layout-model-dir", type=Path, help="Local PP-DocLayoutV2 model directory")
    parser.add_argument("--vl-model-dir", type=Path, help="Local PaddleOCR-VL model directory")
    parser.add_argument("--device", default="gpu", help="Paddle device, e.g. gpu or cpu")
    parser.add_argument("--no-layout", action="store_true", help="Disable layout detection")
    parser.add_argument("--chart", action="store_true", help="Enable chart recognition")
    parser.add_argument("--doc-preprocessor", action="store_true", help="Enable document preprocessing")
    parser.add_argument("--pretty", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json", action="store_true", help="Also save raw JSON results")
    parser.add_argument("--images", action="store_true", help="Also save visualization images")
    return parser.parse_args()


def maybe_path(path: Path | None) -> str | None:
    return str(path.expanduser().resolve()) if path else None


def load_pipeline(args: argparse.Namespace) -> Any:
    try:
        from paddleocr import PaddleOCRVL
    except ImportError as exc:
        raise SystemExit(
            "Could not import PaddleOCRVL. Install PaddleOCR/PaddleX in this environment first."
        ) from exc

    kwargs = {
        "layout_detection_model_dir": maybe_path(args.layout_model_dir),
        "vl_rec_model_dir": maybe_path(args.vl_model_dir),
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    return PaddleOCRVL(**kwargs)


def main() -> None:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    markdown_dir = output_dir / "markdown"
    json_dir = output_dir / "json"
    image_dir = output_dir / "visualizations"

    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)
    if args.json:
        json_dir.mkdir(parents=True, exist_ok=True)
    if args.images:
        image_dir.mkdir(parents=True, exist_ok=True)

    pipeline = load_pipeline(args)
    results = pipeline.predict(
        str(input_path),
        use_doc_preprocessor=args.doc_preprocessor,
        use_layout_detection=not args.no_layout,
        use_chart_recognition=args.chart,
    )

    results = list(results)
    combined_md: list[str] = []
    combined_json: list[Any] = []
    temp_md = output_dir / "_temp_page.md"
    temp_json = output_dir / "_temp_page.json"

    for idx, result in enumerate(results):
        combined_md.append(f"\n\n--- Page {idx + 1} / {len(results)} ---\n\n")
        result.save_to_markdown(save_path=str(temp_md), pretty=args.pretty)
        combined_md.append(temp_md.read_text(encoding="utf-8"))
        page_md = markdown_dir / f"page-{idx + 1:04d}.md"
        page_md.write_text(combined_md[-1], encoding="utf-8")

        if args.json:
            result.save_to_json(save_path=str(temp_json))
            page_json = json.loads(temp_json.read_text(encoding="utf-8"))
            combined_json.append(page_json)
            (json_dir / f"page-{idx + 1:04d}.json").write_text(
                json.dumps(page_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if args.images:
            page_image_dir = image_dir / f"page-{idx + 1:04d}"
            page_image_dir.mkdir(parents=True, exist_ok=True)
            result.save_to_img(save_path=str(page_image_dir))

    temp_md.unlink(missing_ok=True)
    temp_json.unlink(missing_ok=True)

    (output_dir / "combined.md").write_text("".join(combined_md), encoding="utf-8")
    if combined_json:
        (output_dir / "combined.json").write_text(
            json.dumps(combined_json, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    if not combined_md:
        raise SystemExit("No Markdown files were produced; check the PaddleOCR-VL logs above.")

    print(f"Wrote OCR output to {output_dir}")
    print(f"Combined Markdown: {output_dir / 'combined.md'}")


if __name__ == "__main__":
    main()
