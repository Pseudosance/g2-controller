import serial, sys, time
src, dst = sys.argv[1], sys.argv[2]
data = open(src, "rb").read()
s = serial.Serial("COM3", 230400, timeout=1)
def cmd(c, wait=1.5):
    s.write(c.encode() + b"\r\n"); time.sleep(wait); r = s.read(65536).decode(errors="replace"); print(r); return r
s.write(b"\r\n"); time.sleep(0.7); s.read(8192)
cmd(f"storage remove {dst}")
CH = 512
for off in range(0, len(data), CH):
    part = data[off:off+CH]
    s.reset_input_buffer()
    s.write(f"storage write_chunk {dst} {len(part)}\r".encode())
    buf = b""; t = time.time()
    while b"Ready" not in buf and time.time() - t < 5:
        buf += s.read(64)
    if b"Ready" not in buf: sys.exit(f"no Ready at {off}: {buf!r}")
    time.sleep(0.05)
    s.write(part); s.flush()
    buf = b""; t = time.time()
    while b">:" not in buf and time.time() - t < 5:
        buf += s.read(64)
print("sent", len(data))
cmd(f"storage md5 {dst}")
for extra in sys.argv[3:]:
    cmd(extra, 2.5)
s.close()
