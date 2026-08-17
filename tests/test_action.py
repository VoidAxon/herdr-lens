import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lens import action


class WriteJob(unittest.TestCase):
    def test_payload_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = action.write_job({"action": "translate", "text": "hi"}, Path(tmp))
            self.assertEqual(json.loads(path.read_text())["text"], "hi")

    def test_job_file_is_private_to_the_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = action.write_job({"text": "secret selection"}, Path(tmp))
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, f"expected 0600, got {oct(mode)}")

    def test_each_job_gets_its_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = action.write_job({"text": "one"}, Path(tmp))
            b = action.write_job({"text": "two"}, Path(tmp))
            self.assertNotEqual(a, b)

    def test_stale_jobs_are_swept(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "old.json"
            stale.write_text("{}")
            old = time.time() - action.JOB_TTL_SECONDS - 60
            os.utime(stale, (old, old))

            fresh = action.write_job({"text": "new"}, Path(tmp))

            self.assertFalse(stale.exists(), "a job no viewer consumed should be cleaned up")
            self.assertTrue(fresh.exists())

    def test_recent_jobs_survive_the_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            recent = action.write_job({"text": "first"}, Path(tmp))
            action.write_job({"text": "second"}, Path(tmp))
            self.assertTrue(recent.exists())


def fake_api(*replies, size=None):
    """Stand in for the socket, and for the config read behind the geometry.

    Both are patched together because a test that reaches either one is no
    longer a unit test — it talks to the running Herdr and to whatever is in
    the developer's own config file.
    """
    api = mock.Mock(side_effect=list(replies) or [{"result": {"type": "ok"}}])
    return mock.patch.multiple(
        "lens.action",
        api=api,
        popup_size=mock.Mock(return_value=size or {}),
    ), api


class OpenPopup(unittest.TestCase):
    def test_opens_the_viewer_with_the_job_path(self):
        patch, api = fake_api()
        with patch:
            self.assertEqual(action.open_popup(Path("/tmp/job.json")), 0)
        method, params = api.call_args[0]
        self.assertEqual(method, "plugin.pane.open")
        self.assertEqual(params["plugin_id"], "herdr-lens")
        self.assertEqual(params["entrypoint"], "viewer")
        self.assertEqual(params["placement"], "popup")
        self.assertEqual(params["env"], {"LENS_JOB": "/tmp/job.json"})

    def test_configured_geometry_is_passed_through(self):
        patch, api = fake_api(size={"width": "30%", "height": "20%"})
        with patch:
            action.open_popup(Path("/tmp/job.json"))
        params = api.call_args[0][1]
        self.assertEqual(params["width"], "30%")
        self.assertEqual(params["height"], "20%")

    def test_no_geometry_configured_sends_none(self):
        """Absent keys let the manifest decide; sending null would override it."""
        patch, api = fake_api()
        with patch:
            action.open_popup(Path("/tmp/job.json"))
        params = api.call_args[0][1]
        self.assertNotIn("width", params)
        self.assertNotIn("height", params)

    def test_an_unreachable_socket_falls_back_to_the_cli(self):
        """A popup at the manifest's size beats no popup at all."""
        patch, _ = fake_api(None)
        with patch:
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stderr=b"")
                self.assertEqual(action.open_popup(Path("/tmp/job.json")), 0)
        argv = run.call_args[0][0]
        self.assertIn("--plugin", argv)
        self.assertIn("LENS_JOB=/tmp/job.json", argv)

    def test_the_cli_fallback_uses_the_binary_herdr_told_us_about(self):
        with mock.patch.dict(os.environ, {"HERDR_BIN_PATH": "/opt/herdr"}):
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stderr=b"")
                action.open_popup_via_cli(Path("/tmp/job.json"))
        self.assertEqual(run.call_args[0][0][0], "/opt/herdr")

    def test_a_missing_binary_is_reported_not_raised(self):
        with mock.patch("subprocess.run", side_effect=OSError("no such file")):
            with mock.patch("sys.stderr", new=io.StringIO()) as err:
                self.assertEqual(action.open_popup_via_cli(Path("/tmp/job.json")), 1)
        self.assertIn("could not open popup", err.getvalue())

    def test_a_broken_config_does_not_stop_the_popup(self):
        """The popup is where a config error gets reported, so it must open."""
        with mock.patch("lens.action.config.load", side_effect=ValueError("bad toml")):
            self.assertEqual(action.popup_size(), {})


