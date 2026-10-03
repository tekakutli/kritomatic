#!/usr/bin/env python3
"""
interpret_image.py

Interpret an image by asking `pi` to synthesize several different
captions of that image into one reading of what it actually shows.

The single positional argument can be either an image (PNG, JPG, etc.)
or a batch file produced by caption_presets.py. The type is detected
from the file content: known image magic numbers mean "image"; otherwise
a UTF-8 text file is treated as a batch.

    - Image argument:
        * If /tmp/caption_batch.txt already exists and records this
          exact image, reuse it.
        * Otherwise run caption_presets.py first.
        * Output goes to /tmp/caption_batch_interpreted.txt.

    - Batch-file argument:
        * Read captions from that file directly.
        * Output goes next to the input, with a "_interpreted" suffix.

    - Anything else (directory, binary that is not a known image,
      missing file) is an error.

Guards against re-running:

    * A batch file that already contains the interpretation marker is
      refused: it has already been interpreted.
    * An output file that already exists for the same image is refused:
      delete it to re-run.

`pi` is an external command; it is assumed to be on PATH.

Usage:
    interpret_image.py <image_or_batch>

Examples:
    interpret_image.py photo.png                 # image -> generate -> interpret
    interpret_image.py /tmp/caption_batch.txt    # reuse an existing batch
"""

import argparse
import subprocess
import sys
from pathlib import Path

# ===== CONFIGURATION =====
SCRIPT_DIR             = Path(__file__).resolve().parent            # recipes/
CAPTION_PRESETS_SCRIPT = SCRIPT_DIR / "caption_presets.py"

# Fixed batch path used when the argument is an image.
BATCH_PATH = Path("/tmp/caption_batch.txt")

# Suffix appended to the batch stem to form the output filename.
OUTPUT_SUFFIX = "_interpreted"

# Marker written into the output and used to detect already-interpreted
# files. Kept in one place so the writer and the detector cannot drift.
INTERPRETATION_MARKER = "CAPTION INTERPRETATION:"

# Refusal string some LLMs emit; stripped from `pi`'s output if present.
REFUSAL = "Sorry, that's beyond my current scope. Let’s talk about something else."

PROMPT_TEMPLATE = """You are given multiple captions generated from the SAME image. Each caption uses a different style/perspective (LLM, Descriptive, NSFW, Straightforward, e621 tags, Text Extractor).

Your task: Go BEYOND what the captions literally say. Synthesize, interpret, and read between the lines. Think like a film critic, a psychoanalyst, or a cultural anthropologist observing a social scene.

Here are all the captions:
---
{captions}
---

Now, answer these interpretative questions in flowing prose (no bullet points, no section headers):

1. What human drama is unfolding in this image? Describe the relationship between the people as if you were narrating a tense emotional scene in a novel.

2. What power struggle is happening? Who has control and who is vulnerable? How is that communicated through body language, positioning, and words?

3. What is the emotional landscape? What does each character feel toward the other and toward themselves? What's unspoken but present?

4. What social or cultural commentary might the artist be making? About gender, race, desire, shame, belonging, or identity?

5. If this image had a soundtrack, what would it sound like? What genre, tempo, instruments, and emotional tone?

6. What happens a moment before this scene? What happens a moment after? Reconstruct the implied narrative arc.

7. What psychological wound is being touched or inflicted here? What deeper fear or longing is being exploited?

8. If you had to summarize the 'thesis statement' of this image in one sentence, what would it be?

Finally, in one short paragraph, tell me the story this image is telling - not just what it shows, but what it MEANS.

Be bold, insightful, and interpretative. Do not just repeat descriptive details from the captions. Synthesize and interpret. Keep it under 600 words."""
# =========================


def sniff_kind(path: Path) -> str:
    """Return 'image', 'batch', or 'unknown' from the file's content.

    Image detection is by magic number so a .png that has been renamed
    is still recognised, and a .txt that happens to contain image bytes
    is not mistaken for an image. A file whose first 16 bytes do not
    match any known image format and that decodes cleanly as UTF-8 is
    treated as a batch.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "unknown"

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(b"BM"):
        return "image"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image"

    # Anything else that reads as UTF-8 text is treated as a batch file.
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    return "batch"


def read_batch_image(batch_path: Path):
    """Return the image path recorded in the batch header, or None."""
    try:
        with open(batch_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if stripped.startswith("IMAGE:"):
                    return stripped.split(":", 1)[1].strip()
                if stripped == "":
                    break
    except OSError:
        pass
    return None


def batch_matches_image(batch_path: Path, image_path: Path) -> bool:
    """True if the batch file exists and records this exact image path."""
    if not batch_path.is_file():
        return False
    recorded = read_batch_image(batch_path)
    if not recorded:
        return False
    try:
        return Path(recorded).resolve() == image_path.resolve()
    except OSError:
        return False


def contains_marker(path: Path) -> bool:
    """True if the file's content already carries the interpretation marker."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return INTERPRETATION_MARKER in content


