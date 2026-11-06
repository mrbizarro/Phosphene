"""Make a test child LOOK like it died from a signal, without really crashing.

WHY. A child that really aborts (sends itself SIGABRT, calls abort(),
a segfault) makes macOS ReportCrash write a DiagnosticReports file and pop a
"Python quit unexpectedly" dialog on the Mac running the suite. A full-suite
run fired five of them in a row on the owner's Mac (4 Nov 2026), on every run.
RLIMIT_CORE=0 only stops the core file, not the dialog.

HOW. The child exits normally with a sentinel status (FAKE_SIGNAL_EXIT); the
Popen subclass below rewrites that one status to -signal when it is reaped, so
poll(), wait() and .returncode all report "killed by SIGABRT" (-6) exactly as
a real crash would. Everything else (pipes, timing, other exit codes) is the
real child's. test_no_real_crash_signals.py keeps real crash signals out of
the suite.
"""
from __future__ import annotations

import signal
import subprocess

#: Exit status a child uses to say "pretend I was killed by a signal".
FAKE_SIGNAL_EXIT = 86

#: Python lines that end a child "by SIGABRT" without a real abort.
CHILD_FAKE_ABORT = f"import os; os._exit({FAKE_SIGNAL_EXIT})\n"


def popen_class(sig: int = signal.SIGABRT):
    """A Popen subclass that reports FAKE_SIGNAL_EXIT as death by ``sig``."""
    base = subprocess.Popen
    # Patching subprocess.Popen with our own class and calling popen_class()
    # again must not stack subclasses of the fake.
    while getattr(base, "_fake_crash_base", None) is not None:
        base = base._fake_crash_base

    class FakeSignalPopen(base):
        _fake_crash_base = base

        def _handle_exitstatus(self, *args, **kwargs):
            super()._handle_exitstatus(*args, **kwargs)
            if self.returncode == FAKE_SIGNAL_EXIT:
                self.returncode = -int(sig)

    return FakeSignalPopen


def popen(*args, sig: int = signal.SIGABRT, **kwargs):
    """subprocess.Popen(...) whose FAKE_SIGNAL_EXIT reads as -sig."""
    return popen_class(sig)(*args, **kwargs)
