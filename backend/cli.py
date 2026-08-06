import argparse
import os
import json
from pipeline import ingest_audio, separate_audio, detect_syllables, render_percussion

def main():
    parser = argparse.ArgumentParser(description="Rap Flow to Percussion Track CLI")
    parser.add_argument("input", help="URL or local path to audio")
    parser.add_argument("--outdir", default="output", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    input_wav = os.path.join(args.outdir, "input.wav")

    print(f"Ingesting {args.input}...")
    ingest_audio(args.input, input_wav)

    print("Separating stems...")
    vocals_wav, inst_wav = separate_audio(input_wav, args.outdir)

    print("Detecting syllables...")
    events = detect_syllables(vocals_wav)
    events_path = os.path.join(args.outdir, "events.json")
    with open(events_path, "w") as f:
        json.dump(events, f)

    print(f"Detected {len(events)} syllables.")

    print("Rendering percussion mix...")
    mix_wav = os.path.join(args.outdir, "mix.wav")
    render_percussion(events, inst_wav, mix_wav)

    print(f"Done. Outputs in {args.outdir}/")

if __name__ == "__main__":
    main()
