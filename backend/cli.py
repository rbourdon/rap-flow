import argparse
import os
import shutil

import workflow


def main():
    parser = argparse.ArgumentParser(description="Rap Flow to Percussion Track CLI")
    parser.add_argument("input", help="URL or local path to audio")
    parser.add_argument("--outdir", default="output", help="Output directory")
    parser.add_argument(
        "--artifacts",
        default=None,
        help="Durable artifact cache directory (default: <outdir>/artifacts). "
             "Reused across runs so downloads/stems are not recomputed.",
    )
    parser.add_argument(
        "--from-stage",
        choices=workflow.STAGES,
        default=None,
        help="Force recomputation starting at this stage (earlier stages are "
             "reused from the cache). Omit to reuse every cached artifact.",
    )
    parser.add_argument(
        "--no-groove",
        action="store_true",
        help="Skip the GrooVAE tap2drum model and use the built-in heuristic "
             "drum mapping. Local runs without Magenta installed fall back to "
             "the heuristic automatically; this forces it.",
    )
    parser.add_argument(
        "--kit",
        default=None,
        help="Path to a drum kit directory (see backend/kits/README.md for the "
             "folder format). Defaults to the bundled kit or $KIT_DIR.",
    )
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    artifacts_root = args.artifacts or os.path.join(args.outdir, "artifacts")
    os.makedirs(artifacts_root, exist_ok=True)

    # CLI flags mirror the GROOVE_ENABLED / KIT_DIR env vars via render params.
    params = {}
    if args.no_groove:
        params["groove_enabled"] = False
    if args.kit:
        params["kit"] = args.kit

    def on_progress(stage, label, state, reused):
        if state == "RUNNING":
            print(f"[{stage}] {label}...")
        elif state == "REUSED":
            print(f"[{stage}] reused cached artifact")

    result = workflow.run_local(
        artifacts_root,
        args.input,
        params=params,
        from_stage=args.from_stage,
        yt_cookies=os.environ.get("YT_COOKIES"),
        yt_proxy=os.environ.get("YT_PROXY"),
        on_progress=on_progress,
    )

    # Copy the final artifacts out of the cache into the requested outdir so the
    # CLI keeps its previous, predictable output layout.
    mix_out = os.path.join(args.outdir, "mix.wav")
    for src_key, dest_name in (
        ("mix_wav", "mix.wav"),
        ("midi_path", "mix.mid"),
        ("perc_wav", "mix_perc_only.wav"),
        ("inst_wav", "mix_inst_only.wav"),
        ("events_path", "events.json"),
    ):
        src = result.get(src_key)
        if src and os.path.exists(src):
            shutil.copy2(src, os.path.join(args.outdir, dest_name))

    print(f"Done. Outputs in {args.outdir}/")
    print(f"Result mix: {mix_out}")
    print(f"Percussion-only render (for verification): "
          f"{os.path.join(args.outdir, 'mix_perc_only.wav')}")


if __name__ == "__main__":
    main()
