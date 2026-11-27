# Model weights shipped with the backend

## `onset_refiner.pt` (0.5 MB)

The third step of the `speech` syllable detector (`speech.refine_onsets`): given
a vocal and syllable onsets that are good to a few 10 ms frames, it places each
one on the vowel onset itself, on a 2 ms grid.

- **Input.** A two-resolution log-mel spectrogram (6 ms and 25 ms windows, 40
  mel bands each, 2 ms hop), cut to ±110 ms around the coarse time and scaled by
  the window's own maximum, so it does not depend on gain.
- **Network.** Five unpadded kernel-5 dilated 1-D convolutions (64 channels,
  batch norm, ReLU) with the mel bands as input channels; 108,616 parameters.
  One logit per 2 ms step within ±70 ms; each step sees ±40 ms of context.
- **Decoding.** Softmax times a Gaussian prior centred on the coarse time
  (sd 30 ms, stored in the checkpoint), then the centre of mass around the peak.
  Confidence is the posterior mass within ±6 ms of it.
- **Training data.** The hand-labelled phone boundaries of TIMIT's training
  split (3,339 utterances, 334 speakers; 29 more speakers held out to pick the
  prior), about 43,000 vowel onsets, each used once clean and three times
  augmented: ±10% speed, a doubled voice, synthetic reverb, drum/bass/other
  bleed at −10 to −25 dB, EQ tilts, noise, gain, and soft clipping.
- **Result** on 300 TIMIT test utterances (123 unseen speakers, 3,793 onsets),
  starting from the phone classifier's onsets: within 20 ms 77% → 90%, median
  error 11.1 ms → 3.6 ms, and the same on an augmented copy of the test set
  (76% → 89%). Onsets after a stop, fricative or nasal end up at a 2-3 ms
  median; the remaining error is after glides, liquids and other vowels, where
  the boundary is gradual and the hand label is partly convention.

The checkpoint holds only tensors and plain numbers and is loaded with
`weights_only=True`. The training scripts are not in this repository yet.
