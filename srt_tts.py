"""
Generate speech for each SRT subtitle line using Chatterbox TTS and stitch them
into a single WAV while respecting subtitle timing.

Rules:
- Create a temp directory next to the SRT file.
- For each SRT cue, synthesize a WAV named with the cue number and its start/end times.
- When combining, place each cue at its SRT start time. If the generated audio is
  longer than the SRT's time span, do NOT trim it; just let it run long and place
  the next cue immediately after the previous audio finishes (i.e. no overlap).

Example:
    python srt_tts.py path/to/subs.srt path/to/reference_voice.wav \
        --multilingual --language-id en

"""
from __future__ import annotations
import argparse
import os
import re
import sys
import math
from dataclasses import dataclass
from typing import List, Tuple

import torch
import torchaudio as ta

try:
    from chatterbox.tts import ChatterboxTTS
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
except Exception as e:
    print("Error: failed to import Chatterbox TTS modules.\n" \
          "Make sure the correct package is installed (e.g., chatterbox-tts) and accessible.")
    raise

# --------------------------
# SRT parsing utilities
# --------------------------
TIME_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def hmsms_to_seconds(h: int, m: int, s: int, ms: int) -> float:
    return h * 3600 + m * 60 + s + ms / 1000.0


@dataclass
class Cue:
    index: int
    start: float  # seconds
    end: float    # seconds
    text: str

def parse_txt(path: str) -> List[Cue]:
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = [ln.strip("\ufeff") for ln in f if ln.strip() != ""]

    cues: List[Cue] = []
    start = 0.0
    duration = 0.1  # 100 milliseconds
    for idx, line in enumerate(lines, 1):
        cue = Cue(index=idx, start=start, end=start+duration, text=line)
        cues.append(cue)
        start += duration
    return cues

