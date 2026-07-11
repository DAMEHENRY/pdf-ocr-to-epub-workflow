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

## Do Not Rely on WSL `nohup` Across Windows SSH

On some Windows OpenSSH setups, a detached Linux process is still tied to the
`wsl.exe` job created by the SSH session. When that Windows-side job exits, WSL
may stop even though the command used `nohup`.

For full books, prefer a Windows Scheduled Task that launches a short WSL
wrapper under the logged-in Windows account. Keep stdout and stderr in a
book-specific `stream.log`, verify the Windows task and Linux process once, and
remove the task after a successful exit.

## Large PDFs and 8 GB GPUs

Do not pass a full several-hundred-page scanned PDF to `pipeline.predict()` as
one object. Render it to `page-*.jpg` files and run `src/ocr_pages_stream.py`.
The page-level output provides deterministic resume behavior and lets dense code
or formula pages fall back to an exact full-page image only when GPU OOM is
confirmed. Other exceptions remain fatal and visible.

Windows SFTP cannot read the WSL `/home` tree directly. Tar the result to
`/mnt/c/Users/<user>/` before retrieving it with SCP.

## Production Runbook

### 1. Preflight the source and remote host

Check the PDF before committing GPU time:

```bash
pdfinfo book.pdf
pdftotext -layout -f 1 -l 5 book.pdf - | sed -n '1,160p'
```

If the text layer is clean, direct extraction may be preferable. Full OCR is
still useful when formulas, code, tables, or figures require layout parsing.

Before transfer, confirm that SSH works, WSL starts, `nvidia-smi` sees the GPU,
the model directories exist, and the target disk has enough free space. Inspect
shared input directories before running a legacy batch script: a glob such as
`~/my_pdfs/*.pdf` can unintentionally reprocess old books.

### 2. Render one deterministic image per page

```bash
mkdir -p /tmp/book-images
pdftoppm -jpeg -r 170 -jpegopt quality=88,progressive=y,optimize=y \
  book.pdf /tmp/book-images/page
```

Keep the generated names in lexical page order (`page-001.jpg`, etc.). Verify
the rendered image count against `pdfinfo` before transfer.

### 3. Run the resume-safe page worker

```bash
source ~/paddle_env/bin/activate
python ~/pdf-ocr-to-epub-workflow/src/ocr_pages_stream.py \
  --input-dir /path/to/book-images \
  --output-dir ~/ocr_results/book \
  --layout-model-dir ~/.paddlex/official_models/PP-DocLayoutV2 \
  --vl-model-dir ~/.paddlex/official_models/PaddleOCR-VL \
  >> ~/ocr_results/book/stream.log 2>&1
```

The worker considers a page complete only when both its Markdown and JSON files
exist. It rebuilds `combined.md` periodically, so a stopped run can resume
without repeating completed pages.

Only a confirmed Paddle `ResourceExhaustedError`/GPU OOM activates the full-page
image fallback. This is intentional for dense code, formula, or diagram pages:
preserving the exact page is safer than silently dropping it or returning
corrupted OCR. Other exceptions remain fatal.

### 4. Let Windows own unattended runs

Create a short WSL wrapper for the command above, then register a book-specific
Windows Scheduled Task whose action is similar to:

```text
C:\Windows\System32\wsl.exe -e bash /mnt/c/Users/<user>/run-book-ocr.sh
```

Recommended task properties:

- Principal: `<COMPUTERNAME>\<USERNAME>`
- Logon type: `Interactive`
- Execution limit: long enough for the book
- Allow start on battery and do not stop solely because power changes

Verify both layers once:

```powershell
Get-ScheduledTask -TaskName "BookOCR"
Get-ScheduledTaskInfo -TaskName "BookOCR"
```

```bash
pgrep -af ocr_pages_stream.py
tail -n 30 ~/ocr_results/book/stream.log
```

Remove the task after it reaches `LastTaskResult = 0`.

### 5. Retrieve across the Windows/WSL boundary

Windows OpenSSH serves the Windows filesystem, not the WSL `/home` tree. Stage
an archive in the mounted Windows user directory:

```bash
tar -C ~/ocr_results -czf /mnt/c/Users/<user>/book-ocr.tar.gz book
```

Retrieve that archive with SCP and extract it on the editing machine.

### 6. Audit before EPUB packaging

Do not stop at `COMPLETE N/N`. Check:

- Markdown and JSON counts match the PDF page count.
- Page markers are continuous.
- Every referenced image exists.
- Full-page fallbacks are listed and visually justified.
- Real chapter headings are promoted and code/comments are not fake chapters.
- EPUB XHTML/OPF/NCX parse as XML.
- `mimetype` is first and uncompressed, manifest targets exist, and the ZIP
  passes an integrity test.

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
