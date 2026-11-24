# YurLaps Decoder Native

C++20 decoder for the YLP-1 transponder protocol. Protocol definition:
`../docs/YURLAPS-PROTOCOL-DRAFT.md`.

## What it does

Reads unsigned 8-bit interleaved IQ at 2.5 MS/s (live from an RTL-SDR or from a
file), finds the marker, demodulates BPSK with two carrier-phase hypotheses
(constant, then slope from the marker if the CRC fails), runs a tail-biting
Viterbi decoder on soft bits, checks the CRC, and groups valid frames of the
same ID within 40 ms. A passing needs at least two valid frames. Timestamps come
from sample indices, with host time only used to anchor them.

## Build

```sh
cmake -S . -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Options: `-DYURLAPS_WITH_RTLSDR=ON` (needs `librtlsdr`; use the RTL-SDR Blog fork
for the V4), `-DYURLAPS_WITH_ZMQ=ON` (needs ZeroMQ and cppzmq).

## Usage

```sh
YurLapsDecoder --iq capture.iq [--debug]          # replay a file
YurLapsDecoder --rtl [serial] [gain_db] [--debug] [--record file.iq]
```

- Default gain is 28 dB. The V4 is detected by tuner type or name and used
  without direct sampling. Other dongles use direct sampling mode 2.
- `--record` also writes the raw IQ stream (about 5 MB/s). Keep captures short.
- With ZeroMQ enabled, `--rtl` also publishes each line on `tcp://127.0.0.1:5556`.
- `--debug` adds one `H` line per valid frame.

## Output

```text
P <timestamp_ms> YUL <id> <rssi_dbfs> <hits> <duration_us> <mer_db>
S <timestamp_ms> <noise_dbfs> <dc_offset> <frames_received> <frames_processed> <jitter_p95_ms>
H <timestamp_ms> YUL <id> <rssi_dbfs> <mer_db> <status>
```

| Line | Meaning |
|---|---|
| `P` | One passing. Time is the middle of the span where RSSI stays within 6 dB of its peak. `duration_us` is that span, not the loop width. `hits` is the frame count. |
| `S` | Status about once per second. `noise_dbfs` is `-inf` while no estimate exists, for example when a transponder transmits continuously. |
| `H` | One valid frame (debug only). RSSI in dBFS, MER in dB. |

Timestamps are in milliseconds, durations in microseconds.

## Tests

`protocol_test` (golden frame vectors, ID range), `iq_test` (generated IQ),
`passing_test` (timing estimate).

## Known limits

- `duration_us` is not a speed measurement. See the roadmap.
- With `--rtl`, host timestamps come from USB buffers and carry jitter. The
  RTL-SDR crystal drifts, so absolute time needs an external reference.
