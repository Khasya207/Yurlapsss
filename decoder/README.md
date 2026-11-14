# Build YurLaps Decoder

Workflow `.github/workflows/build-windows.yml` membangun `YurLapsDecoder.exe`
dan DLL sebelum menjalankan build aplikasi. Artefak decoder disalin ke
`vendor/openstint/` sebagai input build.

Installer hanya memasukkan `YurLapsDecoder.exe` dan DLL ke
`resources/engine/_internal/vendor/decoder/`. Berkas lain dari folder input
tidak masuk ke installer.

Untuk build manual di Windows, jalankan `decoder/cara.ps1`, lalu salin
`YurLapsDecoder.exe` dan DLL hasilnya ke `vendor/openstint/` sebelum
menjalankan `build_exe.bat`.

Versi sumber build dikunci di `decoder/patch/upstream-ref.txt`. Jika target
CMake berubah, perbarui `decoder/patch/rename-target.patch`.
