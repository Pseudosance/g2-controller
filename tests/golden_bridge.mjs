// Bridge: reference crc/framing/decode via g2.js (its only exports: UUIDS, SID,
// crc16, frames, decode, GESTURES, wrap, effects). Message builders live in
// Python (g2proto.py); their field layout is validated through op=decode_fields
// against g2.js's authoritative decoder.
import { crc16, frames, decode } from "../vendor/even-g2-js/g2.js";

const req = JSON.parse(await (async () => { let s = ""; for await (const c of process.stdin) s += c; return s; })());
let out = {};
if (req.op === "crc") {
  out = req.hexes.map((h) => crc16(Buffer.from(h, "hex")));
} else if (req.op === "frames") {
  out = frames(Buffer.from(req.hex, "hex"), { seq: req.seq, sid: req.sid, flag: req.flag, chunk: req.chunk })
    .map((f) => Buffer.from(f).toString("hex"));
} else if (req.op === "decode") {
  // payloads: {name: hex(pb payload)} -> {name: {uint1, uint2, fields}}
  const enc = (m) => {
    const o = {};
    for (const [f, vals] of m.fields) o[String(f)] = vals.map((v) => (Buffer.isBuffer(v) ? v.toString("hex") : v));
    return o;
  };
  const res = {};
  for (const [k, h] of Object.entries(req.payloads)) {
    const o = enc(decode(Buffer.from(h, "hex")));
    res[k] = { uint1: typeof o["1"]?.[0] === "number" ? o["1"][0] : null,
               uint2: typeof o["2"]?.[0] === "number" ? o["2"][0] : null,
               fields: o };
  }
  out = res;
} else if (req.op === "handshake") {
  // reference frames for the 4 handshake messages, built from an explicitly passed
  // pb payload (Python computes payload bytes; JS wraps+flags them authoritatively)
  out = req.messages.map((m) =>
    frames(Buffer.from(m.hex, "hex"), { seq: m.seq, sid: m.sid, flag: m.flag, chunk: 200 })
      .map((f) => Buffer.from(f).toString("hex"))[0]);
} else if (req.op === "decode_fields") {
  // raw pb payload -> {field_number: [values]} (buffers as hex)
  const o = {};
  for (const [f, vals] of decode(Buffer.from(req.hex, "hex")).fields) {
    o[String(f)] = vals.map((v) => (Buffer.isBuffer(v) ? v.toString("hex") : v));
  }
  out = o;
}
process.stdout.write(JSON.stringify(out));
