import os
import time
import yaml
import requests

from ansible.plugins.callback import CallbackBase


def load_config():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, "obm_monitoring.yaml")

        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        print(f"Error loading config file: {e}")
        return {}


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'obm_monitoring'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()

        config = load_config()
        self.debug = config.get("debug", False)
        self.api_url = config.get("api_url", "http://localhost:8000")
        self._playbook_name = ""
        self._current_play = "default"
        self._playbook_start_time = None

        self._totals = {
            "ok": 0,
            "changed": 0,
            "failed": 0,
            "skipped": 0,
            "unreachable": 0
        }

        self._play_totals = {}

    # -----------------------
    # HELPERS
    # -----------------------
    def _log(self, msg):
        if self.debug:
            self._display.display(f"[callback] {msg}")

    def _emit(self, message=None, api_path=None, payload=None):
        if self.debug and message:
            self._display.display(f"[callback] {message}")

        if api_path and payload is not None:
            try:
                requests.post(
                    f"{self.api_url}{api_path}",
                    json=payload,
                    timeout=2
                )
            except Exception as e:
                if self.debug:
                    self._display.display(f"[callback] API error: {e}")

    def _task_name(self, result):
        name = result._task.get_name() if result._task else "unknown_task"
        return str(name).split(" => ")[0].split("|")[0].strip() or "unnamed_task"

    def _ensure_play(self, play):
        if play not in self._play_totals:
            self._play_totals[play] = {
                "ok": 0,
                "changed": 0,
                "failed": 0,
                "skipped": 0,
                "unreachable": 0
            }

    def _inc_play(self, play, key):
        self._ensure_play(play)
        self._play_totals[play][key] += 1

    def v2_playbook_on_start(self, playbook):
        self._playbook_name = os.path.basename(playbook._file_name)
        self._playbook_start_time = time.time()

        self._emit(
            message=f"PLAYBOOK START: {self._playbook_name}",
            api_path="/runs/start",
            payload={"playbook": self._playbook_name}
        )

    def v2_playbook_on_play_start(self, play):
        self._current_play = play.name or "unnamed_play"

        self._emit(
            message=f"PLAY START: {self._current_play}",
            api_path="/runs/play/start",
            payload={"play": self._current_play}
        )

    def v2_playbook_on_stats(self, stats):
        duration = (
            time.time() - self._playbook_start_time
            if self._playbook_start_time else 0
        )

        total = sum(self._totals.values())
        success = self._totals["ok"] + self._totals["changed"]

        plays_report = {}

        for play, t in self._play_totals.items():
            t_total = sum(t.values())
            t_success = t["ok"] + t["changed"]

            plays_report[play] = {
                **t,
                "tasks_total": t_total,
                "success_rate": round(t_success / t_total, 4) if t_total else 1.0
            }

        local_report = {
            "playbook": self._playbook_name,
            "duration": round(duration, 2),
            "summary": {
                **self._totals,
                "tasks_total": total,
                "success_rate": round(success / total, 4) if total else 1.0
            },
            "plays": plays_report
        }

        if self.debug:
            self._display.display("[callback] FINAL LOCAL REPORT:")
            self._display.display(str(local_report))

        self._emit(
            message=f"PLAYBOOK END ({round(duration,2)}s)",
            api_path="/runs/end",
            payload=local_report
        )

    def v2_runner_on_ok(self, result):
        host = result._host.get_name()
        task = self._task_name(result)
        play = self._current_play

        changed = result._result.get("changed", False)
        status = "changed" if changed else "ok"

        self._totals[status] += 1
        self._inc_play(play, status)

        self._emit(
            message=f"OK: {host} -> {task} ({status})",
            api_path="/runs/task",
            payload={
                "host": host,
                "play": play,
                "task": task,
                "status": status,
                "changed": changed
            }
        )

    def v2_runner_on_failed(self, result, ignore_errors=False):
        host = result._host.get_name()
        task = self._task_name(result)
        play = self._current_play

        self._totals["failed"] += 1
        self._inc_play(play, "failed")

        self._emit(
            message=f"FAILED: {host} -> {task}",
            api_path="/runs/task",
            payload={
                "host": host,
                "play": play,
                "task": task,
                "status": "failed",
                "msg": str(result._result.get("msg", "")),
                "ignore_errors": ignore_errors
            }
        )

    def v2_runner_on_skipped(self, result):
        host = result._host.get_name()
        task = self._task_name(result)
        play = self._current_play

        self._totals["skipped"] += 1
        self._inc_play(play, "skipped")

        self._emit(
            message=f"SKIPPED: {host} -> {task}",
            api_path="/runs/task",
            payload={
                "host": host,
                "play": play,
                "task": task,
                "status": "skipped"
            }
        )

    def v2_runner_on_unreachable(self, result):
        host = result._host.get_name()
        task = self._task_name(result)
        play = self._current_play

        self._totals["unreachable"] += 1
        self._inc_play(play, "unreachable")

        self._emit(
            message=f"UNREACHABLE: {host} -> {task}",
            api_path="/runs/task",
            payload={
                "host": host,
                "play": play,
                "task": task,
                "status": "unreachable",
                "msg": str(result._result.get("msg", ""))
            }
        )