import tempfile
import unittest
import uuid
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from app.club import ClubStore
from app.events import EventStore
from app.event_sync import sync_events
from app.race import RaceEngine
from app.cloud import CloudService
from app.webapp import make_handler


class EventTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "club.sqlite3"
        self.club = ClubStore(self.path)
        self.addCleanup(self.club.close)
        self.track = self.club.save_track({"name": "Tandem Track", "active": True})
        self.alice = self.club.save_member({"name": "Alice", "transponder": [101, 102]})
        self.bob = self.club.save_member({"name": "Bob", "transponder": [201]})
        self.store = EventStore(self.club)
        self.event = self.store.create({"name": "Series 1", "track_id": self.track["id"],
                                        "classes": ["Buggy", "Touring"], "best_legs": 2})
        self.class_id = self.event["classes"][0]["id"]

    def test_offline_restart_and_one_car_per_class(self):
        first = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        with self.assertRaisesRegex(ValueError, "satu mobil"):
            self.store.add_entry(self.event["id"], {"class_id": self.class_id,
                "member_id": self.alice["id"], "transponder": 102})
        self.assertFalse(first["approved"])
        self.club.close()
        reopened = ClubStore(self.path)
        self.addCleanup(reopened.close)
        self.club = reopened
        self.store = EventStore(reopened)
        self.assertEqual(self.store.get(self.event["id"])["entries"][0]["transponder"], "101")

    def test_schedule_move_keeps_sequence_and_protects_saved_sessions(self):
        first = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "kind": "qualifying", "name": "Q1", "group": "Semua", "cfg": {}})
        second = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "kind": "qualifying", "name": "Q2", "group": "Semua", "cfg": {}})
        moved = self.store.move_stage(self.event["id"], second["id"], "up")
        self.assertEqual([s["name"] for s in moved["stages"]], ["Q2", "Q1"])
        self.assertEqual([s["sequence"] for s in moved["stages"]], [1, 2])
        self.store.mark_stage(self.event["id"], second["id"], "live")
        with self.assertRaisesRegex(ValueError, "Selesaikan sesi Event"):
            self.store.mark_stage(self.event["id"], first["id"], "live")
        with self.assertRaisesRegex(ValueError, "melewati"):
            self.store.move_stage(self.event["id"], first["id"], "up")

    def test_competition_recipe_and_grouped_rounds(self):
        event_id = self.event["id"]
        configured = self.store.configure_competition(event_id, self.class_id, {
            "name": "Buggy Pro", "group_min": 2, "group_max": 2,
            "qualifier_rounds": 2, "final_rounds": 1,
            "qualifier_target": 240, "final_target": 300})
        self.assertEqual(configured["classes"][0]["name"], "Buggy Pro")
        self.assertEqual(configured["classes"][0]["config"]["qualifier_rounds"], 2)
        for member, tp in ((self.alice, 101), (self.bob, 201)):
            entry = self.store.add_entry(event_id, {"class_id": self.class_id,
                "member_id": member["id"], "transponder": tp})
            self.store.set_entry(event_id, entry["id"], approved=True)
        stages = self.store.generate_round(event_id, {"class_id": self.class_id,
            "kind": "qualifying", "name": "Qualifier", "rounds": 2,
            "group_max": 2, "cfg": {"target": "time", "value": 240,
            "target_time_ms": 240000, "start_mode": "staggered", "rank": "result"}})
        self.assertEqual([s["name"] for s in stages],
                         ["Qualifier R1 · Grup A", "Qualifier R2 · Grup A"])
        self.assertEqual([s["sequence"] for s in stages], [1, 2])
        self.assertEqual(len(stages[0]["entry_ids"]), 2)

    def test_schedule_generation_rejects_invalid_input_without_partial_stages(self):
        event_id = self.event["id"]
        with self.assertRaisesRegex(ValueError, "Setujui peserta"):
            self.store.generate_round(event_id, {"class_id": self.class_id,
                "kind": "final", "name": "Final", "rounds": 2, "group_max": 2})
        self.assertEqual(self.store.get(event_id)["stages"], [])
        with self.assertRaisesRegex(ValueError, "Minimum grup"):
            self.store.configure_competition(event_id, self.class_id,
                {"group_min": 12, "group_max": 4})
        self.assertNotIn("config", self.store.get(event_id)["classes"][0])

    def test_competition_can_be_added_and_removed_before_entries(self):
        event_id = self.event["id"]
        updated = self.store.add_competition(event_id, "Truggy")
        new_id = updated["classes"][-1]["id"]
        self.assertEqual(updated["classes"][-1]["name"], "Truggy")
        with self.assertRaisesRegex(ValueError, "sudah dipakai"):
            self.store.add_competition(event_id, "truggy")
        self.store.remove_competition(event_id, new_id)
        self.assertEqual(len(self.store.get(event_id)["classes"]), 2)

    def test_meeting_shell_settings_and_competition_cascade(self):
        meeting = self.store.create({"name": "Series 2", "date": "2026-11-15",
                                     "track_id": self.track["id"], "location": "Binjai"})
        self.assertEqual(meeting["classes"], [])
        self.assertEqual(meeting["location"], "Binjai")
        with self.assertRaisesRegex(ValueError, "competition"):
            self.store.set_status(meeting["id"], "registration")
        updated = self.store.update_meeting(meeting["id"], {
            "name": "Series 2 Final", "track_id": self.track["id"],
            "date": "2026-11-16", "series": "Round 2",
            "format": "heat_final", "best_legs": 2, "points_table": "20,15,10"})
        self.assertEqual(updated["format"], "heat_final")
        self.assertEqual(updated["points_table"], [20, 15, 10])
        competition = self.store.add_competition(meeting["id"], "Buggy")["classes"][0]
        entry = self.store.add_entry(meeting["id"], {"class_id": competition["id"],
            "member_id": self.alice["id"], "transponder": 101})
        self.store.add_stage(meeting["id"], {"class_id": competition["id"],
            "kind": "heat", "name": "Heat 1", "cfg": {}})
        other_track = self.club.save_track({"name": "Track B"})
        self.assertEqual(self.store.update_meeting(meeting["id"],
                         {"track_id": other_track["id"]})["track_id"], other_track["id"])
        with self.assertRaisesRegex(ValueError, "Hapus sesi"):
            self.store.update_meeting(meeting["id"], {"format": "qualifying_main"})
        removed = self.store.remove_competition(meeting["id"], competition["id"])
        self.assertEqual(removed["classes"], [])
        self.assertEqual(removed["entries"], [])
        self.assertEqual(removed["stages"], [])
        self.assertEqual(entry["name"], "Alice")

    def test_repeat_keeps_previous_result_and_delete_uses_tombstone(self):
        event_id = self.event["id"]
        entry = self.store.add_entry(event_id, {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        self.store.set_entry(event_id, entry["id"], approved=True, checked_in=True)
        stage = self.store.add_stage(event_id, {"class_id": self.class_id,
            "kind": "final", "name": "Final A", "entry_ids": [entry["id"]], "cfg": {}})
        self.store.set_status(event_id, "registration")
        self.store.set_status(event_id, "running")
        self.store.mark_stage(event_id, stage["id"], "live")
        with self.assertRaisesRegex(ValueError, "Selesaikan sesi"):
            self.store.delete_meeting(event_id)
        self.club.simpan_race({"id": "old-result", "name": "Final A", "started_at": 1,
            "drivers": [{"name": "Alice", "transponder": 101, "pos": 1,
                         "result_status": "FIN", "laps": [12000]}]})
        self.store.mark_stage(event_id, stage["id"], "saved", "old-result")
        repeated = self.store.repeat_stage(event_id, stage["id"])
        self.assertEqual(repeated["entry_ids"], [entry["id"]])
        self.assertEqual(repeated["status"], "planned")
        self.assertNotEqual(repeated["id"], stage["id"])
        self.assertEqual(self.store.stage(event_id, stage["id"])[1]["race_id"], "old-result")
        self.club.simpan_race({"id": "new-result", "name": "Final A Ulang", "started_at": 2,
            "drivers": [{"name": "Alice", "transponder": 101, "pos": 2,
                         "result_status": "FIN", "laps": [11000]}]})
        self.store.mark_stage(event_id, repeated["id"], "live")
        self.store.mark_stage(event_id, repeated["id"], "saved", "new-result")
        self.assertEqual(self.store.stage(event_id, stage["id"])[1]["superseded_by"], repeated["id"])
        self.assertEqual(self.store.standings(event_id)[0]["drivers"][0]["points"], 18)
        self.assertEqual(len([result for result in self.store.results(event_id)
                              if result["status"] == "saved"]), 2)
        with self.assertRaisesRegex(ValueError, "digantikan"):
            self.store.repeat_stage(event_id, stage["id"])
        again = self.store.repeat_stage(event_id, repeated["id"])
        self.assertEqual(again["name"], "Final A · Ulang 2")
        self.store.remove_stage(event_id, again["id"])
        with self.assertRaisesRegex(ValueError, "ulangannya"):
            self.store.remove_stage(event_id, stage["id"])
        self.store.remove_stage(event_id, repeated["id"])
        self.assertNotIn("superseded_by", self.store.stage(event_id, stage["id"])[1])
        self.assertEqual(self.store.standings(event_id)[0]["drivers"][0]["points"], 25)
        deleted = self.store.delete_meeting(event_id)
        self.assertTrue(deleted["deleted_at"])
        self.assertFalse(any(item["id"] == event_id for item in self.store.list()))
        with self.assertRaisesRegex(ValueError, "dihapus"):
            self.store.get(event_id)
        self.assertTrue(self.store.sync_state(event_id)["pending"])

    def test_meeting_api_create_update_delete(self):
        context = SimpleNamespace(cfg=SimpleNamespace(access_token=""), engine=None,
                                  club=self.club)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(path, data):
            request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                              data=json.dumps(data).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        created = post("/api/event/create", {"name": "API Meeting",
                       "track_id": self.track["id"]})["event"]
        self.assertEqual(created["classes"], [])
        updated = post("/api/event/meeting/update", {"event_id": created["id"],
                       "name": "API Meeting 2", "location": "Binjai"})["event"]
        self.assertEqual(updated["location"], "Binjai")
        deleted = post("/api/event/meeting/delete", {"event_id": created["id"]})["event"]
        self.assertTrue(deleted["deleted_at"])

    def test_approval_checkin_and_stage_entry_validation(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        with self.assertRaisesRegex(ValueError, "Setujui"):
            self.store.set_entry(self.event["id"], alice["id"], checked_in=True)
        self.store.set_status(self.event["id"], "registration")
        with self.assertRaisesRegex(ValueError, "check-in"):
            self.store.set_status(self.event["id"], "running")
        self.store.set_entry(self.event["id"], alice["id"], approved=True, checked_in=True)
        with self.assertRaisesRegex(ValueError, "Pilih peserta"):
            self.store.add_stage(self.event["id"], {"class_id": self.class_id,
                "kind": "final", "name": "Final A", "group": "A"})
        stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "kind": "final", "name": "Final A", "group": "A", "entry_ids": [alice["id"]]})
        self.assertEqual(stage["status"], "planned")
        self.store.remove_stage(self.event["id"], stage["id"])
        self.assertEqual(self.store.get(self.event["id"])["stages"], [])
        self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "kind": "final", "name": "Final B", "group": "A", "entry_ids": [alice["id"]]})
        self.assertEqual(self.store.set_status(self.event["id"], "running")["status"], "running")

    def test_standings_use_best_legs_and_ignore_qualifying(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        bob = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.bob["id"], "transponder": 201})
        for index, kind in enumerate(("qualifying", "final", "final", "final"), 1):
            stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
                "kind": kind, "name": f"R{index}", "group": "A",
                "entry_ids": [alice["id"], bob["id"]]})
            race_id = f"race-{index}"
            self.club.simpan_race({"id": race_id, "name": f"R{index}", "started_at": index,
                "drivers": [
                    {"name": "Alice", "transponder": 101, "pos": 1 if index != 3 else 2,
                     "result_status": "FIN", "laps": [10000]},
                    {"name": "Bob", "transponder": 201, "pos": 2 if index != 3 else 1,
                     "result_status": "FIN", "laps": [11000]},
                ]})
            self.store.mark_stage(self.event["id"], stage["id"], "live")
            self.store.mark_stage(self.event["id"], stage["id"], "saved", race_id)
        board = self.store.standings(self.event["id"])[0]["drivers"]
        self.assertEqual(board[0]["entry_id"], alice["id"])
        self.assertEqual(board[0]["points"], 50)
        self.assertEqual(board[1]["entry_id"], bob["id"])
        self.assertEqual(board[1]["points"], 43)

    def test_event_sync_upload_pull_and_conflict(self):
        club_id = str(uuid.uuid4())
        self.club.set_club(club_id=club_id)
        event = self.store.create({"name": "Online Event", "track_id": self.track["id"],
                                   "classes": ["Buggy"]})

        class Client:
            remote = {}

            def _request(self, path, method="GET", body=None):
                if path.startswith("/rest/v1/rpc/save_club_event"):
                    key = body["p_event_id"]
                    old = self.remote.get(key)
                    if (old["revision"] if old else 0) != body["p_base_revision"]:
                        return {"conflict": True, "current": copy.deepcopy(old)}
                    row = {"id": key, "club_id": body["p_club_id"],
                           "document": copy.deepcopy(body["p_document"]),
                           "revision": (old["revision"] if old else 0) + 1}
                    self.remote[key] = row
                    return {"record": copy.deepcopy(row)}
                return copy.deepcopy(list(self.remote.values()))

        client = Client()
        sync_events(self.club, club_id, client)
        self.assertFalse(self.store.sync_state(event["id"])["pending"])
        client.remote[event["id"]]["document"]["date"] = "2026-11-12"
        client.remote[event["id"]]["document"]["revision"] += 1
        client.remote[event["id"]]["revision"] += 1
        sync_events(self.club, club_id, client)
        self.assertEqual(self.store.get(event["id"])["date"], "2026-11-12")
        self.store.set_status(event["id"], "registration")
        client.remote[event["id"]]["revision"] += 1
        client.remote[event["id"]]["document"]["revision"] += 1
        sync_events(self.club, club_id, client)
        self.assertTrue(self.store.sync_state(event["id"])["conflict"])
        self.assertEqual(self.store.get(event["id"])["status"], "registration")
        self.store.resolve_sync_conflict(event["id"], "remote")
        self.assertFalse(self.store.sync_state(event["id"])["conflict"])

    def test_event_stage_uses_race_engine_and_keeps_result(self):
        alice = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        self.store.set_entry(self.event["id"], alice["id"], approved=True, checked_in=True)
        stage = self.store.add_stage(self.event["id"], {"class_id": self.class_id,
            "name": "Final A", "kind": "final", "cfg": {"target": "laps", "value": 1,
            "cd": 0, "rank": "position"}})
        self.store.set_status(self.event["id"], "registration")
        self.store.set_status(self.event["id"], "running")
        race = RaceEngine(self.club)
        race.set_track(self.track["id"])
        race.set_cfg(stage["cfg"])
        race.set_peserta([{"member_id": self.alice["id"], "transponder": 101}])
        race.mulai()
        race.catat(101, decoder_ts=10000)
        race.catat(101, decoder_ts=20000)
        result = race.simpan("Final A · Series 1")
        self.store.mark_stage(self.event["id"], stage["id"], "live")
        self.store.mark_stage(self.event["id"], stage["id"], "saved", result["id"])
        self.assertEqual(self.store.results(self.event["id"])[0]["drivers"][0]["laps"], [10000])
        self.assertEqual(self.store.standings(self.event["id"])[0]["drivers"][0]["points"], 25)
        self.assertEqual(self.store.get(self.event["id"])["published_standings"][0]["drivers"][0]["points"], 25)

    def test_offline_event_and_track_attach_on_first_login(self):
        root = self.path.parent
        local = ClubStore(root / "yurlaps_club.sqlite3")
        self.addCleanup(local.close)
        track = local.save_track({"name": "Offline Track", "active": True})
        guest = local.save_member({"name": "Guest", "transponder": [303]}, _source="local_guest")
        event = EventStore(local).create({"name": "Offline Event", "track_id": track["id"],
                                          "classes": ["Buggy"]})
        EventStore(local).add_entry(event["id"], {"class_id": event["classes"][0]["id"],
                                                   "member_id": guest["id"], "transponder": 303})
        club_id = str(uuid.uuid4())
        target = ClubStore(root / "cloud.sqlite3")
        self.addCleanup(target.close)
        target.set_club(club_id=club_id)
        CloudService(root)._migrate_unassigned_local_history(target, club_id)
        self.assertEqual(target.track(track["id"])["name"], "Offline Track")
        self.assertEqual(EventStore(target).get(event["id"])["club_id"], club_id)
        self.assertEqual(target.members()[0]["name"], "Guest")

    def test_guest_identity_change_requires_reapproval(self):
        entry = self.store.add_entry(self.event["id"], {"class_id": self.class_id,
            "member_id": self.alice["id"], "transponder": 101})
        self.store.set_entry(self.event["id"], entry["id"], approved=True, checked_in=True)
        self.club.delete_member(self.alice["id"])
        new_id = str(uuid.uuid4())
        self.club.save_member({"id": new_id, "name": "Account Owner", "transponder": [101]}, _source="cloud")
        changed = self.store.reconcile_entry(self.event["id"], entry["id"])
        self.assertEqual(changed["member_id"], new_id)
        self.assertFalse(changed["approved"])
        self.assertFalse(changed["checked_in"])


if __name__ == "__main__":
    unittest.main()
