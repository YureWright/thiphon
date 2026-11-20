import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app


class NormalizeTests(unittest.TestCase):
    def test_normalize_submission(self):
        payload = {
            "data": {
                "request_id": "req-1",
                "status": "completed",
                "songs": [{
                    "song_id": "song-1",
                    "title": "茉莉花",
                    "status": "completed",
                    "audio_url": "https://example.test/a.mp3",
                    "duration": 180,
                }],
            }
        }
        result = app.normalize_submission(payload)
        self.assertEqual(result["request_id"], "req-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["songs"]), 1)
        self.assertEqual(result["songs"][0]["song_id"], "song-1")

    def test_decode_audio(self):
        raw = b"RIFF" + b"\x00" * 2048
        encoded = base64.b64encode(raw).decode("ascii")
        name, data = app.decode_audio("test.wav", encoded)
        self.assertEqual(name, "test.wav")
        self.assertEqual(data, raw)

    def test_decode_audio_rejects_extension(self):
        encoded = base64.b64encode(b"hello" * 200).decode("ascii")
        with self.assertRaises(app.AppError):
            app.decode_audio("test.txt", encoded)

    def test_normalize_status_aliases(self):
        self.assertEqual(app.normalize_status("complete"), "completed")
        self.assertEqual(app.normalize_status("success"), "completed")
        self.assertEqual(app.normalize_status("failure"), "failed")
        self.assertEqual(app.normalize_status("cancelled"), "canceled")
        self.assertEqual(app.normalize_status("ready"), "unknown")

    def test_selected_model_validation(self):
        self.assertEqual(app.selected_model(None), "suno-v6")
        self.assertEqual(app.selected_model("suno-v6-wild"), "suno-v6-wild")
        with self.assertRaises(app.AppError):
            app.selected_model("unknown-model")

    def test_text_limit_uses_utf16_length(self):
        self.assertEqual(app.utf16_length("茉莉花"), 3)
        app.require_text_limit("lyrics", "茉莉花", 3, "歌词")
        with self.assertRaises(app.AppError) as raised:
            app.require_text_limit("lyrics", "茉莉花", 2, "歌词")
        self.assertEqual(raised.exception.code, "TEXT_TOO_LONG")

    def test_normalize_submission_aliases(self):
        payload = {
            "data": {
                "request_id": "req-2",
                "status": "success",
                "tasks": [{"status": "complete", "song_id": "song-2"}],
            }
        }
        result = app.normalize_submission(payload)
        self.assertEqual(result["request_id"], "req-2")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tasks"][0]["status"], "completed")

    def test_normalize_submission_derives_tasks_from_songs(self):
        payload = {
            "data": {
                "request_id": "req-3",
                "songs": [{
                    "song_id": "song-3",
                    "title": "song",
                    "status": "complete",
                    "audio_url": "https://example.test/a.mp3",
                }],
            }
        }
        result = app.normalize_submission(payload)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["song_id"], "song-3")
        self.assertEqual(result["tasks"][0]["status"], "completed")


class PricingTests(unittest.TestCase):
    def test_estimate(self):
        api = object.__new__(app.SunoApi)
        api.prices = lambda: {
            "prices": {
                "generation.per_request": 0.6,
                "generation.max_mode_factor": 2,
                "voice.generate_with_clone": 1.2,
                "stems.two_track": 1.2,
                "stems.auto_twelve": 3,
            }
        }
        self.assertEqual(api.estimate("source", False)["amount"], 0.6)
        self.assertEqual(api.estimate("source", True)["amount"], 1.2)
        self.assertEqual(api.estimate("voice", False)["amount"], 1.2)
        self.assertEqual(api.estimate("stems_twelve", False)["amount"], 3.0)
        self.assertEqual(api.estimate("edit", False)["amount"], 0.6)

    def test_confirmed_cost(self):
        estimate = {"amount": 0.6}
        app.require_confirmed_cost(estimate, 0.6, True)
        with self.assertRaises(app.AppError):
            app.require_confirmed_cost(estimate, 0.6, False)
        with self.assertRaises(app.AppError):
            app.require_confirmed_cost(estimate, 0.8, True)


