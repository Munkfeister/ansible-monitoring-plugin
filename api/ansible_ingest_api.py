from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import time
import uuid
import json

app = FastAPI()

# ----------------------------
# CONFIG
# ----------------------------
DEBUG = True

# ----------------------------
# STORAGE
# ----------------------------
RUNS: Dict[str, Any] = {}
CURRENT_RUN: Optional[str] = None


# ----------------------------
# TIME HELPERS
# ----------------------------
def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().isoformat()


def now_ts():
    return time.time()


# ----------------------------
# DEBUG LOGGING
# ----------------------------
def log(msg: str):
    if DEBUG:
        print(f"[api] {now_iso()} {msg}")


def log_json(label: str, obj: Any):
    if DEBUG:
        print(f"[api] {now_iso()} {label}:")
        print(json.dumps(obj, indent=2))


# ----------------------------
# MIDDLEWARE (request tracing)
# ----------------------------
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    received_at = now_iso()

    body = await request.body()
    response = await call_next(request)

    duration = time.time() - start

    log(f"{request.method} {request.url.path} ({duration:.3f}s)")
    log(f"received_at={received_at}")

    if body:
        try:
            log_json("payload", json.loads(body))
        except Exception:
            log(f"payload (raw): {body.decode(errors='ignore')}")

    return response


# ----------------------------
# MODELS
# ----------------------------
class StartRun(BaseModel):
    playbook: str


class PlayStart(BaseModel):
    play: str


class TaskEvent(BaseModel):
    host: str
    play: str
    task: str
    status: str
    changed: bool = False
    msg: Optional[str] = None


# ----------------------------
# HELPERS
# ----------------------------
def get_run():
    if CURRENT_RUN is None:
        raise Exception("No active run")
    return RUNS[CURRENT_RUN]


def ensure_play(run, play):
    run["plays"].setdefault(play, {
        "hosts": {},
        "summary": {
            "ok": 0,
            "changed": 0,
            "failed": 0,
            "skipped": 0,
            "unreachable": 0
        }
    })


def ensure_host(play_bucket, host):
    play_bucket["hosts"].setdefault(host, {
        "tasks": {}
    })


def ensure_task(host_bucket, task):
    host_bucket["tasks"].setdefault(task, {
        "ok": 0,
        "changed": 0,
        "failed": 0,
        "skipped": 0,
        "unreachable": 0
    })


# ----------------------------
# API ENDPOINTS
# ----------------------------

@app.post("/runs/start")
def start_run(data: StartRun):
    global CURRENT_RUN

    run_id = str(uuid.uuid4())
    CURRENT_RUN = run_id

    RUNS[run_id] = {
        "id": run_id,
        "playbook": data.playbook,
        "start_time": now_ts(),
        "start_time_iso": now_iso(),
        "end_time": None,
        "end_time_iso": None,
        "plays": {},
        "summary": {
            "ok": 0,
            "changed": 0,
            "failed": 0,
            "skipped": 0,
            "unreachable": 0
        }
    }

    log(f"RUN STARTED {run_id} ({data.playbook})")

    return {"run_id": run_id}


@app.post("/runs/play/start")
def play_start(data: PlayStart):
    run = get_run()
    run["current_play"] = data.play

    log(f"PLAY START {data.play}")

    return {"status": "ok"}


@app.post("/runs/task")
def task_event(event: TaskEvent):
    run = get_run()

    ensure_play(run, event.play)
    play_bucket = run["plays"][event.play]

    ensure_host(play_bucket, event.host)
    host_bucket = play_bucket["hosts"][event.host]

    ensure_task(host_bucket, event.task)
    task_bucket = host_bucket["tasks"][event.task]

    # ----------------------------
    # TIMESTAMPED EVENT RECORD
    # ----------------------------
    event_record = {
        "host": event.host,
        "play": event.play,
        "task": event.task,
        "status": event.status,
        "changed": event.changed,
        "msg": event.msg,
        "received_at": now_iso(),
        "received_ts": now_ts()
    }

    log_json("TASK EVENT", event_record)

    # ----------------------------
    # UPDATE COUNTERS
    # ----------------------------
    task_bucket[event.status] += 1
    run["summary"][event.status] += 1
    play_bucket["summary"][event.status] += 1

    return {"status": "recorded"}


@app.post("/runs/end")
def end_run(payload: Dict[str, Any]):
    run = get_run()

    run["end_time"] = now_ts()
    run["end_time_iso"] = now_iso()
    run["duration"] = round(run["end_time"] - run["start_time"], 2)

    # ----------------------------
    # PLAY AGGREGATION
    # ----------------------------
    plays_report = {}

    for play, data in run["plays"].items():
        totals = data["summary"]
        total_tasks = sum(totals.values())
        success = totals["ok"] + totals["changed"]

        plays_report[play] = {
            **totals,
            "tasks_total": total_tasks,
            "success_rate": round(success / total_tasks, 4) if total_tasks else 1.0
        }

    # ----------------------------
    # FINAL REPORT
    # ----------------------------
    total = sum(run["summary"].values())
    success = run["summary"]["ok"] + run["summary"]["changed"]

    run["summary"]["tasks_total"] = total
    run["summary"]["success_rate"] = round(success / total, 4) if total else 1.0
    run["plays"] = plays_report

    log("RUN COMPLETE")
    log_json("FINAL REPORT", run)

    return {
        "status": "finished",
        "run_id": run["id"],
        "duration": run["duration"]
    }


@app.get("/runs/latest")
def latest():
    return get_run()