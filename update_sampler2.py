with open('backend/sampler.py', 'r') as f:
    content = f.read()

content = content.replace(
    'like <drum_class>/v1_rr1.wav.',
    'like <drum_class>/v1_rr1.wav or .flac.'
)
with open('backend/sampler.py', 'w') as f:
    f.write(content)