class CloudSongTests(unittest.TestCase):
    def test_list_songs_parses_items_and_clamps_page_size(self):
        api = object.__new__(app.SunoApi)
        seen = {}

        def request(path):
            seen["path"] = path
            return {
                "data": {
                    "items": [{
                        "song_id": "song-9",
                        "title": "cloud song",
                        "status": "complete",
                        "audio_url": "https://example.test/a.mp3",
                    }],
                    "total": 1,
                    "page": 2,
                    "page_size": 50,
                }
            }

        api._request = request
        result = api.list_songs(page=2, page_size=100, query="jasmine", status="completed")
        self.assertIn("p=2", seen["path"])
        self.assertIn("page_size=50", seen["path"])
        self.assertIn("q=jasmine", seen["path"])
        self.assertIn("status=completed", seen["path"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["songs"][0]["song_id"], "song-9")
        self.assertEqual(result["songs"][0]["status"], "completed")


class EditSongTests(unittest.TestCase):
    def make_api(self, response):
        api = object.__new__(app.SunoApi)
        seen = {}

        def request(path, method="GET", body=None, timeout=75):
            seen.update({"path": path, "method": method, "body": body, "timeout": timeout})
            return response

        api._request = request
        return api, seen

    def test_crop_payload(self):
        api, seen = self.make_api({
            "data": {"request_id": "req-edit", "status": "queued", "tasks": []}
        })
        result = api.edit_song("song-1", "title", "crop", {
            "start_seconds": 1,
            "end_seconds": 12.5,
        })
        self.assertEqual(seen["path"], "/api/music/crop")
        self.assertEqual(seen["body"]["crop_start_s"], 1.0)
        self.assertEqual(seen["body"]["crop_end_s"], 12.5)
        self.assertEqual(seen["body"]["source_clip_id"], "song-1")
        self.assertEqual(result["request_id"], "req-edit")

    def test_fade_and_speed_payloads(self):
        api, seen = self.make_api({"data": {"request_id": "req-edit"}})
        api.edit_song("song-1", "title", "fade", {"direction": "out", "duration_seconds": 3})
        self.assertEqual(seen["path"], "/api/music/fade")
        self.assertEqual(seen["body"]["direction"], "out")
        self.assertEqual(seen["body"]["duration_seconds"], 3.0)

        api.edit_song("song-1", "title", "speed", {
            "speed_multiplier": 1.05,
            "keep_pitch": True,
        })
        self.assertEqual(seen["path"], "/api/music/speed")
        self.assertEqual(seen["body"]["speed_multiplier"], 1.05)
        self.assertTrue(seen["body"]["keep_pitch"])

    def test_invalid_crop_rejected(self):
        api, seen = self.make_api({"data": {}})
        with self.assertRaises(app.AppError):
            api.edit_song("song-1", "title", "crop", {
                "start_seconds": 10,
                "end_seconds": 5,
            })
        self.assertEqual(seen, {})

    def test_remove_section_payload(self):
        api, seen = self.make_api({"data": {"request_id": "req-remove"}})
        api.edit_song("song-1", "title", "remove_section", {
            "start_seconds": 5,
            "end_seconds": 9.5,
        })
        self.assertEqual(seen["path"], "/api/music/remove-section")
        self.assertEqual(seen["body"]["source_clip_id"], "song-1")
        self.assertEqual(seen["body"]["crop_start_s"], 5.0)
        self.assertEqual(seen["body"]["crop_end_s"], 9.5)


class StoreTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = app.Store(Path(temp) / "state.json")
            store.add_job({"id": "job-1", "status": "queued"})
            self.assertEqual(store.read()["jobs"][0]["id"], "job-1")
            store.update_job({"id": "job-1", "status": "completed"})
            self.assertEqual(store.read()["jobs"][0]["status"], "completed")


class OutputFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output_dir = Path(self.temp.name) / "outputs"
        self.output_dir.mkdir()
        self.patcher = mock.patch.object(app, "OUTPUT_DIR", self.output_dir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_list_outputs_filters_media_and_uses_download_metadata(self):
        (self.output_dir / "jasmine.mp3").write_bytes(b"ID3" + b"\x00" * 1500)
        (self.output_dir / "jasmine.wav").write_bytes(b"RIFF" + b"\x00" * 1500)
        (self.output_dir / "notes.txt").write_text("ignore", encoding="utf-8")
        with mock.patch.object(app.STORE, "read", return_value={
            "downloads": [{"filename": "jasmine.mp3", "title": "茉莉花导出", "song_id": "song-1"}]
        }):
            files = app.list_output_files()
        by_name = {item["filename"]: item for item in files}
        self.assertEqual(set(by_name), {"jasmine.mp3", "jasmine.wav"})
        self.assertEqual(by_name["jasmine.mp3"]["title"], "茉莉花导出")
        self.assertEqual(by_name["jasmine.mp3"]["song_id"], "song-1")

    def test_delete_output_rejects_path_traversal(self):
        outside = Path(self.temp.name) / "outside.mp3"
        outside.write_bytes(b"ID3" + b"\x00" * 1500)
        with self.assertRaises(app.AppError) as raised:
            app.delete_output_file("../outside.mp3")
        self.assertEqual(raised.exception.code, "OUTPUT_NAME_INVALID")
        self.assertTrue(outside.exists())

    def test_delete_output_removes_only_media_file(self):
        target = self.output_dir / "song.mp3"
        target.write_bytes(b"ID3" + b"\x00" * 1500)
        with mock.patch.object(app.STORE, "remove_download") as remove_download:
            result = app.delete_output_file("song.mp3")
        self.assertTrue(result["deleted"])
        self.assertFalse(target.exists())
        remove_download.assert_called_once_with("song.mp3")


class DownloadFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output_dir = Path(self.temp.name) / "outputs"
        self.patches = [
            mock.patch.object(app, "OUTPUT_DIR", self.output_dir),
            mock.patch.object(app.STORE, "add_download"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_mp3_download_uses_mp3_url(self):
        api = mock.Mock()
        api.download_bytes.side_effect = app.AppError(404, "NO_FILE", "not ready")
        api.download_url.return_value = "https://example.test/song.mp3"
        with mock.patch.object(app, "download_remote", return_value=(b"ID3" + b"\x00" * 2048, "audio/mpeg")):
            result = app.save_song_file(api, "song-1", "mp3", "test")
        api.download_url.assert_called_once_with("song-1", "mp3")
        self.assertTrue(result["filename"].endswith(".mp3"))

    def test_mp3_download_falls_back_to_song_url(self):
        api = mock.Mock()
        api.download_bytes.side_effect = app.AppError(404, "NO_FILE", "not ready")

        def download_url(song_id, kind):
            if kind == "mp3":
                raise app.AppError(404, "NO_FILE", "mp3 unavailable")
            return "https://example.test/song"

        api.download_url.side_effect = download_url
        with mock.patch.object(app, "download_remote", return_value=(b"ID3" + b"\x00" * 2048, "audio/mpeg")):
            result = app.save_song_file(api, "song-1", "mp3", "test")
        self.assertEqual(api.download_url.call_args_list, [
            mock.call("song-1", "mp3"),
            mock.call("song-1", "song"),
        ])
        self.assertTrue(result["filename"].endswith(".mp3"))

    def test_mp3_download_reports_missing_file(self):
        api = mock.Mock()
        api.download_bytes.side_effect = app.AppError(404, "NO_FILE", "not ready")
        api.download_url.side_effect = app.AppError(404, "NO_FILE", "not found")
        with self.assertRaises(app.AppError) as raised:
            app.save_song_file(api, "song-1", "mp3", "test")
        self.assertEqual(raised.exception.code, "MEDIA_NOT_FOUND")
        self.assertEqual(api.download_url.call_args_list, [
            mock.call("song-1", "mp3"),
            mock.call("song-1", "song"),
        ])

    def test_wav_prepare_failure_falls_back_to_bytes(self):
        api = mock.Mock()
        api.prepare_wav.side_effect = app.AppError(502, "WAV_FAILED", "prepare failed")
        api.download_bytes.return_value = {
            "bytes": b"RIFF" + b"\x00" * 2048,
            "content_type": "audio/wav",
        }
        result = app.save_song_file(api, "song-1", "wav", "test")
        api.download_bytes.assert_called_once_with("song-1", "wav")
        self.assertTrue(result["filename"].endswith(".wav"))


class SifengLyricsTests(unittest.TestCase):
    def test_refine_adds_scat_and_adlibs(self):
        result = app.refine_sifeng_lyrics("第一句\n第二句\n第三句\n第四句\n第五句\n第六句\n第七句")
        lyrics = result["lyrics"]
        self.assertIn("[Scat Break]", lyrics)
        self.assertIn("beng-ba-beng-ba", lyrics)
        self.assertIn("邦吧邦吧邦吧", lyrics)
        self.assertIn("[scream] WOO!", lyrics)
        self.assertIn("[scream] WOO! WOO! WOO!", lyrics)
        self.assertIn("[Outro]", lyrics)

    def test_refine_keeps_existing_adlibs(self):
        source = "[Verse 1]\n第一句 (Woo!)\n第二句\n第三句\n\n[Outro]\n(收!)"
        result = app.refine_sifeng_lyrics(source)
        self.assertIn("第一句 [scream] (Woo!)\n", result["lyrics"])
        self.assertNotIn("第一句 (Woo!) (", result["lyrics"])
        self.assertIn("第三句\n[scream] YEAH!", result["lyrics"])

    def test_refine_does_not_double_bare_woo(self):
        result = app.refine_sifeng_lyrics("第一句\nWOO! 明天再说吧\n第三句\n第四句")
        self.assertIn("[scream] WOO! 明天再说吧\n", result["lyrics"])
        self.assertNotIn("WOO! 明天再说吧 (", result["lyrics"])

    def test_refine_adds_scream_before_interjections(self):
        result = app.refine_sifeng_lyrics("a\nb\nc\nd\ne\nf\ng")
        lyrics = result["lyrics"]
        self.assertIn("a\n[scream] WOO!", lyrics)
        self.assertIn("[Scat Break]\n[scream] WOO! WOO! WOO!", lyrics)
        self.assertIn("[scream] (WOO!!!)", lyrics)
        self.assertNotIn("[scream] WOO! [scream] WOO!", lyrics)
        self.assertNotIn("[scream] [scream]", lyrics)
        self.assertNotIn("[scream] (呼——哈——)", lyrics)


    def test_refine_does_not_invent_bridge(self):
        result = app.refine_sifeng_lyrics("第一句\n第二句\n第三句\n第四句")
        self.assertNotIn("你说人生", result["lyrics"])
        self.assertNotIn("[Bridge]", result["lyrics"])
        self.assertIn("第一句\n[scream] WOO!", result["lyrics"])

    def test_refine_scream_tags_are_idempotent(self):
        once = app.refine_sifeng_lyrics("a\nb\nc\nd\ne\nf\ng")["lyrics"]
        twice = app.refine_sifeng_lyrics(once)["lyrics"]
        self.assertNotIn("[scream] [scream]", twice)

    def test_draft_does_not_repeat_full_idea(self):
        idea = "凌晨三点我决定放过自己，但又把烦恼捡了回来"
        lyrics = app.draft_sifeng_lyrics(idea)
        self.assertNotIn("我决定凌晨三点", lyrics)
        self.assertIn("放过自己", lyrics)
        self.assertIn("[Scat Break]", lyrics)

    def test_config_summary_masks_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            key = "sk-test-1234567890abcdef"
            path.write_text(json.dumps({
                "apiKey": key,
                "baseUrl": "https://example.test",
            }), encoding="utf-8")
            with mock.patch.object(app, "CONFIG_PATH", path):
                summary = app.config_summary()
        self.assertTrue(summary["configured"])
        self.assertEqual(summary["base_url"], "https://example.test")
        self.assertEqual(summary["key_masked"], "sk-t…cdef")
        self.assertNotIn(key, json.dumps(summary, ensure_ascii=False))


class LyricsGenerationTests(unittest.TestCase):
    def test_generate_lyrics_payload(self):
        api = object.__new__(app.SunoApi)
        seen = {}

        def request(path, method="GET", body=None, timeout=75):
            seen.update({"path": path, "method": method, "body": body, "timeout": timeout})
            return {"data": {"lyrics": "第一句\n第二句"}}

        api._request = request
        result = api.generate_lyrics("写一首通透的歌", "Chinese EDM", "通透", "suno-v6-wild")
        self.assertEqual(seen["path"], "/api/lyrics/generate")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"]["instruction"], "写一首通透的歌")
        self.assertEqual(seen["body"]["style"], "Chinese EDM")
        self.assertEqual(seen["body"]["title"], "通透")
        self.assertEqual(seen["body"]["model"], "suno-v6-wild")
        self.assertNotIn("prompt", seen["body"])
        self.assertEqual(result["lyrics"], "第一句\n第二句")


class LyricsStyleCompactionTests(unittest.TestCase):
    def test_default_style_is_compacted_for_lyrics(self):
        compact = app.compact_lyrics_style(app.SIFENG_STYLE)
        self.assertEqual(compact, app.SIFENG_LYRICS_STYLE)
        self.assertLessEqual(app.utf16_length(compact), app.MAX_LYRICS_STYLE_CHARS)

    def test_short_custom_style_is_preserved(self):
        self.assertEqual(app.compact_lyrics_style("Chinese EDM"), "Chinese EDM")

    def test_chinese_comma_style_is_compacted(self):
        style = "Chinese EDM, " + "heavy bass， " * 80 + "scat"
        compact = app.compact_lyrics_style(style)
        self.assertLessEqual(app.utf16_length(compact), app.MAX_LYRICS_STYLE_CHARS)

    def test_generate_lyrics_payload_uses_compact_style(self):
        api = object.__new__(app.SunoApi)
        seen = {}

        def request(path, method="GET", body=None, timeout=75):
            seen.update({"path": path, "body": body})
            return {"data": {"edited_lyrics": "第一句\n第二句"}}

        api._request = request
        api.generate_lyrics("写一首通透的歌", app.SIFENG_STYLE, "通透", "suno-v6-wild")
        self.assertLessEqual(app.utf16_length(seen["body"]["style"]), app.MAX_LYRICS_STYLE_CHARS)

    def test_generate_lyrics_retries_with_fallback_style(self):
        api = object.__new__(app.SunoApi)
        bodies = []

        def request(path, method="GET", body=None, timeout=75):
            bodies.append(body)
            if len(bodies) == 1:
                raise app.AppError(
                    400,
                    "SUNO_HTTP",
                    "That's a bit too long. Please shorten it and try again.",
                )
            return {"data": {"edited_lyrics": "line one\nline two"}}

        api._request = request
        result = api.generate_lyrics("short idea", app.SIFENG_STYLE, "title", "suno-v6-wild")
        self.assertEqual(result["lyrics"], "line one\nline two")
        self.assertEqual(bodies[0]["style"], app.SIFENG_LYRICS_STYLE)
        self.assertEqual(bodies[1]["style"], app.SIFENG_LYRICS_STYLE_FALLBACK)

    def test_content_too_long_error_messages_are_detected(self):
        self.assertTrue(app.is_content_too_long_error("That's a bit too long."))
        self.assertTrue(app.is_content_too_long_error("上游认为提交内容过长。"))
        self.assertFalse(app.is_content_too_long_error("rate limited"))


class UpstreamErrorTests(unittest.TestCase):
    def test_nested_error_message(self):
        api = object.__new__(app.SunoApi)
        raw = json.dumps({
            "error": {
                "message": "That's a bit too long. Please shorten it and try again.",
                "type": "bad_response_status_code",
            }
        }).encode("utf-8")
        self.assertEqual(
            api._error_message(raw),
            "That's a bit too long. Please shorten it and try again.",
        )

    def test_too_long_message_is_actionable(self):
        message = app.friendly_suno_error(
            "That's a bit too long. Please shorten it and try again."
        )
        self.assertIn("内容过长", message)
        self.assertIn("缩短", message)


class ConfigValidationTests(unittest.TestCase):
    def test_api_key_rejects_masked_and_chinese_values(self):
        for value in ("sk-s…XhqA", "sk-中文密钥abcdef", "sk-abc def"):
            with self.assertRaises(app.AppError) as raised:
                app.normalize_api_key(value)
            self.assertEqual(raised.exception.code, "API_KEY_INVALID")

    def test_api_key_accepts_normal_key(self):
        key = "sk-test-1234567890abcdef"
        self.assertEqual(app.normalize_api_key(key), key)

    def test_base_url_rejects_chinese(self):
        with self.assertRaises(app.AppError) as raised:
            app.normalize_base_url("https://例子.测试")
        self.assertEqual(raised.exception.code, "BASE_URL_INVALID")

    def test_load_config_rejects_non_ascii_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({
                "apiKey": "sk-中文密钥abcdef",
                "baseUrl": "https://www.suno-api.io",
            }), encoding="utf-8")
            with mock.patch.object(app, "CONFIG_PATH", path):
                with self.assertRaises(app.AppError) as raised:
                    app.load_config()
        self.assertEqual(raised.exception.code, "API_KEY_INVALID")


if __name__ == "__main__":
    unittest.main()
