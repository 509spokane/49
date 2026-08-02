import subprocess, sys

def is_striped(path, sample_cols=80, min_run=8):
    r = subprocess.run(['djpeg', '-pnm', path], capture_output=True)
    if r.returncode != 0: return False
    data = r.stdout
    if not data.startswith(b'P6'): return False
    i = 0; n = 0
    while n < 3:
        if data[i] == ord('\n'): n += 1
        i += 1
    parts = data[:i].decode().split()
    w, h = int(parts[1]), int(parts[2])
    pixel_data = data[i:]
    row_bytes = w * 3
    step = max(1, w // sample_cols)
    cols = list(range(0, w, step))[:sample_cols]
    run, prev = 1, None
    for r in range(int(h * 0.70), h):
        offset = r * row_bytes
        if offset + row_bytes > len(pixel_data): break
        row = bytes(pixel_data[offset + c*3] for c in cols)
        run = run + 1 if row == prev else 1
        if run >= min_run: return True
        prev = row
    return False

if is_striped(sys.argv[1]):
    print("WARNING: repeated-row pattern detected in bottom 30% — quarantining")
    sys.exit(1)
