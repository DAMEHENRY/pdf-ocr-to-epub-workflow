# Windows/WSL PaddleOCR-VL Backend

This workflow is useful when your main laptop is comfortable for editing, but a
Windows desktop or gaming laptop has the NVIDIA GPU needed for OCR.

## Architecture

```text
Mac or Linux laptop
  -> SSH into Windows
  -> enter WSL2 Ubuntu
  -> activate a Python environment
  -> run PaddleOCR-VL on local GPU/model files
  -> copy Markdown output back to the editing machine
```

## WSL Setup Shape

Inside WSL, keep the paths boring:

```text
~/paddle_env/       # Python virtual environment
~/my_pdfs/          # input PDFs
~/ocr_results/      # OCR output
~/.paddlex/official_models/
  PP-DocLayoutV2/
  PaddleOCR-VL/
```

The exact install commands depend on your GPU driver, CUDA, PaddlePaddle, and
PaddleOCR versions. Follow the official PaddleOCR/PaddleX documentation for
installation, then use this repository's scripts as orchestration glue.

## Model Downloads

Download these two official Hugging Face repositories inside WSL:

- PaddleOCR-VL: <https://huggingface.co/PaddlePaddle/PaddleOCR-VL>
- PP-DocLayoutV2: <https://huggingface.co/PaddlePaddle/PP-DocLayoutV2>

```bash
python3 -m pip install -U "huggingface_hub[cli]"

mkdir -p ~/.paddlex/official_models

huggingface-cli download PaddlePaddle/PaddleOCR-VL \
  --local-dir ~/.paddlex/official_models/PaddleOCR-VL

huggingface-cli download PaddlePaddle/PP-DocLayoutV2 \
  --local-dir ~/.paddlex/official_models/PP-DocLayoutV2
```

If your PaddleOCR version expects the inner VLM folder directly, point
`--vl-model-dir` at the downloaded subdirectory that contains the
`PaddleOCR-VL-0.9B` files. Otherwise, the top-level
`~/.paddlex/official_models/PaddleOCR-VL` path is the usual starting point.

Do not commit model weights, OCR outputs, PDFs, or generated EPUBs.

## Operating System Limits

This workflow has two different platform profiles:

- `src/build_epub.py`: plain Python EPUB packaging; usable on macOS, Linux, and Windows.
- `src/ocr_paddle_vl.py`: PaddleOCR-VL document parsing; best run on Linux,
  WSL2, or Docker with NVIDIA GPU access.

Native Windows is not the recommended target for the local doc-parser stack.
The PP-DocLayoutV2 Hugging Face model card explicitly says Windows users should
use WSL or a Docker container. macOS is fine as the editing/orchestration
machine, but it is not a good default for this PaddlePaddle GPU OCR backend.

## Running OCR In WSL

```bash
source ~/paddle_env/bin/activate
nvidia-smi

python3 src/ocr_paddle_vl.py \
  --input ~/my_pdfs/book.pdf \
  --output-dir ~/ocr_results/book \
  --layout-model-dir ~/.paddlex/official_models/PP-DocLayoutV2 \
  --vl-model-dir ~/.paddlex/official_models/PaddleOCR-VL \
  --json
```

If you run from this repository on the remote machine, use repository-relative
paths. If the repository only exists on your laptop, copy `src/ocr_paddle_vl.py`
to WSL first.

## SSH Notes

For a Windows host reached over Tailscale or LAN:

```bash
ssh windows-user@100.x.y.z
```

Then enter WSL:

```powershell
wsl
```

Windows OpenSSH behaves differently for administrator accounts. It may read
authorized keys from:

```text
C:\ProgramData\ssh\administrators_authorized_keys
```

rather than:

```text
C:\Users\<user>\.ssh\authorized_keys
```

If you add a temporary key, tag it with a clear comment and remove it when the
run is finished.

## Avoid Long Inline Commands

Mac -> SSH -> Windows PowerShell -> WSL -> bash quoting gets fragile quickly.
Prefer a short script file in WSL:

```bash
#!/usr/bin/env bash
set -euo pipefail
source ~/paddle_env/bin/activate
python3 ~/pdf-md-to-epub-workflow/src/ocr_paddle_vl.py \
  --input "$1" \
  --output-dir "$2" \
  --layout-model-dir ~/.paddlex/official_models/PP-DocLayoutV2 \
  --vl-model-dir ~/.paddlex/official_models/PaddleOCR-VL \
  --json
```

Then call the wrapper with simple arguments.

## First Check: Does The PDF Already Have Text?

Before launching heavy OCR, check whether the PDF has a usable text layer:

```bash
pdfinfo book.pdf | sed -n '1,80p'
pdftotext -layout -f 1 -l 3 book.pdf - | sed -n '1,120p'
```

If `pdftotext` already gives clean text, you may only need light cleanup and
EPUB packaging. Full OCR is most valuable for scanned pages, diagrams, tables,
formula-heavy pages, and books whose text layer is missing or unusable.

## References

- PaddleOCR-VL docs: <https://paddlepaddle.github.io/PaddleX/3.5/en/pipeline_usage/tutorials/ocr_pipelines/PaddleOCR-VL.html>
- PaddleOCR repository: <https://github.com/PaddlePaddle/PaddleOCR>
