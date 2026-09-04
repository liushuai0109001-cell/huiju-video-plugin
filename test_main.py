import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


plugin_utils = types.ModuleType("plugin_utils")
plugin_utils.load_plugin_config = lambda _path: {}
plugin_utils.update_plugin_params = lambda _path, _params: None
sys.modules.setdefault("plugin_utils", plugin_utils)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main


class MergePluginParamsTest(unittest.TestCase):
    def test_normalizes_current_zizi_chinese_reference_keys(self):
        raw = {
            "参考图片MAP": {"2": "two.png", "1": "one.png"},
            "首帧": "first.png",
            "尾帧": "last.png",
        }

        normalized = main._normalize_reference_images(raw, {})

        self.assertEqual(normalized["参考图片MAP"], {2: "two.png", 1: "one.png"})
        self.assertEqual(normalized["首帧"], "first.png")
        self.assertEqual(normalized["尾帧"], "last.png")

    def test_normalizes_list_and_english_reference_aliases(self):
        normalized = main._normalize_reference_images(
            ["one.png", "two.png"],
            {"first_frame_path": "first.png", "end_frame_path": "last.png"},
        )

        self.assertEqual(normalized["参考图片MAP"], {1: "one.png", 2: "two.png"})
        self.assertEqual(normalized["首帧"], "first.png")
        self.assertEqual(normalized["尾帧"], "last.png")

    def test_collects_public_urls_in_stable_numeric_order(self):
        normalized = main._normalize_reference_images(
            {"参考图片MAP": {"10": "https://example.com/ten.png", "2": "https://example.com/two.png"}},
            {},
        )

        self.assertEqual(
            main._collect_reference_images(normalized, "multi_image"),
            ["https://example.com/two.png", "https://example.com/ten.png"],
        )

    def test_first_frame_mode_falls_back_to_first_mapped_image(self):
        normalized = main._normalize_reference_images(
            {"参考图片MAP": {2: "https://example.com/two.png", 1: "https://example.com/one.png"}},
            {},
        )

        self.assertEqual(
            main._collect_reference_images(normalized, "first_frame"),
            ["https://example.com/one.png"],
        )

    def test_detects_prompts_that_require_reference_media(self):
        self.assertTrue(main._prompt_requires_reference("请保持@图片1中的人物一致"))
        self.assertTrue(main._prompt_requires_reference("Animate <IMAGE_2>"))
        self.assertFalse(main._prompt_requires_reference("一只猫在阳光下散步"))

    def test_reads_standard_and_legacy_failure_reasons(self):
        self.assertEqual(
            main._task_failure_reason({"error": {"message": "upstream overloaded"}}),
            "upstream overloaded",
        )
        self.assertEqual(
            main._task_failure_reason({"status": "failed", "url": "旧版失败原因"}),
            "旧版失败原因",
        )

    def test_chre_payload_also_includes_stable_reference_aliases(self):
        payload = {"model": "sd2.5"}
        urls = ["https://example.com/one.png"]

        main._attach_reference_image_fields(payload, urls, include_image_refs=True)

        self.assertEqual(payload["image_urls"], urls)
        self.assertEqual(payload["images"], urls)
        self.assertEqual(payload["reference_images"], urls)
        self.assertEqual(payload["image_refs"], urls)

    def test_generate_sd25_keeps_reference_images_in_final_json(self):
        captured = {}

        class Response:
            def __init__(self, status_code, data=None, content=b""):
                self.status_code = status_code
                self._data = data or {}
                self._content = content
                self.text = ""

            def json(self):
                return self._data

            def iter_content(self, chunk_size=8192):
                del chunk_size
                yield self._content

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return Response(200, {"id": "task_test"})

        def fake_get(url, **_kwargs):
            if url.endswith("/v1/videos/task_test"):
                return Response(200, {
                    "id": "task_test",
                    "status": "completed",
                    "url": "https://media.example.com/result.mp4",
                })
            return Response(200, content=b"fake mp4")

        with tempfile.TemporaryDirectory() as temp_dir:
            references = {}
            for index in range(1, 11):
                image = Path(temp_dir) / f"reference-{index}.png"
                image.write_bytes(f"image-{index}".encode())
                references[index] = str(image)
            context = {
                "prompt": "保持@图片1到@图片10中的人物和场景一致",
                "project_path": temp_dir,
                "reference_images": {"参考图片MAP": references},
                "plugin_params": {
                    "api_key": "test-key",
                    "base_url": "https://huiju.example",
                    "model": "sd2.5",
                    "duration": 15,
                    "reference_mode": "multi_image",
                    "poll_interval": 0,
                },
            }

            with patch.object(main, "_upload_image_to_host", side_effect=lambda path, *_args: f"https://media.example.com/{Path(path).name}"), \
                    patch.object(main.requests, "post", side_effect=fake_post), \
                    patch.object(main.requests, "get", side_effect=fake_get), \
                    patch.object(main.time, "sleep", return_value=None):
                outputs = main.generate(context)

            self.assertTrue(Path(outputs[0]).is_file())

        expected = [f"https://media.example.com/reference-{index}.png" for index in range(1, 11)]
        self.assertEqual(captured["url"], "https://huiju.example/v1/videos")
        self.assertEqual(captured["json"]["image_urls"], expected)
        self.assertEqual(captured["json"]["images"], expected)
        self.assertEqual(captured["json"]["reference_images"], expected)
        self.assertEqual(captured["json"]["image_refs"], expected)
        self.assertEqual(captured["json"]["duration"], 30)
        self.assertEqual(captured["json"]["prompt"], "保持@图片1到@图片10中的人物和场景一致")

    def test_generate_blocks_missing_references_before_submit(self):
        context = {
            "prompt": "让@图片1中的人物转头",
            "project_path": ".",
            "reference_images": {},
            "plugin_params": {
                "api_key": "test-key",
                "base_url": "https://huiju.example",
                "model": "sd2.5",
                "duration": 15,
            },
        }

        with patch.object(main.requests, "post") as post:
            with self.assertRaisesRegex(Exception, "插件没有收到可用图片"):
                main.generate(context)
        post.assert_not_called()

    def test_collects_audio_from_top_level_and_character_references(self):
        context = {
            "reference_audios": [{"path": "voice-one.mp3"}],
            "characters": [{
                "reference_items": [{"path": "voice-two.wav", "media_type": "audio"}],
            }],
            "reference_items": [{"path": "ignore.png", "media_type": "image"}],
        }

        self.assertEqual(
            main._collect_reference_media(context, "audio"),
            ["voice-one.mp3", "voice-two.wav"],
        )

    def test_collects_video_aliases_and_limits_to_three(self):
        context = {
            "reference_videos": ["one.mp4", {"url": "https://example.com/two.mp4"}],
            "video_refs": ["three.mov", "four.webm"],
        }

        self.assertEqual(
            main._collect_reference_media(context, "video"),
            ["one.mp4", "https://example.com/two.mp4", "three.mov"],
        )

    def test_public_media_url_does_not_upload(self):
        url = "https://example.com/reference.mp3"
        self.assertEqual(main._upload_image_to_host(url, "https://host/upload"), url)

    def test_seedance_mini_requires_wav_reference_audio(self):
        self.assertTrue(main._requires_wav_reference_audio("seedance-2.0-mini"))
        self.assertFalse(main._requires_wav_reference_audio("wan3.0th"))

    def test_existing_wav_does_not_need_conversion(self):
        self.assertEqual(main._convert_reference_audio_to_wav("voice.wav"), "voice.wav")

    def test_non_wav_audio_is_converted_with_bundled_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "voice.mp3"
            source.write_bytes(b"fake mp3")

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"RIFF converted wav")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch.object(main, "_find_ffmpeg_binary", return_value="ffmpeg"), \
                    patch.object(main.subprocess, "run", side_effect=fake_run):
                output = main._convert_reference_audio_to_wav(str(source))

            try:
                self.assertEqual(Path(output).suffix, ".wav")
                self.assertGreater(Path(output).stat().st_size, 0)
            finally:
                Path(output).unlink(missing_ok=True)

    def test_wan3_th_preserves_duration_up_to_30_seconds(self):
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 29), 29)
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 30), 30)
        self.assertEqual(main._normalize_xingyao_duration("wan3.0th", 31), 30)

    def test_wan_dash_3_preserves_duration_up_to_30_seconds(self):
        self.assertEqual(main._normalize_xingyao_duration("wan-3.0", 30), 30)

    def test_sd25_is_recognized_as_fixed_30_second_model(self):
        self.assertTrue(main._is_fixed_sd25_model("sd2.5"))
        self.assertFalse(main._is_fixed_sd25_model("seedance2.5"))

    def test_wan_dash_3_normalizes_unsupported_ratios(self):
        self.assertEqual(main._normalize_meaicc_aspect_ratio("21:9"), "16:9")
        self.assertEqual(main._normalize_meaicc_aspect_ratio("3:2"), "4:3")
        self.assertEqual(main._normalize_meaicc_aspect_ratio("2:3"), "3:4")
        self.assertEqual(main._normalize_meaicc_aspect_ratio("9:16"), "9:16")

    def test_1080p_video_sizes(self):
        self.assertEqual(main._ratio_to_video_size("16:9", "1080p"), "1920x1080")
        self.assertEqual(main._ratio_to_video_size("9:16", "1080p"), "1080x1920")
        self.assertEqual(main._ratio_to_video_size("4:3", "1080p"), "1440x1080")

    def test_current_request_duration_overrides_saved_duration(self):
        with patch.object(main, "load_plugin_config", return_value={"duration": 6}):
            merged, disk, host = main._merge_plugin_params({"duration": "15"})

        self.assertEqual(merged["duration"], "15")
        self.assertEqual(disk["duration"], 6)
        self.assertEqual(host["duration"], "15")

    def test_saved_duration_is_used_when_request_omits_it(self):
        with patch.object(main, "load_plugin_config", return_value={"duration": 10}):
            merged, _, _ = main._merge_plugin_params({"model": "seedance-2.0-mini"})

        self.assertEqual(merged["duration"], 10)

    def test_empty_request_secret_does_not_erase_saved_secret(self):
        with patch.object(main, "load_plugin_config", return_value={"api_key": "saved-key"}):
            merged, _, _ = main._merge_plugin_params({"api_key": ""})

        self.assertEqual(merged["api_key"], "saved-key")


if __name__ == "__main__":
    unittest.main()
