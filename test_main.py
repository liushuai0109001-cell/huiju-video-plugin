import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import main


class MergePluginParamsTest(unittest.TestCase):
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
