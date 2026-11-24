"""Focused tests for Windows RTL-SDR serial selection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import devices
from app.config import Config
from app.decoder_manager import DecoderManager
from app.decoder import DecoderClient, parse_passing


class DummyEngine:
    decoder_online = False
    decoder_last_msg = 0

    def log(self, *_args):
        pass

    def mono(self):
        return 0


class DecoderSelectionTest(unittest.TestCase):
    def test_bundled_decoder_lists_custom_eeprom_serial(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'vendor' / 'decoder' / 'YurLapsDecoder.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'MZ')
            with patch.object(devices, 'IS_WINDOWS', True), \
                 patch.object(devices, 'app_root', return_value=Path(folder)), \
                 patch.object(devices, '_run', return_value='YURLAPS_DEVICE\t0\tYD0001\n'):
                found = devices.list_sdr_devices('0x1234', '0xabcd')
            self.assertEqual([(d['serial'], d['source'], d['selectable']) for d in found],
                             [('YD0001', 'decoder', True)])

    def test_manager_rejects_unpatched_decoder(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('app.decoder_manager.default_data_dir', return_value=Path(folder)):
            exe = Path(folder) / 'YurLapsDecoder.exe'
            exe.write_bytes(b'MZ')
            manager = DecoderManager(Config(), DummyEngine())
            result = type('Result', (), {'returncode': 1, 'stdout': b'', 'stderr': b'unknown option'})()
            with patch('app.decoder_manager.subprocess.run', return_value=result), \
                 patch('app.decoder_manager.subprocess.Popen') as launch:
                self.assertFalse(manager._start_process(exe))
                launch.assert_not_called()

    def test_saved_old_decoder_path_falls_back_to_bundled_decoder(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('app.decoder_manager.default_data_dir', return_value=Path(folder)), \
             patch('app.decoder_manager.app_root', return_value=Path(folder)):
            old = Path(folder) / 'old_decoder.exe'
            old.write_bytes(b'MZ')
            bundled = Path(folder) / 'vendor' / 'decoder' / 'YurLapsDecoder.exe'
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b'MZ')
            manager = DecoderManager(Config(decoder_exe=str(old)), DummyEngine())
            self.assertEqual(manager.find_decoder(), bundled)

    def test_saved_serial_is_tried_even_if_windows_scan_misses_it(self):
        cfg = Config(decoder_device_serial='YD0001',
                     decoder_devices={'YD0001': {'enabled': True, 'gain': 20}})
        with tempfile.TemporaryDirectory() as folder, \
             patch('app.decoder_manager.default_data_dir', return_value=Path(folder)):
            manager = DecoderManager(cfg, DummyEngine())
            manager._device_present = False
            with patch.object(manager, 'find_decoder', return_value=Path(folder) / 'YurLapsDecoder.exe'), \
                 patch.object(manager, '_start_process', side_effect=lambda _: manager._stop_event.set() or False) as launch:
                manager.run()
            launch.assert_called_once()

    def test_only_opn_and_amb_reach_engine_even_with_old_decoder(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(
                (passing.transponder_type, passing.transponder_id))
            def on_timesync(self, _parts): self.seen.append('timesync')

        engine = Engine()
        client = DecoderClient(engine, 'inproc://test')
        for typ in ('RC3', 'RC4', 'RC4Hybrid', 'Vostok'):
            client.process_message(f'P 100 {typ} 44 -30 2 90000')
            client.process_message(f'T 100 {typ} 44 123')
            self.assertIsNone(parse_passing(f'P 100 {typ} 44 -30 2 90000'))
        client.process_message('L 100 START -30')
        client.process_message('L 101 DONE 44 5')
        self.assertEqual(engine.seen, [])
        self.assertEqual(client.rx_counts['bad'], 0)
        client.process_message('P 102 YUL 45 -30 2 90000')
        client.process_message('P 103 YUL 46 -30 2 90000')
        self.assertEqual(engine.seen, [('YUL', 45), ('YUL', 46)])
        self.assertEqual(parse_passing('P 103 YUL 46 -30 2 90000').transponder_type, 'YUL')

    def test_passing_jitter_is_reordered_by_decoder_timestamp(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(
                (passing.transponder_id, passing.decoder_timecode))
        engine = Engine()
        client = DecoderClient(engine, "inproc://test", reorder_ms=40)
        client._reorder_enabled = True
        client._dispatch_message("P 120 YUL 11 -30 2 90000")
        client._dispatch_message("P 100 YUL 22 -30 2 90000")
        self.assertEqual(engine.seen, [])
        client._flush_pending(force=True)
        self.assertEqual(engine.seen, [(22, 100), (11, 120)])

    def test_opn_and_amb_share_one_timestamp_order(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(
                (passing.transponder_type, passing.transponder_id))

        engine = Engine()
        client = DecoderClient(engine, "inproc://test", reorder_ms=40)
        client._reorder_enabled = True
        client._dispatch_message("P 120 YUL 11 -30 2 90000")
        client._dispatch_message("P 100 YUL 22 -30 2 90000")
        client._flush_pending(force=True)
        self.assertEqual(engine.seen, [("YUL", 22), ("YUL", 11)])

    def test_three_passings_wait_for_quiet_window(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(passing.transponder_id)

        engine = Engine()
        client = DecoderClient(engine, 'inproc://test', reorder_ms=40)
        client._reorder_enabled = True
        with patch('app.decoder.time.monotonic') as clock:
            for arrival, timestamp, transponder in [(1.0, 120, 1),
                                                    (1.02, 115, 2),
                                                    (1.035, 100, 3)]:
                clock.return_value = arrival
                client._dispatch_message(f'P {timestamp} YUL {transponder} -30 2 90000')
            clock.return_value = 1.074
            client._flush_pending()
            self.assertEqual(engine.seen, [])
            clock.return_value = 1.076
            client._flush_pending()
        self.assertEqual(engine.seen, [3, 2, 1])

    def test_continuous_passings_flush_at_max_wait(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(passing.transponder_id)

        engine = Engine()
        client = DecoderClient(engine, 'inproc://test', reorder_ms=40, max_reorder_ms=150)
        client._reorder_enabled = True
        with patch('app.decoder.time.monotonic') as clock:
            for i, arrival in enumerate((1.0, 1.035, 1.07, 1.105, 1.14)):
                clock.return_value = arrival
                client._dispatch_message(f'P {100+i} YUL {i+1} -30 2 90000')
            self.assertEqual(engine.seen, [])
            clock.return_value = 1.151
            client._flush_pending()
        self.assertEqual(engine.seen, [1, 2, 3, 4, 5])

    def test_decoder_clock_reset_does_not_mix_batches(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing): self.seen.append(passing.decoder_timecode)

        engine = Engine()
        client = DecoderClient(engine, 'inproc://test', reorder_ms=40)
        client._reorder_enabled = True
        client._dispatch_message('P 100000 YUL 1 -30 2 90000')
        client._dispatch_message('P 200 YUL 2 -30 2 90000')
        self.assertEqual(engine.seen, [100000])
        client._flush_pending(force=True)
        self.assertEqual(engine.seen, [100000, 200])

    def test_deferred_callback_failure_keeps_next_passing(self):
        class Engine:
            def __init__(self): self.seen = []
            def log(self, *_args): pass
            def on_passing(self, passing):
                self.seen.append(passing.transponder_id)
                if passing.transponder_id == 1:
                    raise RuntimeError('storage failed')

        engine = Engine()
        client = DecoderClient(engine, 'inproc://test', reorder_ms=40)
        client._reorder_enabled = True
        with tempfile.TemporaryDirectory() as folder, \
             patch('app.config.default_data_dir', return_value=Path(folder)):
            client._dispatch_message('P 100 YUL 1 -30 2 90000')
            client._dispatch_message('P 101 YUL 2 -30 2 90000')
            with self.assertLogs('app.decoder', level='ERROR'):
                client._flush_pending(force=True)
            self.assertIn('storage failed', (Path(folder) / 'receiver-errors.log').read_text())
        self.assertEqual(engine.seen, [1, 2])

    def test_windows_pnp_serial_and_duplicate_rejection(self):
        rows = [
            {"Name": "RTL-SDR", "PNPDeviceID": "USB\\VID_0BDA&PID_2838\\YD0001", "Service": "WinUSB"},
            {"Name": "RTL-SDR", "PNPDeviceID": "USB\\VID_0BDA&PID_2838\\YD0001", "Service": "WinUSB"},
        ]
        with patch.object(devices, "IS_WINDOWS", True), \
             patch.object(devices.ctypes, "CDLL", side_effect=OSError("missing")), \
             patch.object(devices.ctypes.util, "find_library", return_value=None), \
             patch.object(devices, "_run", return_value=json.dumps(rows)):
            found = devices.list_sdr_devices()
            self.assertEqual([x["serial"] for x in found], ["YD0001", "YD0001"])
            self.assertTrue(all(not x["selectable"] for x in found))
            self.assertTrue(devices.detect().present)

    def test_location_id_is_not_serial(self):
        rows = [{"Name": "RTL-SDR", "PNPDeviceID": "USB\\VID_0BDA&PID_2838\\6&ABC&0&2"}]
        with patch.object(devices, "IS_WINDOWS", True), \
             patch.object(devices.ctypes, "CDLL", side_effect=OSError("missing")), \
             patch.object(devices.ctypes.util, "find_library", return_value=None), \
             patch.object(devices, "_run", return_value=json.dumps(rows)):
            self.assertEqual(devices.list_sdr_devices()[0]["serial"], "")

    def test_saved_serial_and_gain_are_passed_to_decoder(self):
        cfg = Config(decoder_device_serial="YD0001", decoder_gain=28)
        self.assertEqual(cfg.effective_decoder_args()[:4], ["-g", "28", "-d", "YD0001"])
        cfg.zmq_endpoint = "tcp://127.0.0.1:5560"
        self.assertEqual(cfg.effective_decoder_args()[-2:], ["-p", "5560"])

    def test_legacy_serial_port_and_gain_migrate_together(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"decoder_args": "-g 30 -d YD0001 -p 5560"}))
            with patch.object(Config, "path", return_value=path):
                cfg = Config.load()
            self.assertEqual(cfg.decoder_devices["YD0001"], {"gain": 30, "enabled": True})
            self.assertEqual(cfg.zmq_endpoint, "tcp://127.0.0.1:5560")

    def test_unselected_device_never_starts_process(self):
        cfg = Config(decoder_auto_download=False)
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.decoder_manager.default_data_dir", return_value=Path(folder)):
            manager = DecoderManager(cfg, DummyEngine())
            manager.device_changed(True)
            manager.start()
            manager.join(timeout=0.1)
            self.assertEqual(manager.status.state, "waiting")
            self.assertIsNone(manager._proc)
            manager.stop()
            manager.join(timeout=1)

    def test_quiet_decoder_is_not_treated_as_missing_usb(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.decoder_manager.default_data_dir", return_value=Path(folder)):
            manager = DecoderManager(Config(), DummyEngine())
            manager.status.started_at = 1
            manager._log_path.write_text("=== old start\nno supported devices\n=== current start\n")
            self.assertFalse(manager._waiting_for_dongle())
            manager._log_path.write_text("=== current start\nno supported devices\n")
            self.assertTrue(manager._waiting_for_dongle())

    def test_temporary_usb_scan_miss_keeps_running_decoder(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("app.decoder_manager.default_data_dir", return_value=Path(folder)):
            manager = DecoderManager(Config(), DummyEngine())
            process = type("Process", (), {"poll": lambda self: None})()
            manager._proc = process
            manager.device_changed(True)
            with patch.object(manager, "_kill_process") as kill, \
                 patch.object(manager, "restart_now") as restart:
                manager.device_changed(False)
                manager.device_changed(True)
            kill.assert_not_called()
            restart.assert_not_called()
            self.assertIs(manager._proc, process)


if __name__ == "__main__":
    unittest.main()