class Main(unittest.TestCase):
    def test_action_never_calls_a_provider(self):
        """The AI request belongs to the viewer; the action must exit fast."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}):
                with mock.patch("lens.providers.build") as build:
                    patch, _ = fake_api()
                    with patch:
                        with mock.patch(
                            "lens.selection.acquire",
                            return_value=mock.Mock(text="hello", source="clipboard", backend="x"),
                        ):
                            action.main(["translate"])
        build.assert_not_called()

    def test_empty_selection_still_opens_the_popup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}):
                patch, api = fake_api()
                with patch:
                    with mock.patch(
                        "lens.selection.acquire",
                        return_value=mock.Mock(text="", source="none", backend=""),
                    ):
                        action.main(["translate"])
        api.assert_called_once()


class Sweeping(unittest.TestCase):
    """A selection must not outlive the popup it was written for."""

    def stale(self, directory, age):
        path = Path(directory) / "orphan.json"
        path.write_text('{"text": "a secret selection"}')
        when = time.time() - age
        os.utime(path, (when, when))
        return path

    def test_the_viewer_sweeps_on_startup(self):
        # Sweeping only when a new job is written would leave this file on
        # disk forever if the user never translated again.
        with tempfile.TemporaryDirectory() as tmp:
            orphan = self.stale(tmp, action.JOB_TTL_SECONDS + 10)
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}):
                with mock.patch("lens.action.job_dir", return_value=Path(tmp)):
                    from lens import viewer

                    with mock.patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("LENS_JOB", None)
                        viewer.load_job()
            self.assertFalse(orphan.exists())

    def test_a_job_still_in_flight_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = self.stale(tmp, 1)
            action.sweep(Path(tmp))
            self.assertTrue(fresh.exists())

    def test_the_window_is_short(self):
        # 180 ms is the measured consumption time; a minute is already
        # generous for a popup that is going to open at all.
        self.assertLessEqual(action.JOB_TTL_SECONDS, 60)


class ForcedMode(unittest.TestCase):
    def payload(self, action_name):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": tmp}):
                with mock.patch("subprocess.run") as run:
                    run.return_value = mock.Mock(returncode=0, stderr=b"")
                    with mock.patch(
                        "lens.selection.acquire",
                        return_value=mock.Mock(text="hello", source="clipboard", backend="x"),
                    ):
                        with mock.patch("lens.action.write_job", side_effect=action.write_job) as w:
                            action.main([action_name])
            return w.call_args[0][0]

    def test_translate_leaves_the_mode_to_the_viewer(self):
        self.assertIsNone(self.payload("translate")["mode"])

    def test_explain_and_summarize_carry_their_mode(self):
        self.assertEqual(self.payload("explain")["mode"], "explain")
        self.assertEqual(self.payload("summarize")["mode"], "summarize")


class PopupRefused(unittest.TestCase):
    """Herdr permits one popup at a time and does not expose the open one, so
    a refused request can only be reported — never silently dropped."""

    def refuse(self, message):
        patch, _ = fake_api({"error": {"message": message}})
        with patch:
            with mock.patch("subprocess.run") as run:
                with mock.patch("sys.stderr", new=io.StringIO()):
                    action.open_popup(Path("/tmp/job.json"))
        return run.call_args_list

    def test_an_older_popup_is_replaced_rather_than_refused(self):
        """A popup still on screen holds a stale answer; the keypress must
        answer the selection just made."""
        patch, api = fake_api(
            {"error": {"message": "popup already open"}},
            {"result": {"type": "ok"}},
        )
        with patch:
            with mock.patch("lens.action.close_popup", return_value=True) as closed:
                with mock.patch("sys.stderr", new=io.StringIO()):
                    code = action.open_popup(Path("/tmp/job.json"))
        closed.assert_called_once()
        self.assertEqual(code, 0)
        self.assertEqual(api.call_count, 2, "it should retry after closing")

    def test_it_does_not_retry_forever(self):
        refused = {"error": {"message": "popup already open"}}
        patch, api = fake_api(refused, refused)
        with patch:
            with mock.patch("lens.action.close_popup", return_value=True):
                with mock.patch("subprocess.run"):
                    with mock.patch("sys.stderr", new=io.StringIO()):
                        action.open_popup(Path("/tmp/job.json"))
        # One open, one retry, then a notification — never a loop.
        self.assertEqual(api.call_count, 2)

    def test_a_failed_close_falls_back_to_notifying(self):
        with mock.patch("lens.action.close_popup", return_value=False):
            calls = self.refuse("popup already open")
        self.assertTrue([c for c in calls if "notification" in c[0][0]])

    def test_other_failures_are_reported_too(self):
        calls = self.refuse("terminal area too small for popup")
        notified = [c for c in calls if "notification" in c[0][0]]
        self.assertTrue(notified)

    def test_success_raises_no_notification(self):
        patch, _ = fake_api()
        with patch:
            with mock.patch("subprocess.run") as run:
                action.open_popup(Path("/tmp/job.json"))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
