with open('backend/kits/README.md', 'r') as f:
    content = f.read()

content = content.replace(
    'v<layer>_rr<variant>.wav',
    'v<layer>_rr<variant>.[wav|flac]'
)
content = content.replace(
    'Files may be mono or stereo WAV',
    'Files may be mono or stereo WAV or FLAC'
)
with open('backend/kits/README.md', 'w') as f:
    f.write(content)

with open('backend/sampler.py', 'r') as f:
    content = f.read()
content = content.replace(
    'v<layer>_rr<variant>.wav',
    'v<layer>_rr<variant>.[wav|flac]'
)
content = content.replace(
    'def _load_wav(path, target_sr):',
    'def _load_audio(path, target_sr):'
)
content = content.replace(
    '_load_wav(p, sr)',
    '_load_audio(p, sr)'
)
content = content.replace(
    'Load a WAV as stereo',
    'Load a WAV or FLAC as stereo'
)
with open('backend/sampler.py', 'w') as f:
    f.write(content)
