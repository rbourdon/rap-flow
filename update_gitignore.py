with open('.gitignore', 'r') as f:
    content = f.read()
content = content.replace(
    '!backend/kits/default/**/*.wav',
    '!backend/kits/default/**/*.wav\n!backend/kits/default/**/*.flac'
)
with open('.gitignore', 'w') as f:
    f.write(content)
