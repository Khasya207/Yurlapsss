# Decoder YurLaps (YLP-1)

`YurLapsDecoder.exe` dibangun dari sumber di `native/`, ditulis khusus untuk
protokol YLP-1 (`docs/YURLAPS-PROTOCOL-DRAFT.md`). Tidak ada kode decoder pihak lain di
dalamnya.

Workflow `.github/workflows/build-windows.yml` punya job **`decoder`** yang:

1. membangun `librtlsdr` dari fork RTL-SDR Blog (perlu untuk dongle V4),
2. membangun decoder dengan RTL-SDR dan ZeroMQ,
3. menjalankan tes unit dan tes replay IQ sintetis,
4. mengemas `YurLapsDecoder.exe` + DLL + lisensi ke artifact `yurlaps-decoder`,

lalu job `build` mengunduhnya ke `vendor/decoder/` sebelum aplikasi dibangun.

## Keluaran decoder (ZeroMQ `tcp://127.0.0.1:5556`, juga stdout)

```text
P <ts_ms> YUL <id> <rssi_dbfs> <hits> <duration_us> <mer_db>
S <ts_ms> <noise_dbfs> <dc_offset> <frames_received> <frames_processed> <jitter_p95_ms>
```

Baris `H` (satu frame valid) hanya keluar dengan `--debug`.

## Argumen yang dipakai aplikasi

`-g <gain_db>`, `-d <serial>`, `-p <port>`, `-b` (bias tee), `-D` (direct
sampling paksa), `--quiet`, `--debug`, `--record file.iq`,
`--list-devices`, `--protocol-check` (mencetak `YURLAPS_YLP1=1`).

## Rekam IQ untuk analisis

`YurLapsDecoder.exe -g 28 --debug --record capture.iq` lalu putar ulang dengan
`YurLapsDecoder.exe --iq capture.iq --debug`.