def parse_srt(path: str) -> List[Cue]:
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Split on blank lines (one or more) robustly
    blocks = re.split(r"\n\s*\n", content.strip(), flags=re.MULTILINE)
    cues: List[Cue] = []

    for block in blocks:
        lines = [ln.strip("\ufeff") for ln in block.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # First line may be an index; if not, we still try
        idx = None
        line0_is_index = lines[0].isdigit()
        if line0_is_index:
            idx = int(lines[0])
            lines = lines[1:]
        else:
            idx = len(cues) + 1

        if not lines:
            continue

        # Next line must be the time range
        m = TIME_RE.match(lines[0])
        if not m:
            # Attempt to continue to next block if malformed
            continue
        (sh, sm, ss, sms, eh, em, es, ems) = map(int, m.groups())
        start = hmsms_to_seconds(sh, sm, ss, sms)
        end = hmsms_to_seconds(eh, em, es, ems)

        # Remaining lines are text (join with spaces)
        text_lines = lines[1:] if len(lines) > 1 else []
        text = " ".join(text_lines).strip()
        cues.append(Cue(index=idx, start=start, end=end, text=text))

    # Sort by start time just in case
    cues.sort(key=lambda c: (c.start, c.index))
    return cues


# --------------------------
# Audio helpers
# --------------------------

def ensure_mono(wav: torch.Tensor) -> torch.Tensor:
    """Return (1, N) float32 tensor."""
    if wav is None:
        return wav
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    elif wav.dim() == 2 and wav.size(0) > 1:
        # average to mono
        wav = wav.mean(dim=0, keepdim=True)
    return wav.to(dtype=torch.float32)


def format_timecode(seconds: float) -> str:
    """Format seconds to HH-MM-SS_mmm for filenames."""
    ms_total = int(round(seconds * 1000.0))
    h = ms_total // 3600000
    ms_total %= 3600000
    m = ms_total // 60000
    ms_total %= 60000
    s = ms_total // 1000
    ms = ms_total % 1000
    return f"{h:02d}-{m:02d}-{s:02d}_{ms:03d}"


# --------------------------
# Core processing
# --------------------------

def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_tts(multilingual: bool, device: str):
    if multilingual:
        model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    else:
        model = ChatterboxTTS.from_pretrained(device=device)
    return model


def synthesize_cues(
    cues: List[Cue],
    model,
    sr: int,
    temp_dir: str,
    language_id: str,
    ref_audio_path: str | None,
) -> List[Tuple[Cue, str, torch.Tensor]]:
    """Synthesize each cue. Returns list of (cue, filepath, waveform)."""
    results = []
    os.makedirs(temp_dir, exist_ok=True)

    for cue in cues:
        start_code = format_timecode(cue.start)
        end_code = format_timecode(cue.end)
        base = f"{cue.index:04d}__{start_code}__{end_code}.wav"
        out_path = os.path.join(temp_dir, base)

        kwargs = {"language_id": language_id}
        if ref_audio_path:
            kwargs["audio_prompt_path"] = ref_audio_path

        wav = model.generate(cue.text, **kwargs)
        wav = ensure_mono(wav)
        # Ensure sample rate consistency; if model has sr attribute, prefer that
        # but we trust the provided sr parameter here.
        ta.save(out_path, wav, sr)
        results.append((cue, out_path, wav))
        print(f"Synthesized cue {cue.index} -> {out_path} ({wav.shape[-1]/sr:.2f}s)")

    return results


def stitch(results: List[Tuple[Cue, str, torch.Tensor]], sr: int) -> torch.Tensor:
    """Combine per-cue audio respecting SRT start times, but never truncating a cue.
    If a cue's audio overflows its slot, we just keep it and start the next cue after
    the previous audio's end (i.e., append immediately, ignoring its nominal SRT start).
    """
    timeline = 0  # in samples
    assembled = []  # list of tensors to cat

    for cue, _path, wav in results:
        wav = ensure_mono(wav)
        n = wav.shape[-1]
        desired_start_samples = int(round(cue.start * sr))

        start_samples = max(desired_start_samples, timeline)
        if start_samples > timeline:
            # Insert silence gap
            gap = start_samples - timeline
            assembled.append(torch.zeros((1, gap), dtype=torch.float32))
        # Append the cue audio
        assembled.append(wav)
        timeline = start_samples + n

    if not assembled:
        return torch.zeros((1, 0), dtype=torch.float32)

    return torch.cat(assembled, dim=-1)


# --------------------------
# Main CLI
# --------------------------

def main():
    parser = argparse.ArgumentParser(description="SRT -> TTS per line -> stitched WAV")
    parser.add_argument("input", type=str, help="Path to the .srt or .txt file")
    parser.add_argument("reference_audio", type=str, nargs="?", default=None,
                        help="Reference voice audio (wav) for cloning (optional)")
    parser.add_argument("--language-id", type=str, default="en",
                        help="Language ID for the TTS model (e.g., en, fr, zh, ...)")
    parser.add_argument("--multilingual", default=False, action="store_true",
                        help="Use ChatterboxMultilingualTTS instead of the default model")

    args = parser.parse_args()

    input = os.path.abspath(args.input)
    if not os.path.isfile(input):
        print(f"Error: SRT file not found: {input}")
        sys.exit(1)

    if not (input.lower().endswith(".srt") or input.lower().endswith(".txt")):
        print(f"Error: Input file must be .srt or .txt: {input}")
        sys.exit(1)

    reference_stem = os.path.splitext(os.path.basename(args.reference_audio))[0] if args.reference_audio else 'noRef'

    srt_dir = os.path.dirname(input)
    srt_stem = os.path.splitext(os.path.basename(input))[0]
    temp_dir_name = f"{srt_stem}_tts_{reference_stem}_temp"
    temp_dir = os.path.join(srt_dir, temp_dir_name)

    out_wav = os.path.join(srt_dir, f"{srt_stem}_tts_{reference_stem}.wav")

    # if file ends with .txt, parse as txt
    if input.lower().endswith(".txt"):
        cues = parse_txt(input)
    else:
        cues = parse_srt(input)

    if not cues:
        print("No cues parsed from input; exiting.")
        sys.exit(1)

    print(f"Parsed {len(cues)} cues from {input}")

    # Device + model
    device = pick_device()
    print(f"Using device: {device}")
    model = load_tts(args.multilingual, device)
    sr = getattr(model, "sr", 24000)  # fallback

    # Synthesize per cue
    results = synthesize_cues(
        cues=cues,
        model=model,
        sr=sr,
        temp_dir=temp_dir,
        language_id=args.language_id,
        ref_audio_path=args.reference_audio,
    )

    # Stitch
    final_wav = stitch(results, sr)
    ta.save(out_wav, final_wav, sr)

    total_dur = final_wav.shape[-1] / sr if final_wav.numel() > 0 else 0.0
    print(f"\nDone. Wrote final WAV: {out_wav}  (duration: {total_dur:.2f}s)")
    print(f"Per-line WAVs are in: {temp_dir}")


if __name__ == "__main__":
    main()
