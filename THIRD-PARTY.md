# Komponen pihak ketiga dan lisensi

StintLab/YurLaps memakai decoder native milik proyek ini. Tidak ada kode
OpenStint di aplikasi ini.

| Komponen | Sumber | Lisensi | Penggunaan |
|---|---|---|---|
| **YurLapsDecoder YLP-1** | `decoder/native/` | MIT proyek YurLaps | Decoder protokol transponder YurLaps, dipanggil sebagai proses terpisah. |
| **librtlsdr** (fork RTL-SDR Blog, dukungan Blog V4) | https://github.com/rtlsdrblog/rtl-sdr-blog | GPL-2.0-or-later | Backend dongle RTL-SDR yang ditautkan ke decoder. |
| **libusb** | https://libusb.info/ | LGPL-2.1-or-later | Akses USB RTL-SDR. |
| **ZeroMQ / libzmq** | https://zeromq.org/ | MPL-2.0 | Kanal data decoder ke engine pada `tcp://127.0.0.1:5556`. |
| **Zadig / libwdi** | https://github.com/pbatard/libwdi | GPL-3.0 | Alat terpisah untuk memasang driver WinUSB pada dongle, bila dibundel. |

## Pemberitahuan distribusi

Workflow Windows membangun `YurLapsDecoder.exe` dari `decoder/native/` dan
membawa `librtlsdr.dll` dari fork RTL-SDR Blog. Karena librtlsdr adalah GPL,
paket rilis yang membawa DLL itu harus menyediakan teks lisensi dan akses ke
sumber sesuai GPL. Pemberitahuan paket ada di `decoder/NOTICES.txt`; workflow
juga menyalinnya ke artifact decoder.

Sumber native YurLaps dan draft protokol tersedia di repo ini. Aplikasi dan
kode YurLaps lainnya memakai lisensi yang tercantum di `LICENSE`.
