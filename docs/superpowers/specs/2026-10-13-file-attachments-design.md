# File attachments in Nova's window

Status: design, awaiting review · 2026-10-13

## Goal

Let the owner hand files to Nova in the desktop window, by attach button,
drag and drop, or pasting a screenshot, and have Nova read the popular file
types and look at pictures. Done when a PDF, a spreadsheet or a photo can be
attached in any of those three ways, and "what's in this?", "summarise it" or
"what does this screenshot say?" gets a correct answer.

## Scope

In:

- The tkinter window (`nova/interfaces/gui.py`): attach button, drag and
  drop, Cmd+V / Ctrl+V paste of an image, removable chips.
- `read_file` learns PDF, Word, Excel and PowerPoint; on macOS also `.doc`,
  `.rtf` and `.odt`.
- A new `view_image` tool that sends a picture file to the vision model.
- macOS first. The attach button and paste must also work on Windows; drag
  and drop is verified on macOS only.

Out (each can be its own project later):

- Nova uploading files elsewhere (Google Drive, websites, share links).
- Attachments over Discord or in the CLI. Both still benefit from the new
  `read_file` formats and `view_image`, because those are ordinary tools.
- OCR of scanned PDFs, and audio or video files.

## Decisions

- **Nova reads files on demand.** A message carries only a list of the
  attached paths. The model opens what it needs with `read_file` or
  `view_image`, so a large file costs nothing until it is used, long
  documents page as they do today, and image questions are specific.
- **Originals, not copies.** Attachments are the user's real files, so
  "rename these photos" renames them; changes still go through the usual
  approval prompt. Only pasted screenshots, which have no original, are
  written by Nova.

## Components

| Unit | Job | Depends on |
|---|---|---|
| `nova/attachments.py` (new) | Classify a path, format the "Attached files" list, save a pasted image, prune old pastes. No UI. | `config.DATA_ROOT`, Pillow |
| `nova/tools/documents.py` (new) | `extract_text(path)` for document formats; registers `view_image`. | pypdf, python-docx, openpyxl, python-pptx, pillow-heif (each optional), `LLM.look` |
| `nova/tools/files.py` | `read_file` calls `extract_text` for document formats, refuses images and binaries with a helpful message. | `documents.extract_text` |
| `nova/tools/screen.py` | Its downscale-and-encode helper becomes public (`fit_for_vision`) so `view_image` reuses it. | Pillow |
| `nova/interfaces/gui.py` | Button, chips, drag and drop, paste, composing the message. | `attachments`, tkinterdnd2 (optional) |

The agent loop and the model's message format are unchanged: attachments
arrive as text in the user's message.

## Behaviour

### Adding files

- **📎 button** next to Stop and Send: the system file picker, several files
  at once.
- **Drag and drop** of files or folders anywhere on the window. While a drag
  is over the window the status bar reads "Drop to attach". Needs
  tkinterdnd2; without it the window is a plain `tk.Tk` and drag and drop is
  simply absent.
- **Cmd+V** (macOS) / **Ctrl+V** (elsewhere) in the message box: if the
  clipboard holds an image (`PIL.ImageGrab.grabclipboard`), it is saved as
  `paste-YYYYMMDD-HHMMSS.png` (with `-2`, `-3` on a clash) in the
  attachments folder and attached; if it holds file paths, those are
  attached; otherwise the normal text paste happens.
- Attachments can be added while Nova is working; they go with the next
  message. The same path twice keeps one chip. At most 50 per message;
  extra files are left out and the status bar says so.

### Chips

A row above the message box, hidden when empty: `📎 name ✕` per file,
clicking ✕ removes it. After six chips the rest collapse into "+N more".

### Sending

The instruction sent to the agent is the typed text, or "Take a look at the
attached files." if nothing was typed, followed by:

```
Attached files (open them with read_file, or view_image for pictures):
- /Users/victor/Downloads/report.pdf (PDF document, 2.1 MB)
- /Users/victor/Pictures/IMG_2041.heic (image, 3.4 MB)
- /Users/victor/Desktop/receipts/ (folder)
```

Kinds: `image` (png, jpg, jpeg, heic, heif, webp, gif, tiff, tif, bmp),
`PDF document`, `Word document` (docx, doc), `Excel workbook` (xlsx, xlsm),
`PowerPoint deck` (pptx), `document` (rtf, odt), `folder`, otherwise `file`.
Sizes are rounded to one decimal in KB, MB or GB.