def output_path_for(batch_path: Path) -> Path:
    """<stem>_interpreted<suffix> next to the batch file."""
    return batch_path.with_name(
        batch_path.stem + OUTPUT_SUFFIX + batch_path.suffix
    )


def run_presets(image_path: Path) -> int:
    """Run caption_presets.py on the image."""
    print(f"Generating captions for: {image_path}")
    result = subprocess.run(
        [sys.executable, str(CAPTION_PRESETS_SCRIPT), str(image_path)],
        check=False,
    )
    return result.returncode


def strip_refusal(text: str) -> str:
    return text.replace(REFUSAL, "")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interpret an image (or a batch of captions) via `pi`.",
    )
    parser.add_argument("path",
                        help="Image file, or a batch file from "
                             "caption_presets.py.")
    args = parser.parse_args()

    arg_path = Path(args.path).expanduser().resolve()
    if not arg_path.exists():
        print(f"Error: no such file: {arg_path}", file=sys.stderr)
        return 1
    if not arg_path.is_file():
        print(f"Error: not a regular file: {arg_path}", file=sys.stderr)
        return 1

    kind = sniff_kind(arg_path)

    if kind == "image":
        batch_path = BATCH_PATH
        output_path = output_path_for(batch_path)
        image_path = arg_path

        # Refuse if the interpretation already exists for this image.
        if (output_path.is_file()
                and batch_matches_image(output_path, image_path)):
            print(f"Error: already interpreted for this image.",
                  file=sys.stderr)
            print(f"  Existing output: {output_path}", file=sys.stderr)
            print(f"  Delete it to re-run.", file=sys.stderr)
            return 1

        if batch_matches_image(batch_path, image_path):
            print(f"Reusing existing captions: {batch_path}")
        else:
            if batch_path.is_file():
                recorded = read_batch_image(batch_path)
                if recorded:
                    print(f"Batch file is for a different image "
                          f"({recorded}); regenerating.")
                else:
                    print("Batch file has no image header; regenerating.")
            else:
                print(f"No batch file at {batch_path}; generating.")
            rc = run_presets(image_path)
            if rc != 0:
                print(f"caption_presets.py failed (exit {rc})",
                      file=sys.stderr)
                return 1

    elif kind == "batch":
        batch_path = arg_path
        output_path = output_path_for(batch_path)

        # Refuse if the batch itself has already been interpreted.
        if contains_marker(batch_path):
            print(f"Error: {batch_path} already contains an interpretation.",
                  file=sys.stderr)
            print(f"  Pass the un-interpreted captions instead.",
                  file=sys.stderr)
            return 1

        # Refuse if the corresponding output file already exists.
        if output_path.is_file():
            print(f"Error: output already exists: {output_path}",
                  file=sys.stderr)
            print(f"  Delete it to re-run.", file=sys.stderr)
            return 1

        print(f"Using existing captions: {batch_path}")

    else:
        print(f"Error: {arg_path} is neither an image nor a batch text file.",
              file=sys.stderr)
        return 1

    if not batch_path.is_file() or batch_path.stat().st_size == 0:
        print(f"Error: batch file is missing or empty: {batch_path}",
              file=sys.stderr)
        return 1

    captions = batch_path.read_text(encoding="utf-8")
    prompt = PROMPT_TEMPLATE.format(captions=captions)

    print("Calling `pi` ...")
    result = subprocess.run(
        ["pi", "-p", prompt],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(f"pi exited {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1

    interpretation = strip_refusal(result.stdout)

    parts = [
        captions.rstrip(),
        "",
        "",
        "-" * 50,
        INTERPRETATION_MARKER,
        "-" * 50,
        "",
        "",
        interpretation.rstrip(),
        "",
        "",
    ]
    output_path.write_text("\n".join(parts), encoding="utf-8")

    print("=" * 60)
    print("INTERPRETATION COMPLETE")
    print("=" * 60)
    print(interpretation)
    print()
    print("=" * 60)
    print("File saved:")
    print(f"  {output_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
