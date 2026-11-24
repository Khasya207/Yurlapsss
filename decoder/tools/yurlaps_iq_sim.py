#!/usr/bin/env python3
"""Generate unsigned 8-bit interleaved IQ for the YLP-1 decoder.

The simulator is intentionally small and deterministic. It exercises carrier
phase, noise, repeated frames, and multiple IDs without depending on SDR tools.
"""
from __future__ import annotations

import argparse
import math
import random
import struct
from pathlib import Path

PREAMBLE = 0xF9A8
POLY_A = 0x5B
POLY_B = 0x79


def parity(value: int) -> int:
    result = 0
    while value:
        result ^= value & 1
        value >>= 1
    return result


def crc16(identifier: int) -> int:
    value = 0x594C
    for byte in identifier.to_bytes(3, "big"):
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ 0x1021) & 0xFFFF if value & 0x8000 else (value << 1) & 0xFFFF
    return value


def payload_symbols(identifier: int) -> list[int]:
    payload = identifier.to_bytes(3, "big") + crc16(identifier).to_bytes(2, "big")
    bits = [(byte >> shift) & 1 for byte in payload for shift in range(7, -1, -1)]
    wstate = 0x5D
    for index in range(40):
        bits[index] ^= (wstate >> 6) & 1
        wstate = ((wstate << 1) | (((wstate >> 6) ^ (wstate >> 3)) & 1)) & 0x7F
    initial = 0
    for candidate in range(64):
        state = candidate
        for bit in bits:
            state = ((state << 1) | bit) & 0x3F
        if state == candidate:
            initial = candidate
            break
    result: list[int] = []
    state = initial
    for bit in bits:
        register = ((state << 1) | bit) & 0x7F
        result.extend((parity(register & POLY_A), parity(register & POLY_B)))
        state = register & 0x3F
    return result


def frame(identifier: int) -> list[int]:
    return [0] * 4 + [(PREAMBLE >> bit) & 1 for bit in range(15, -1, -1)] + payload_symbols(identifier) + [0, 1, 1, 0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--ids", default="2504260")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--noise", type=float, default=0.04, help="RMS noise as IQ full-scale fraction")
    parser.add_argument("--phase", type=float, default=0.71)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    ids = [int(value) for value in args.ids.split(",")]
    if any(identifier < 1 or identifier > 0xFFFFFF for identifier in ids):
        raise SystemExit("IDs must be 1..16777215")
    random.seed(args.seed)
    samples: list[tuple[float, float]] = []
    for _ in range(4000):
        samples.append((random.gauss(0, args.noise), random.gauss(0, args.noise)))
    for repeat in range(args.repeats):
        for identifier in ids:
            for bit in frame(identifier):
                sign = 1.0 if bit else -1.0
                carrier_i = 0.8 * sign * math.cos(args.phase)
                carrier_q = 0.8 * sign * math.sin(args.phase)
                for _ in range(2):
                    samples.append((carrier_i + random.gauss(0, args.noise), carrier_q + random.gauss(0, args.noise)))
            gap = 1500 + random.randint(-400, 400)
            samples.extend((random.gauss(0, args.noise), random.gauss(0, args.noise)) for _ in range(gap))
    with args.output.open("wb") as output:
        for i_value, q_value in samples:
            i_byte = max(0, min(255, round(127.5 + 127.5 * i_value)))
            q_byte = max(0, min(255, round(127.5 + 127.5 * q_value)))
            output.write(bytes((i_byte, q_byte)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
