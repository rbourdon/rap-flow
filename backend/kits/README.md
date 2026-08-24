# Drum kits

The sample-playback engine (`backend/sampler.py`) renders the drum score into
audio by playing real one-shot samples from a kit on disk. This directory holds
those kits.

## Folder format

```
kits/<kit_name>/<drum_class>/v<layer>_rr<variant>.[wav|flac]
```

- `<kit_name>` — the kit folder (the bundled one is `default`). Point the
  pipeline at a different kit with the `KIT_DIR` environment variable or the
  `--kit` CLI flag.
- `<drum_class>` — one of the nine classes the engine understands:
  `kick`, `snare`, `hat_closed`, `hat_open`, `tom_low`, `tom_mid`, `tom_high`,
  `crash`, `ride`.
- `v<layer>` — the **velocity layer**, `v1` being the *softest*. A class may
  have any number of layers; incoming MIDI velocity picks the nearest one and is
  then fine-scaled with gain. A class with a single layer additionally darkens
  soft hits with a gentle lowpass.
- `rr<variant>` — a **round-robin** variant. The engine cycles the variants and
  never plays the same one twice in a row. With a single variant it applies a
  ±3% random varispeed so consecutive hits are never bit-identical.

Example:

```
kits/default/kick/v1_rr1.wav   # soft kick, round-robin 1
kits/default/kick/v1_rr2.wav   # soft kick, round-robin 2
kits/default/kick/v2_rr1.wav   # loud kick, round-robin 1
kits/default/kick/v2_rr2.wav   # loud kick, round-robin 2
kits/default/crash/v1_rr1.wav  # single crash one-shot
```

Files may be mono or stereo WAV or FLAC at any sample rate — they are resampled to the
render rate on load. The loader scans the directory, so **any conforming folder
works**: drop real samples in and they are picked up automatically. Classes with
no folder are simply skipped.

## The bundled `default` kit

The `default/` kit ships **synthesized placeholder one-shots** so the pipeline
works out of the box with no external assets. Everything is generated from
scratch (no sampled material), so it is CC0 / public domain. It provides 2
velocity layers × 2 round-robins for `kick`, `snare`, and `hat_closed`, plus a
single one-shot for each of the other classes.

To regenerate the placeholders (or as a starting point for your own kit):

```bash
python kits/generate_default_kit.py
```

To use a real, permissively-licensed acoustic/hip-hop kit instead, create a new
folder that follows the format above and select it with `--kit /path/to/kit` (or
`KIT_DIR`). No code changes are needed.
