with open('backend/kits/generate_default_kit.py', 'r') as f:
    content = f.read()

content = content.replace(
    'path = os.path.join(out_dir, f"v{layer}_rr{variant}.wav")',
    'path = os.path.join(out_dir, f"v{layer}_rr{variant}.flac")'
)
content = content.replace(
    'sf.write(path, stereo, SR, subtype="PCM_16")',
    'sf.write(path, stereo, SR, format="FLAC", subtype="PCM_16")'
)
with open('backend/kits/generate_default_kit.py', 'w') as f:
    f.write(content)
