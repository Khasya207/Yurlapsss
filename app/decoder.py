"""Subscriber ZeroMQ untuk YurLapsDecoder (satu-satunya sumber data lap).

Keluaran decoder (lihat decoder/README.md dan decoder/docs/):

    P <ts_ms> YUL <id> <rssi_dbfs> <hits> <pass_duration_us> <mer_db>
    S <ts_ms> <noise_power> <dc_offset> <frames_received> <frames_processed> <jitter_p95_ms>

Pesan dipisah spasi dan bertipe posisional, jadi parsing dibuat defensif
(tidak unpack seluruh baris).
"""
from __future__ import annotations

import threading
import logging
import traceback
import time
from typing import Callable, Optional

from .engine import Engine, Passing


class DecoderClient(threading.Thread):
    def __init__(self, engine: Engine, endpoint: str, poll_ms: int = 250,
                 reorder_ms: int = 40, max_reorder_ms: int = 150):
        super().__init__(name="decoder-zmq", daemon=True)
        self.engine = engine
        self.endpoint = endpoint
        self.poll_ms = poll_ms
        # The decoder may finish estimating a later crossing first. Collect a
        # short burst before dispatching by the decoder clock.
        self.reorder_window = max(0.0, float(reorder_ms) / 1000.0)
        self.max_reorder_window = max(self.reorder_window,
                                      float(max_reorder_ms) / 1000.0)
        self._reorder_enabled = False
        self._pending_passings: list[tuple[float, Passing]] = []
        self._pending_lock = threading.RLock()
        self._stop_event = threading.Event()
        self.connected = False
        self._got_message = False
        self._last_error: Optional[str] = None
        self.rx_counts = {"P": 0, "S": 0, "T": 0, "bad": 0}

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            import zmq  # type: ignore
        except ImportError:
            self.engine.log("error", "pyzmq tidak terpasang — decoder tidak bisa dibaca "
                                     "(pip install pyzmq)")
            return

        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.setsockopt(zmq.LINGER, 0)
        # A short poll lets the reorder window expire even if no next packet
        # arrives after a close finish.
        sock.setsockopt(zmq.RCVTIMEO, min(self.poll_ms, 10))
        sock.connect(self.endpoint)
        self._reorder_enabled = True

        last_connect_note = 0.0
        while not self._stop_event.is_set():
            try:
                msg = sock.recv_string()
            except zmq.Again:
                self._safe_flush_pending()
                if not self._got_message and time.monotonic() - last_connect_note > 20:
                    last_connect_note = time.monotonic()
                    self.engine.log(
                        "info", f"Menunggu decoder di {self.endpoint} …")
                continue
            except Exception as exc:  # pragma: no cover
                self._last_error = str(exc)
                self.engine.log("error", f"ZeroMQ: {exc}")
                time.sleep(0.5)
                continue

            self.process_message(msg)
            self._safe_flush_pending()

        self._safe_flush_pending(force=True)
        try:
            sock.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def process_message(self, msg: str) -> bool:
        """A failed callback must not terminate the only decoder subscriber.

        Do not replay a partially processed crossing: that could count it twice.
        Keep the traceback and expose the failure to the operator instead.
        """
        try:
            self._dispatch_message(msg)
            return True
        except Exception as exc:
            self._report_processing_error(exc)
            return False

    def _report_processing_error(self, exc: Exception) -> None:
        self._last_error = str(exc)
        logging.getLogger(__name__).exception("Decoder processing failed")
        try:
            from .config import default_data_dir
            folder = default_data_dir()
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'receiver-errors.log').open('a', encoding='utf-8') as log:
                log.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + traceback.format_exc() + '\n')
        except OSError:
            pass
        try:
            self.engine.log("error", "Passing gagal diproses; periksa hasil sesi: " + str(exc))
        except Exception:
            pass

    def _safe_flush_pending(self, force: bool = False) -> None:
        try:
            self._flush_pending(force=force)
        except Exception as exc:
            self._report_processing_error(exc)

    def _dispatch_message(self, msg: str) -> None:
        if not self._got_message:
            self._got_message = True
            self.connected = True
            self.engine.log("ok", f"Data decoder diterima dari {self.endpoint}")
        parts = msg.split()
        if not parts:
            return
        kind = parts[0]
        try:
            if kind == "P":
                if len(parts) < 7:
                    self.rx_counts["bad"] += 1
                    return
                if parts[2] != "YUL":
                    return
                self.rx_counts["P"] += 1
                p = Passing(
                    transponder_id=int(parts[3]),
                    transponder_type=parts[2],
                    rssi=_f(parts, 4),
                    hit_count=_i(parts, 5),
                    pass_duration_us=_i(parts, 6),
                    mer=_f(parts, 7),
                    decoder_timecode=int(parts[1]),
                    raw=msg,
                )
                if self._reorder_enabled and self.reorder_window > 0:
                    with self._pending_lock:
                        latest = max((item[1].decoder_timecode for item in self._pending_passings
                                      if item[1].decoder_timecode is not None), default=None)
                    if latest is not None and p.decoder_timecode is not None \
                            and latest - p.decoder_timecode > 10_000:
                        # A large clock jump is a new decoder epoch, not a
                        # close crossing to sort with the old batch.
                        self._flush_pending(force=True)
                    with self._pending_lock:
                        self._pending_passings.append((time.monotonic(), p))
                    self._flush_pending()
                else:
                    self.engine.on_passing(p)
            elif kind == "S":
                status_ts = _i(parts, 1)
                with self._pending_lock:
                    latest = max((item[1].decoder_timecode for item in self._pending_passings
                                  if item[1].decoder_timecode is not None), default=None)
                if latest is not None and status_ts is not None and status_ts < latest:
                    self._flush_pending(force=True)
                self._flush_pending()
                self.rx_counts["S"] += 1
                self.engine.decoder_status_message(parts)
            elif kind == "T":
                # YLP-1 tidak punya pesan time sync; abaikan jika ada.
                self._flush_pending()
                return
            elif kind == "L":
                return
            else:
                self.rx_counts["bad"] += 1
        except (ValueError, IndexError):
            self.rx_counts["bad"] += 1

    def _flush_pending(self, force: bool = False) -> None:
        """Dispatch passing packets in decoder-clock order after the jitter window."""
        if not self._reorder_enabled and not force:
            return
        now = time.monotonic()
        with self._pending_lock:
            if force:
                ready = self._pending_passings[:]
                self._pending_passings.clear()
            else:
                # Wait for silence after the newest packet, with a hard cap
                # so a continuous stream cannot keep the batch forever.
                if not self._pending_passings:
                    return
                newest = max(item[0] for item in self._pending_passings)
                oldest = min(item[0] for item in self._pending_passings)
                if (now - newest < self.reorder_window and
                        now - oldest < self.max_reorder_window):
                    return
                ready = self._pending_passings[:]
                self._pending_passings.clear()
        ready.sort(key=lambda item: (item[1].decoder_timecode is None,
                                     item[1].decoder_timecode if item[1].decoder_timecode is not None else 0,
                                     item[0]))
        for _, passing in ready:
            try:
                self.engine.on_passing(passing)
            except Exception as exc:
                # One failing callback must not kill the only subscriber or
                # discard the remaining passings in the same batch.
                self._report_processing_error(exc)


def _i(parts: list[str], idx: int) -> Optional[int]:
    try:
        return int(parts[idx])
    except (IndexError, ValueError):
        return None


def _f(parts: list[str], idx: int) -> Optional[float]:
    try:
        return float(parts[idx])
    except (IndexError, ValueError):
        return None


def parse_passing(msg: str) -> Optional[Passing]:
    """Helper untuk tes/utilitas."""
    parts = msg.split()
    if len(parts) < 4 or parts[0] != "P" or parts[2] != "YUL":
        return None
    try:
        timestamp = int(parts[1])
        transponder = int(parts[3])
    except (TypeError, ValueError):
        # Malformed decoder lines are ignored by the stream worker; keep the
        # public helper equally safe for callers parsing logs manually.
        return None
    if timestamp < 0 or transponder < 0:
        return None
    return Passing(
        transponder_id=transponder,
        transponder_type=parts[2],
        rssi=_f(parts, 4),
        hit_count=_i(parts, 5),
        pass_duration_us=_i(parts, 6),
        mer=_f(parts, 7),
        decoder_timecode=timestamp,
        raw=msg,
    )
