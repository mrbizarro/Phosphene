# Local image diagnostics

Image jobs using mflux keep up to 16 local records in state/image-diagnostics.json,
with a 32 KiB file limit and owner-only file permissions. The existing analytics
privacy switch also disables this collection and excludes saved records from
support export. No diagnostic fields are added to analytics or network delivery.

Records contain the selected preset, recognized model ID, effective command-line
quantization and steps, canvas, reference count, available GiB before admission
and immediately before spawn, last positively observed phase, child exit status,
start time and outcome. Direct engine calls without a panel preset say unknown.
Custom model paths and unrecognized model IDs say unknown to avoid exposing names.
Unavailable memory readings are null, not zero. Admission readings precede the
existing preflight, which can subsequently release the idle video helper.

The native excerpt keeps at most eight exact recognized error phrases from the
worker stream. Surrounding prose, paths, credentials, environment values and
prompt text are discarded before persistence. Unknown exception prose is not
saved. This does not scan macOS crash reports or infer OOM from a signal. No
phase marker means unknown; a download line alone is not a download diagnosis.

A single daemon writes sanitized snapshots through a queue of eight. Slow or
failed storage drops evidence instead of blocking a job. A process exit before
spawn leaves exit status unknown. Abrupt panel shutdown can lose queued records.
The outcome describes child execution, not validation of the resulting image.

The existing bug-context log bundle includes revalidated records. The native
app includes them in its existing log_tail only when support diagnostics are
selected, with the same preview and consent flow. Export validates the closed
schema again, even if the local file was edited. Disabling collection hides old
records from export; it does not delete them. No automatic upload is added.

Tests use CPU-only fixture workers for download failure, load and render abort,
explicit allocation failure, unknown abort, timeout and Stop. These tests prove
collection and privacy boundaries, not a fix for the native Qwen crash or new
memory requirements. No defaults, retries or RAM floors change.
