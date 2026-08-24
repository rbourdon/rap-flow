# Default drum kit — Salamander Drumkit (restructured)

Samples from the **Salamander Drumkit** by Alexander Holm, licensed **CC-BY 3.0**
(https://creativecommons.org/licenses/by/3.0/). Sourced via the StudioRack
mirror (https://github.com/studiorack/salamander-drumkit). See LICENSE.
tom_high is the rack tom varisped +3 semitones (ffmpeg asetrate).

## Format

kits/<kit_name>/<drum_class>/v<layer>_rr<variant>.flac

- drum_class: kick, snare, hat_closed, hat_open, tom_low, tom_mid, tom_high, crash, ride
- v<layer>: velocity layers, sorted soft -> loud (v1 = softest)
- rr<variant>: round-robin variants within a layer
- Loader must accept .wav and .flac (soundfile reads both natively)

## Layer map (this kit)

- kick:   v1=P, v2=F, v3=FF (3 RR each)
- snare:  v1=ghost, v2=MP, v3=F, v4=FF (3 RR each)
- hat_closed: v1=P, v2=F (4 RR each)
- hat_open:   v1=P, v2=F, v3=FF (2 RR each)
- toms:   v1=soft, v2=med, v3=loud (2 RR each)
- crash/ride: v1=soft, v2=loud (2 RR each)
