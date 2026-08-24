with open('backend/sampler.py', 'r') as f:
    content = f.read()

content = content.replace(
    'for path in sorted(glob.glob(os.path.join(class_dir, "v*_rr*.wav"))):',
    'for path in sorted(glob.glob(os.path.join(class_dir, "v*_rr*.wav")) + glob.glob(os.path.join(class_dir, "v*_rr*.flac"))):'
)

with open('backend/sampler.py', 'w') as f:
    f.write(content)