The chips clear, and the transcript shows the message followed by a muted
line: `📎 report.pdf, IMG_2041.heic, receipts`.

### Attachments folder

`DATA_ROOT / "attachments"`: `attachments/` in a checkout (added to
`.gitignore`), `~/.nova/attachments` in the packaged app. Only pasted images
are written there, and only the 50 most recent `paste-*.png` are kept.

## Reading files

`read_file(path, start_line, max_lines)` keeps its signature and its numbered,
pageable output. For document formats the text comes from `extract_text`:

| Format | Text produced |
|---|---|
| PDF | Each page's text after a `--- page N ---` line. An encrypted PDF is first opened with an empty password, which many "protected" PDFs accept. |
| `.docx` | Paragraphs and tables in document order; table rows as `cell \| cell \| cell`. |
| `.xlsx`, `.xlsm` | Per sheet, `=== Sheet: name ===`, then one line per row of cell values (not formulas) joined with ` \| `, trailing empty cells dropped, at most 5,000 rows per sheet with a note when cut. |
| `.pptx` | Per slide, `--- slide N ---`, the text of every shape (including tables and groups), then `Notes:` and the speaker notes. |
| `.doc`, `.rtf`, `.odt` | macOS only, via `textutil -convert txt -stdout`. Elsewhere: an error suggesting conversion to `.docx`. |

The header line names the format, e.g.
`/path/report.pdf  (PDF, 12 pages, 840 lines total)`.

The last four extractions are cached by (path, modification time, size), so
paging through a long document does not re-extract it each time.

Images given to `read_file` return a pointer to `view_image`. Other files
whose first 8 KB contain a NUL byte return `binary file (.mp4, 40.2 MB) -
read_file can only show text`, not decoded garbage.

## `view_image(path, question)`

- `question` defaults to "Describe this image in detail, including any text
  in it."
- Opens png, jpg, jpeg, heic, heif (via pillow-heif), webp, gif (first frame),
  tiff and bmp; applies EXIF orientation; converts to RGB; downscales and
  encodes with `fit_for_vision`; asks `LLM.look`.
- Returns `[image 4032x3024, /path/IMG_2041.heic]` followed by the answer.
- Read-only, so never asks for approval. Registered whenever Pillow is
  available, independently of screen capture.

## Errors

Every failure comes back to the model as a sentence it can relay:

- Missing file: the existing "does not exist".
- Missing optional library: "reading .docx needs the python-docx package".
- Encrypted PDF that the empty password does not open: "report.pdf is
  password-protected".
- No text in a PDF: "no text found in report.pdf; it looks like a scan, which
  read_file cannot read".
- Any other library failure: "could not read report.pdf: <reason>".
- `PermissionError` on macOS: names Privacy & Security > Files and Folders as
  the place to allow access.
- Vision failures use the existing `LLMError` handling and 60-second limit.
- Dragged text or links that are not existing paths are ignored.

## Dependencies and packaging

- `requirements.txt` gains `pypdf`, `python-docx`, `openpyxl`, `python-pptx`,
  `pillow-heif` and `tkinterdnd2`, all importable on Python 3.14 / macOS
  arm64 (checked with a dry-run install). About 15 MB more in the app.
- `packaging/build-macos.sh` and the Windows build in `release.yml` add
  `--collect-all tkinterdnd2` and `--collect-all pillow_heif` for their native
  libraries.
- A local macOS build is checked for drag and drop, HEIC, PDF and Excel
  inside the packaged app.

## Testing

- **pytest suite** in `tests/` for everything that is not UI, run in CI by
  the existing lint job (renamed "Lint and test"). Sample PDF, PDF with no
  text, encrypted PDF, `.docx`, `.xlsx`, `.pptx`, HEIC and PNG files are
  generated in a temporary folder. It covers extraction, paging, caching,
  the binary and image messages, the attachment list wording and kinds,
  paste naming and pruning, and `view_image` with a stub vision model.
- **GUI checks on macOS**, scripted as for the Permissions button: picker
  path, a simulated drop, a real Cmd+V of an image on the clipboard, chips,
  removal, "+N more", and the transcript line.
- **End to end**: attach a PDF and a photo, ask "summarise the PDF and tell
  me what's in the photo", and compare the answers with the files.
- **By hand**: one real drag from Finder, done by the owner.
