# Memakai decoder pada build YurLaps

1. Jalankan workflow **Build Windows** di GitHub Actions.
2. Unduh artifact `YurLaps-Windows` untuk installer lengkap.
3. Untuk build lokal, salin `YurLapsDecoder.exe` dan DLL dari artifact
   `yurlaps-decoder` ke `vendor/openstint/`, lalu jalankan `build_exe.bat`.

Decoder hasil installer berada di
`resources\engine\_internal\vendor\decoder\YurLapsDecoder.exe`.
