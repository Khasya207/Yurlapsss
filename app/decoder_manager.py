"""Supervisor decoder: menjalankan decoder di latar belakang & auto-reconnect.

Perilaku yang diinginkan (dan diimplementasikan di sini):

* YurLaps **selalu terbuka** walau SDR belum terpasang / decoder belum ada.
* Decoder dijalankan sebagai proses latar belakang **tanpa jendela hitam**
  (CREATE_NO_WINDOW di Windows).
* Kalau decoder mati (YurLaps Decoder terputus, crash, USB reset), percobaan
  berikutnya dilakukan setelah 5 detik; pemasangan USB membangunkan supervisor.
* Proses yang masih hidup tidak di-restart hanya karena belum ada passing.
* Biner yang dijalankan harus menandai diri sebagai YurLapsDecoder YLP-1 (`--protocol-check`).
* Status lengkap (proses hidup? berapa kali restart? kenapa gagal?) tersedia
  untuk UI lewat `status()`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import Config, app_root, default_data_dir

IS_WINDOWS = os.name == "nt"

# URL rilis lama dipertahankan untuk kompatibilitas konfigurasi; tidak dipakai otomatis.

# Nama file yang kita cari setelah ekstrak (rilis resmi memakai .exe; build
# Linux tanpa ekstensi).
# YurLapsDecoder = biner hasil build dari decoder/native (protokol YLP-1).
EXE_NAMES = ("YurLapsDecoder.exe", "YurLapsDecoder")
RETRY_SECONDS = 5.0


def public_decoder_log(line: str) -> str:
    """Keep decoder diagnostics while presenting the YurLaps device name."""
    for old, new in (("RTL-SDR Blog V4", "YurLaps Decoder"),
                     ("RTL-SDR", "YurLaps Decoder"),
                     ):
        line = line.replace(old, new)
    return line


def exe_names() -> tuple[str, ...]:
    """Urutan pencarian biner sesuai platform (di Linux/Mac tanpa .exe)."""
    if IS_WINDOWS:
        return EXE_NAMES
    return tuple(n for n in EXE_NAMES if not n.endswith(".exe")) + \
           tuple(n for n in EXE_NAMES if n.endswith(".exe"))


def command_for(exe: Path) -> list[str]:
    """Perintah untuk menjalankan biner decoder.

    Biner asli dijalankan langsung. Kalau file-nya **skrip** (diawali `#!`,
    mis. decoder hasil kompilasi sendiri atau skrip pembungkus python), ia
    dijalankan lewat interpreter-nya — karena Windows tidak mengenal shebang
    dan tidak bisa mengeksekusi file tanpa ekstensi.
    """
    exe = Path(exe)
    try:
        with open(exe, "rb") as fh:
            if fh.read(2) == b"#!":
                with open(exe, "r", encoding="utf-8", errors="replace") as sh:
                    line = sh.readline().strip()
                parts = line[2:].split()
                if parts:
                    # `#!python`, `#!/usr/bin/env python3`, `#!/usr/bin/python3`
                    if any("python" in part.lower() for part in parts):
                        return [sys.executable, str(exe)]
                    return parts + [str(exe)]
    except Exception:
        pass
    if exe.suffix.lower() == ".py":
        return [sys.executable, str(exe)]
    return [str(exe)]


@dataclass
class DecoderStatus:
    mode: str = "managed"          # managed | external | off
    running: bool = False
    pid: Optional[int] = None
    exe: str = ""
    started_at: Optional[float] = None
    restarts: int = 0
    last_exit: Optional[str] = None
    last_error: str = ""
    state: str = "idle"            # idle | starting | running | error | downloading
    message: str = ""
    args: list = field(default_factory=list)   # argumen aktif (ditampilkan di UI)
    gain: Optional[float] = None               # gain tuner aktif (dB)
    freq_hz: Optional[float] = None            # frekuensi center dari stderr decoder
    device_present: Optional[bool] = None     # hasil deteksi USB terakhir

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "running": self.running, "pid": self.pid,
            "exe": self.exe, "started_at": self.started_at,
            "restarts": self.restarts, "last_exit": self.last_exit,
            "last_error": self.last_error, "state": self.state,
            "message": self.message,
            "args": list(self.args or []),
            "gain": self.gain,
            "freq_hz": self.freq_hz,
            "device_present": self.device_present,
        }


class DecoderManager(threading.Thread):
    def __init__(self, cfg: Config, engine, on_change=None):
        super().__init__(name="decoder-manager", daemon=True)
        self.cfg = cfg
        self.engine = engine
        self.on_change = on_change
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self.status = DecoderStatus()
        self._log_path = default_data_dir() / "decoder.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # debounce untuk perubahan setelan (gain) supaya tidak restart beruntun
        self._restart_timer: Optional[threading.Timer] = None
        self._restart_lock = threading.Lock()
        self._wanted_stop = False       # True kalau restart memang diminta UI
        self._device_present: Optional[bool] = None

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()
        self._kill_process()

    def _wait(self, timeout: float) -> bool:
        """Wait for timeout, or wake immediately on a USB state change."""
        self._wake.wait(timeout)
        stopped = self._stop_event.is_set()
        self._wake.clear()
        return stopped

    def device_changed(self, present: Optional[bool]) -> None:
        """Wake the supervisor as soon as the USB decoder is plugged in."""
        if present is None:
            return
        present = bool(present)
        previous = self._device_present
        self._device_present = present
        self._publish(device_present=present)
        if previous is not present:
            self._wake.set()
        if previous is True and not present:
            # A Windows USB scan can temporarily miss a dongle while the
            # decoder has it open. Keep a live process; its exit/log is the
            # authority for reconnecting after a real unplug.
            if self._proc is None or self._proc.poll() is not None:
                self._publish(state="waiting", message="Menunggu YurLaps Decoder …")
            return
        if not present or previous is present or previous is None:
            return
        if self.cfg.manage_decoder and not self._stop_event.is_set():
            self._wake.set()
            if self._proc is None or self._proc.poll() is not None:
                self._publish(state="starting", message="YurLaps Decoder terdeteksi …")

    # ------------------------------------------------------------------
    def bin_dir(self) -> Path:
        d = default_data_dir() / "bin"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _vendor_dirs(self) -> tuple[Path, ...]:
        """Folder tempat biner decoder bisa berada, urut prioritas.

        PyInstaller onedir menaruh berkas data di ``_internal/``, sedangkan
        ``app_root()`` menunjuk folder di atasnya — jadi ``vendor/`` harus
        dicari di KEDUA tempat, plus ``sys._MEIPASS`` untuk mode onefile.
        """
        cands: list[Path] = []
        root = app_root()
        cands.append(root / "vendor" / "decoder")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "vendor" / "decoder")
        cands.append(root / "_internal" / "vendor" / "decoder")
        # hasil build PyInstaller (mesin Electron) saat dijalankan dari repo
        cands.append(root / "engine" / "_internal" / "vendor" / "decoder")
        cands.append(self.bin_dir())
        seen, out = set(), []
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return tuple(out)

    def find_decoder(self) -> Optional[Path]:
        """Cari biner decoder: config → vendor → bin data dir → PATH."""
        if self.cfg.decoder_exe:
            p = Path(self.cfg.decoder_exe).expanduser()
            if p.is_file() and p.name in EXE_NAMES:
                return p
        for base in self._vendor_dirs():
            for name in exe_names():
                p = base / name
                if p.is_file():
                    return p
        for name in exe_names():
            found = shutil.which(name)
            if found:
                return Path(found)
        return None

    # ------------------------------------------------------------------
    def _publish(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self.status, k, v)
        if self.on_change:
            try:
                self.on_change(self.status.to_dict())
            except Exception:
                pass

    def _kill_process(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if not proc:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True,
                               creationflags=0x08000000)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _start_process(self, exe: Path) -> bool:
        exe = Path(exe).resolve()
        try:
            check = subprocess.run(command_for(exe) + ["--protocol-check"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, cwd=str(exe.parent),
                                   timeout=5, creationflags=0x08000000 if IS_WINDOWS else 0)
            if check.returncode != 0 or b"YURLAPS_YLP1=1" not in check.stdout:
                raise RuntimeError("Biner decoder bukan YurLapsDecoder YLP-1")
        except Exception as exc:
            self._publish(state="error", running=False, last_error=str(exc),
                          message="Pasang ulang aplikasi: decoder harus YurLapsDecoder YLP-1.")
            return False
        try:
            args = self.cfg.effective_decoder_args()
        except Exception:
            args = (self.cfg.decoder_args or "").split()
        cmd = command_for(exe) + args
        env = os.environ.copy()
        # rtl-sdr/libwdi di Windows butuh path driver bawaan; biarkan default.
        try:
            with open(self._log_path, "ab") as log:
                log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} mulai: "
                          f"{exe.name} {' '.join(args)}\n".encode())
                log.flush()
                flags = 0
                if IS_WINDOWS:
                    flags = 0x08000000 | 0x00000200   # NO_WINDOW | NEW_PROCESS_GROUP
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    cwd=str(exe.parent), env=env, creationflags=flags,
                )
        except FileNotFoundError:
            self._publish(state="error", last_error="Biner decoder tidak ditemukan",
                          message="File decoder hilang setelah diperiksa.")
            return False
        except OSError as exc:
            msg = str(exc)
            if "Exec format error" in msg or "not a valid Win32" in msg:
                msg = ("Biner decoder tidak cocok dengan sistem operasi ini "
                       "(file .exe Windows dijalankan di Linux/Mac).")
            self._publish(state="error", last_error=msg, message=msg)
            self.engine.log("error", msg)
            return False
        except Exception as exc:
            self._publish(state="error", last_error=str(exc),
                          message=f"Gagal menjalankan decoder: {exc}")
            return False

        with self._lock:
            self._proc = proc
        threading.Thread(target=self._record_decoder_log, args=(proc,), daemon=True).start()
        self._publish(running=True, pid=proc.pid, exe=str(exe), started_at=time.time(),
                      state="starting", last_error="", args=args, freq_hz=None,
                      gain=float(self.cfg.decoder_gain or 0) or None,
                      message=f"Decoder dijalankan (PID {proc.pid})")
        self.engine.log("info", f"Decoder dijalankan di latar belakang: {exe.name}"
                                + (f" ({' '.join(args)})" if args else ""))
        threading.Thread(target=self._wait_radio_line, daemon=True).start()
        return True

    def _record_decoder_log(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            with stream, open(self._log_path, "ab") as log:
                for raw in iter(stream.readline, b""):
                    log.write(public_decoder_log(raw.decode("utf-8", "replace")).encode("utf-8"))
                    log.flush()
        except OSError:
            pass

    def _waiting_for_dongle(self) -> bool:
        """Proses decoder hidup tapi belum/tidak lagi membuka perangkatnya.

        Dipakai untuk mempercepat percobaan ulang: kalau perangkat terputus,
        YurLapsDecoder dapat tetap hidup tanpa data saat dongle belum ada.
        Tanda paling andal: tidak ada baris radio (freq) dan tidak ada data
        ZMQ, plus log terakhir memuat keluhan perangkat.
        """
        # USB presence probing is only advisory. Windows driver stacks can
        # report no device even while YurLapsDecoder can open the RTL-SDR. Let
        # the decoder try first; its own log/error remains authoritative.
        if self.engine.decoder_online:
            return False        # ada data ZMQ = perangkat pasti sudah terbuka
        if self.status.freq_hz:
            return False        # perangkat terbuka & tuner diset = cuma diam, bukan isu perangkat
        tail = (self._tail_log() or "").rsplit("===", 1)[-1].lower()
        hints = ("no supported devices", "usb_open error", "failed to open",
                 "no device", "cannot open", "device not found",
                 "resource busy", "usb_claim_interface", "no tuner")
        if any(h in tail for h in hints):
            return True
        # A quiet track is normal. Missing startup text is not proof that USB
        # was unplugged; only a decoder error line or process exit is.
        return False

    def _wait_radio_line(self, tries: int = 30) -> None:
        for _ in range(tries):
            if self._stop_event.wait(0.5):
                return
            self._scan_log_for_radio()
            if self.status.freq_hz:
                return

    # ------------------------------------------------------------------
    def _scan_log_for_radio(self) -> None:
        """Ambil frekuensi/gain dari baris startup decoder (stderr -> log)."""
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 8000))
                text = fh.read().decode("utf-8", "replace")
        except Exception:
            return
        freq = None
        for line in text.splitlines()[-40:]:
            marker = next((name for name in ("YurLaps Decoder RX: freq=", "RTL-SDR RX: freq=")
                          if name in line), None)
            i = line.find(marker) if marker else -1
            if i >= 0:
                try:
                    freq = float(line[i + len(marker):].split()[0])
                except (ValueError, IndexError):
                    pass
        if freq is not None and freq != self.status.freq_hz:
            self._publish(freq_hz=freq)

    def _tail_log(self, n: int = 4) -> str:
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4000))
                lines = fh.read().decode("utf-8", "replace").strip().splitlines()
            return " | ".join(public_decoder_log(l.strip()) for l in lines[-n:] if l.strip())
        except Exception:
            return ""

    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.cfg.manage_decoder:
            self._publish(mode="off", state="idle",
                          message="Manajemen decoder dimatikan (pakai decoder sendiri).")
            return

        self._publish(mode="managed", state="starting",
                      message="Mencari YurLaps Decoder …")

        while not self._stop_event.is_set():
            if self._proc is None and self.engine.decoder_online:
                self._publish(mode="external", state="running", running=True,
                              message="Menerima data dari decoder eksternal.")
                self._wait(RETRY_SECONDS)
                continue
            self.status.mode = "managed"
            serial = (self.cfg.decoder_device_serial or "").strip()
            profile = (self.cfg.decoder_devices or {}).get(serial, {})
            if not serial or not profile.get("enabled"):
                self._publish(state="waiting", running=False, pid=None,
                              message="Pilih serial RTL-SDR di menu Decoder lalu aktifkan.")
                self._wait(RETRY_SECONDS)
                continue
            # USB/PnP enumeration is advisory. A saved serial can still be
            # opened by librtlsdr when Windows hides it from the scan.
            exe = self.find_decoder()

            if exe is None:
                hint = "Pasang paket aplikasi yang menyertakan YurLapsDecoder."
                self._publish(state="error", running=False,
                              message="Decoder belum tersedia. " + hint)
                self._wait(RETRY_SECONDS)
                continue

            if not self._proc or self._proc.poll() is not None:
                if not self._start_process(exe):
                    self._wait(RETRY_SECONDS)
                    continue

            # pantau proses + aliran data
            started = time.time()
            while not self._stop_event.is_set():
                proc = self._proc
                if proc is None:
                    break
                code = proc.poll()
                if code is not None:
                    tail = self._tail_log()
                    if self._wanted_stop:
                        # restart yang memang kita minta (ubah gain) bukan crash
                        break
                    self.status.restarts += 1
                    no_dongle = self._waiting_for_dongle()
                    self._publish(
                        running=False, pid=None,
                        state="waiting" if no_dongle else "error",
                        last_exit=f"keluar dengan kode {code}",
                        last_error=f"Decoder keluar: {code} (0x{code & 0xffffffff:08X}). {tail}",
                        message=("Menunggu YurLaps Decoder dicolokkan …"
                                 if no_dongle else
                                 f"Decoder berhenti (kode {code}). "
                                 f"Mencoba lagi dalam {int(RETRY_SECONDS)} s."))
                    self.engine.log("warn", f"Decoder berhenti (kode {code}); "
                                            f"restart dalam {int(RETRY_SECONDS)} s")
                    if tail:
                        self.engine.log("warn", f"Log decoder: {tail}")
                    self._wait(RETRY_SECONDS)
                    break

                # A decoder may be silent while no car crosses the loop. A
                # missing USB device is handled by the debounced device scan;
                # do not repeatedly restart a healthy process on silence.
                idle_limit = float(self.cfg.decoder_idle_restart or 15)
                if self._waiting_for_dongle():
                    # Perangkat belum tersambung / belum terbaca: retry 5 detik
                    # sekali agar dongle yang baru dicolok segera diambil alih.
                    dongle_wait = RETRY_SECONDS
                    if time.time() - started > dongle_wait:
                        if self.status.state != "waiting":
                            self._publish(state="waiting", running=False, pid=None,
                                          message="Menunggu YurLaps Decoder terhubung …")
                            self.engine.log("info", "Decoder: menunggu YurLaps Decoder "
                                                    "terhubung")
                        self._kill_process()
                        self.status.restarts += 1     # percobaan sambung ulang
                        break
                    self._wait(0.5)
                    continue
                if time.time() - started > idle_limit and self.status.state == "starting":
                    self._publish(state="running", message="Proses decoder aktif; menunggu passing.")
                if self.engine.decoder_online and self.status.state != "running":
                    self._publish(state="running",
                                  message="Decoder aktif dan mengirim data.")
                self._wait(1.0)

        self._kill_process()
        self._publish(running=False, state="idle", message="Manajemen decoder berhenti.")

    # ------------------------------------------------------------------
    def probe(self, timeout: int = 12) -> dict:
        """Jalankan decoder sekali secara manual untuk melihat apakah YurLaps Decoder
        bisa dibuka. Berguna setelah memasang driver / menyambungkan decoder.

        Kembalikan: ok, output (log), diagnosis.
        """
        exe = self.find_decoder()
        if exe is None:
            return {"ok": False, "available": False,
                    "message": "Biner YurLapsDecoder belum ada di paket aplikasi.",
                    "output": ""}
        try:
            check = subprocess.run(command_for(exe) + ["--protocol-check"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, cwd=str(exe.parent),
                                   timeout=5, creationflags=0x08000000 if IS_WINDOWS else 0)
            if check.returncode != 0 or b"YURLAPS_YLP1=1" not in check.stdout:
                return {"ok": False, "message": "Biner decoder bukan YurLapsDecoder YLP-1.",
                        "output": ""}
        except Exception as exc:
            return {"ok": False, "message": f"Pemeriksaan decoder gagal: {exc}",
                    "output": ""}
        args = self.cfg.effective_decoder_args()
        cmd = command_for(exe) + args
        flags = 0
        if IS_WINDOWS:
            flags = 0x08000000
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=str(exe.parent), creationflags=flags, text=True)
        except Exception as exc:
            return {"ok": False, "message": f"Gagal menjalankan decoder: {exc}",
                    "output": ""}
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, _ = proc.communicate(timeout=5)
            except Exception:
                out = ""
        out = (out or "").strip()
        low = out.lower()

        if any(k in low for k in ("no supported devices", "usb_open error",
                                  "failed to open", "no device", "cannot open",
                                  "device not found")):
            diagnosis, ok = ("YurLaps Decoder tidak bisa dibuka. Biasanya karena driver perangkat belum "
                             "WinUSB, YurLaps Decoder belum tersambung, atau sedang dipakai "
                             "program lain (SDR#, gqrx, Zadig)."), False
        elif any(k in low for k in ("driver", "winusb", "libusb_init")):
            diagnosis, ok = ("Masalah driver/driver library. Pasang WinUSB lewat "
                             "tombol Pasang driver, lalu uji lagi."), False
        elif proc.returncode not in (0, None) and not out:
            diagnosis, ok = (f"Decoder keluar tanpa pesan (kode {proc.returncode}). "
                             f"Coba jalankan manual untuk melihat detail."), False
        else:
            diagnosis, ok = ("Decoder berhasil terhubung ke YurLaps Decoder. Kalau lap masih belum "
                             "terbaca, periksa loop/antena dan gain."), True
        return {"ok": ok, "message": diagnosis, "output": out[-3000:],
                "returncode": proc.returncode, "exe": str(exe)}

    # ------------------------------------------------------------------
    def restart_now(self, reason: str = "diminta dari UI") -> None:
        """Dipanggil dari UI: paksa restart decoder (mis. setelah ubah gain)."""
        self.engine.log("info", f"Restart decoder ({reason})")
        self._wanted_stop = True
        with self._restart_lock:
            self._kill_process()
            self._wanted_stop = False
        self._wake.set()
        self._publish(state="starting",
                      message=f"Decoder dijalankan ulang ({reason}) …")

    def restart_later(self, delay: float = 1.2, reason: str = "setelan diubah") -> None:
        """Debounce: beberapa perubahan cepat (slider gain) = satu restart."""
        with self._restart_lock:
            if self._restart_timer is not None:
                try:
                    self._restart_timer.cancel()
                except Exception:
                    pass
            t = threading.Timer(delay, self.restart_now, kwargs={"reason": reason})
            t.daemon = True
            self._restart_timer = t
            self._publish(state="starting",
                          message=f"Perubahan {reason} — decoder akan dijalankan ulang …")
            t.start()

    def apply_gain(self, gain: float, delay: float = 1.2) -> float:
        """Simpan gain baru lalu restart decoder (kalau dikelola YurLaps)."""
        try:
            gain = max(0.0, min(49.6, float(gain)))
        except (TypeError, ValueError):
            gain = 20.0
        self.cfg.decoder_gain = gain
        try:
            self.cfg.save()
        except Exception:
            pass
        self._publish(gain=gain)
        if self.cfg.manage_decoder and self.status.running:
            self.restart_later(delay, reason=f"gain {gain:g} dB")
        return gain
