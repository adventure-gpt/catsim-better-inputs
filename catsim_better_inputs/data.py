"""catSim's game data: check a folder, and make it with catSim's own dumper when it is missing.

    python -m catsim_better_inputs.setup --keys keys.txt      # at setup time, before any match (the recommended way)
    ensure_data("catsim_data", keys="keys.txt")              # the same from Python: blocks until the folder is good
    DumpJob("catsim_data", keys="keys.txt").start()           # in the background (Corrector(dump_if_missing=True))

The dump is `python -m catsim.dump --out DIR [--keys FILE] [--game DIR]` and then `python -m catsim.dump --check DIR`,
each a separate process. The dumper reads this machine's Rocket League files (found from Steam or Epic, or `game`) and
never starts or touches the game. A background job uses no threads: it only starts processes and looks at them when
polled (the corrector polls on each packet), so it never runs anything inside the bot's own work.

Only one process at a time dumps into a folder: a lock file next to it (`catsim_data.dump.lock`) makes every other bot
or setup run on the machine wait for that dump instead of starting its own. The dumper's output goes to
`catsim_data.dump.log`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

DUMP_TIMEOUT = 1800.0                  # s; with catsim-rl[dump] (the cryptography package) a dump takes a few minutes
CHECK_TIMEOUT = 120.0                  # s; a check takes well under a second


def _python() -> list | None:
    """The command that runs this Python, or None in a frozen executable (it can't run `-m`)."""
    if getattr(sys, "frozen", False) or not sys.executable:
        return None
    return [sys.executable]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"      # the dumper's progress lines arrive as they are printed
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _quiet_flags(background: bool) -> int:
    """Windows: no console window (it would pop up over the game) and, for a background dump, below-normal priority."""
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if background:
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return flags


def _check_cmd(data_dir: str) -> list | None:
    py = _python()
    return None if py is None else py + ["-m", "catsim.dump", "--check", str(data_dir)]


def _check_in_process(data_dir: str) -> tuple:
    """The dumper's own check function, in this process (for a frozen bot, which can't start `python -m`)."""
    from pathlib import Path

    from catsim.dump import check
    lines: list = []
    return bool(check(Path(data_dir), log=lines.append)), "\n".join(lines)


def check_data(data_dir, echo: bool = False) -> tuple:
    """`python -m catsim.dump --check DIR`: (True, its report) when `data_dir` holds catSim's checked game data,
    (False, the report) when it doesn't (missing, incomplete, or not the checked data). Blocks for the check
    (well under a second)."""
    data_dir = str(data_dir)
    if not os.path.isdir(data_dir):
        ok, text = False, f"{os.path.abspath(data_dir)} is not a folder yet"
    elif _check_cmd(data_dir) is None:
        ok, text = _check_in_process(data_dir)
    else:
        r = subprocess.run(_check_cmd(data_dir), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=_env(), stdin=subprocess.DEVNULL, creationflags=_quiet_flags(False),
                           timeout=CHECK_TIMEOUT)
        ok, text = r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()
    if echo and text:
        print(text, flush=True)
    return ok, text


def _alive(pid) -> bool:
    """Is process `pid` still running? (Never signals it.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5          # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class DumpJob:
    """One run of catSim's dumper into `data_dir`, as a separate process, then catSim's check of the result.

        job = DumpJob("catsim_data", keys="keys.txt").start()
        job.poll()       # "running", then "done" (the folder passed `--check`) or "failed" (`job.error` says why)
        job.wait(10)     # polls until it has finished either way (True) or the time is up (False)

    `poll()` never blocks: it looks at the processes and starts the next step. Nothing runs in between, so the job
    moves on only when polled (the corrector polls it on every packet). The dumper runs at below-normal priority, with
    no console window, and its output in `<data_dir>.dump.log`. If another process on this machine is already dumping into the same folder,
    this job waits for that one and then checks the folder. `echo=True` lets the dumper print its progress here and
    prints the check (the setup command). `on_done(job)` is called once it has finished. Nothing here raises: every
    problem ends as state "failed" with a reason."""

    def __init__(self, data_dir="catsim_data", keys: str | None = None, game: str | None = None,
                 timeout: float = DUMP_TIMEOUT, echo: bool = False, on_done=None):
        self.data_dir = os.path.abspath(str(data_dir))
        base = self.data_dir.rstrip("\\/")
        self.lock_path = base + ".dump.lock"
        self.log_path = base + ".dump.log"
        self.keys = os.path.abspath(keys) if keys else None
        self.game = game
        self.timeout = float(timeout)
        self.echo = echo
        self.on_done = on_done
        self.state = "new"
        self.error: str | None = None
        self.proc = None                  # the dumper's subprocess.Popen, once this job started one
        self.returncode = None            # the dumper's exit code
        self.waited_for_other = False     # another process was dumping into this folder; this job waited for it
        self.started = None
        self.finished = None
        self._token = uuid.uuid4().hex
        self._owner = False
        self._step = None                 # "lock", "check" or "dump"
        self._deadline = 0.0
        self._next_try = 0.0
        self._told_waiting = False
        self._check_proc = None
        self._check_out = None
        self._check_deadline = 0.0
        self._after_dump = False
        self._log = None
        self._cancel = False

    # ---------------------------------------------------------------- public

    def command(self) -> list:
        cmd = (_python() or []) + ["-m", "catsim.dump", "--out", self.data_dir]
        if self.keys:
            cmd += ["--keys", self.keys]
        if self.game:
            cmd += ["--game", str(self.game)]
        if os.name != "nt" and not self.echo and shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd
        return cmd

    def start(self) -> "DumpJob":
        if self.state == "new":
            self.state = "running"
            self.started = time.time()
            self._deadline = time.monotonic() + self.timeout
            self._step = "lock"
            self.poll()
        return self

    def poll(self) -> str:
        """Move the job on without waiting; returns its state."""
        if self.state != "running":
            return self.state
        try:
            if self._cancel:
                self._kill()
                self._finish(False, "cancelled")
            elif self._step == "lock":
                self._poll_lock()
            elif self._step == "check":
                self._poll_check()
            elif self._step == "dump":
                self._poll_dump()
        except Exception as e:                # never let the bot see this
            self._kill()
            self._finish(False, f"{type(e).__name__}: {e}")
        return self.state

    def wait(self, timeout: float | None = None, interval: float = 0.2) -> bool:
        end = None if timeout is None else time.monotonic() + timeout
        while self.poll() == "running":
            if end is not None and time.monotonic() >= end:
                return False
            time.sleep(interval)
        return True

    def cancel(self) -> None:
        """Stop the dumper (if this job started it) and end the job as failed."""
        self._cancel = True
        self.poll()

    # ---------------------------------------------------------------- steps

    def _say(self, msg: str) -> None:
        if self.echo:
            print(msg, flush=True)

    def _poll_lock(self) -> None:
        now = time.monotonic()
        if now < self._next_try:
            return
        if self._take_lock():
            self._start_check(after_dump=False)       # it may have been made while we waited, or just before
            return
        holder = self._read_lock()
        if self._holder_alive(holder):                # another process is dumping into this folder: wait for it
            self.waited_for_other = True
            if not self._told_waiting:
                self._say(f"another process is already making catSim's data in {self.data_dir}; waiting for it ...")
                self._told_waiting = True
            if now > self._deadline:
                return self._finish(False, f"waited {self.timeout:.0f} s for another process's dump into "
                                           f"{self.data_dir} (lock {self.lock_path})")
            self._next_try = now + 1.0
            return
        if holder is not None and self._read_lock() == holder:     # stale (its dumper is gone): take it over
            try:
                os.remove(self.lock_path)
            except OSError:
                self._next_try = now + 0.5

    def _start_check(self, after_dump: bool) -> None:
        self._after_dump = after_dump
        if not os.path.isdir(self.data_dir):
            return self._checked(False, f"{self.data_dir} is not a folder")
        cmd = _check_cmd(self.data_dir)
        if cmd is None:
            return self._checked(*_check_in_process(self.data_dir))
        self._check_out = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        self._check_proc = subprocess.Popen(cmd, stdout=self._check_out, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, env=_env(), creationflags=_quiet_flags(True))
        self._check_deadline = time.monotonic() + CHECK_TIMEOUT
        self._step = "check"

    def _poll_check(self) -> None:
        rc = self._check_proc.poll()
        if rc is None:
            if time.monotonic() > self._check_deadline:
                self._check_proc.kill()
                self._check_proc.wait()
                self._close_check()
                return self._checked(False, "catSim's data check didn't finish")
            return
        text = ""
        try:
            self._check_out.seek(0)
            text = self._check_out.read().strip()
        finally:
            self._close_check()
        self._checked(rc == 0, text)

    def _close_check(self) -> None:
        if self._check_out is not None:
            try:
                self._check_out.close()
            except OSError:
                pass
        self._check_out, self._check_proc = None, None

    def _checked(self, ok: bool, text: str) -> None:
        if self._after_dump and self.echo and text:
            print(text, flush=True)
        if ok:
            return self._finish(True)
        if self._after_dump:
            return self._finish(False, f"{self._reason(text)} (dumper exit code {self.returncode}{self._where()})")
        self._start_dump()

    def _start_dump(self) -> None:
        if self.keys and not os.path.isfile(self.keys):
            return self._finish(False, f"the key file {self.keys} doesn't exist")
        if _python() is None:
            return self._finish(False, "this bot runs as a frozen executable, which can't start catSim's dumper: "
                                       "make catsim_data at setup time instead")
        parent = os.path.dirname(self.data_dir)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._say(f"making catSim's data in {self.data_dir} from this PC's Rocket League files "
                  f"(a few minutes; the game is never started) ...")
        try:
            if self.echo:                                   # the dumper prints straight to this console
                self.proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, env=_env())
            else:
                self._log = open(self.log_path, "w", encoding="utf-8", errors="replace")
                self.proc = subprocess.Popen(self.command(), stdout=self._log, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, env=_env(), creationflags=_quiet_flags(True))
        except OSError as e:
            self._close_log()
            return self._finish(False, f"couldn't start catSim's dumper: {e}")
        self._write_lock(child=self.proc.pid)
        self._step = "dump"

    def _poll_dump(self) -> None:
        rc = self.proc.poll()
        if rc is None:
            if time.monotonic() > self._deadline:
                self._kill()
                self._close_log()
                return self._finish(False, f"the dump took longer than {self.timeout:.0f} s and was stopped "
                                           f"(is catsim-rl[dump] installed? without the cryptography package a dump "
                                           f"takes an hour or more{self._where()})")
            return
        self.returncode = rc
        self._close_log()
        self._start_check(after_dump=True)

    def _where(self) -> str:
        return "" if self.echo else f"; log: {self.log_path}"

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def _kill(self) -> None:
        for p in (self.proc, self._check_proc):
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        self._close_check()
        self._close_log()

    def _finish(self, ok: bool, error: str | None = None) -> None:
        if self.state != "running":
            return
        self._release()
        self.error = None if ok else (error or "unknown error")
        self.state = "done" if ok else "failed"
        self.finished = time.time()
        if self.on_done is not None:
            try:
                self.on_done(self)
            except Exception:
                pass

    def _reason(self, check_text: str) -> str:
        """The dumper's own last word on what went wrong."""
        lines = []
        if not self.echo:
            try:
                with open(self.log_path, encoding="utf-8", errors="replace") as f:
                    lines = [ln.strip() for ln in f.read()[-8000:].splitlines() if ln.strip()]
            except OSError:
                pass
        for ln in reversed(lines):
            if ln.lower().startswith("error:"):
                return ln[6:].strip()[-400:]
            if "error" in ln.lower():
                return ln[-400:]
        if lines:
            return lines[-1][-400:]
        if self.echo:
            return "the dump failed (its output is above)"
        return (check_text.splitlines() or ["the folder doesn't hold catSim's checked data"])[-1][-400:]

    # ---------------------------------------------------------------- the lock

    def _lock_body(self, child) -> dict:
        return {"pid": os.getpid(), "child": child, "time": time.time(), "data": self.data_dir, "token": self._token}

    def _take_lock(self) -> bool:
        parent = os.path.dirname(self.lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._lock_body(None), f)
        self._owner = True
        return True

    def _write_lock(self, child: int) -> None:
        if self._owner and self._lock_is_mine():
            try:
                with open(self.lock_path, "w", encoding="utf-8") as f:
                    json.dump(self._lock_body(int(child)), f)
            except OSError:
                pass

    def _read_lock(self):
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            try:
                return {"unreadable": True, "mtime": os.path.getmtime(self.lock_path)}
            except OSError:
                return None

    def _lock_is_mine(self) -> bool:
        h = self._read_lock()
        return isinstance(h, dict) and h.get("token") == self._token

    def _holder_alive(self, holder) -> bool:
        if holder is None:
            return False
        if holder.get("unreadable"):                     # being written right now, or junk
            return time.time() - holder.get("mtime", 0.0) < 10.0
        if time.time() - float(holder.get("time", 0.0)) > self.timeout + 60.0:
            return False
        if holder.get("child"):
            return _alive(holder["child"])
        return _alive(holder.get("pid"))

    def _release(self) -> None:
        if self._owner:
            self._owner = False
            if self._lock_is_mine():                     # (another process may have taken over a lock it saw stale)
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass


def ensure_data(data_dir: str = "catsim_data", keys: str | None = None, game: str | None = None,
                timeout: float = DUMP_TIMEOUT, echo: bool = False) -> str:
    """Make sure `data_dir` holds catSim's game data; if it doesn't, dump it from this machine's own Rocket League
    install (found from Steam or Epic, or `game`) with the key file `keys` (default: $CATSIM_RL_KEYS). Only reads the
    game's files; never starts the game. Blocks: the first dump takes a few minutes, later calls just check the folder
    (well under a second). `echo=True` prints the check and the dumper's progress. Needs catsim-rl's dumper with its
    fast AES: pip install "catsim-rl[dump]". Returns the folder; raises RuntimeError if the dump fails."""
    if echo:
        print(f"checking {os.path.abspath(str(data_dir))} ...", flush=True)
    ok, _ = check_data(data_dir, echo=echo)
    if ok:
        return data_dir
    job = DumpJob(data_dir, keys=keys, game=game, timeout=timeout, echo=echo).start()
    try:
        job.wait()
    except KeyboardInterrupt:
        job.cancel()
        raise
    if job.state != "done":
        raise RuntimeError(f"catsim data dump failed: {job.error}")
    return data_dir
