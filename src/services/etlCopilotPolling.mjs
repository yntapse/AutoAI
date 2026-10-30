/**
 * @typedef {{ status: string, result?: unknown, error?: unknown }} JobStatus
 * @typedef {{
 *   setInterval: typeof globalThis.setInterval,
 *   clearInterval: typeof globalThis.clearInterval,
 *   setTimeout: typeof globalThis.setTimeout,
 *   clearTimeout: typeof globalThis.clearTimeout,
 * }} PollScheduler
 */

function statusCode(error) {
  if (!error || typeof error !== "object") return undefined;
  return error.status ?? error.statusCode;
}

function isJobNotFound(error) {
  if (statusCode(error) === 404) return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /job.*not found|not found.*job/i.test(message);
}

/**
 * Poll an ETL build and own every timer until a terminal state or cancellation.
 *
 * @param {{
 *   jobId: string,
 *   readStatus: (jobId: string, signal: AbortSignal) => Promise<JobStatus>,
 *   onCompleted: (job: JobStatus) => void,
 *   onFailed: (job: JobStatus) => void,
 *   onNotFound: (error?: unknown) => void,
 *   onError: (error: unknown) => void,
 *   onTimeout: () => void,
 *   intervalMs?: number,
 *   timeoutMs?: number,
 *   scheduler?: PollScheduler,
 * }} options
 * @returns {() => void} Cancels timers and aborts any in-flight status request.
 */
export function startEtlCopilotPolling({
  jobId,
  readStatus,
  onCompleted,
  onFailed,
  onNotFound,
  onError,
  onTimeout,
  intervalMs = 2000,
  timeoutMs = 90000,
  scheduler = globalThis,
}) {
  const controller = new AbortController();
  let stopped = false;
  let requestInFlight = false;
  let intervalId;
  let timeoutId;

  function stop() {
    if (stopped) return false;
    stopped = true;
    scheduler.clearInterval(intervalId);
    scheduler.clearTimeout(timeoutId);
    controller.abort();
    return true;
  }

  function finish(callback, value) {
    if (!stop()) return;
    callback(value);
  }

  async function poll() {
    if (stopped || requestInFlight) return;
    requestInFlight = true;

    try {
      const job = await readStatus(jobId, controller.signal);
      if (stopped) return;

      const status = String(job.status).toLowerCase();
      if (status === "completed") {
        finish(onCompleted, job);
      } else if (status === "failed") {
        finish(onFailed, job);
      } else if (status === "404") {
        finish(onNotFound);
      }
    } catch (error) {
      if (stopped) return;
      if (isJobNotFound(error)) {
        finish(onNotFound, error);
      } else {
        finish(onError, error);
      }
    } finally {
      requestInFlight = false;
    }
  }

  intervalId = scheduler.setInterval(() => void poll(), intervalMs);
  timeoutId = scheduler.setTimeout(() => finish(onTimeout), timeoutMs);
  void poll();

  return stop;
}

export function createBuildGuard() {
  let active = false;
  return {
    tryStart() {
      if (active) return false;
      active = true;
      return true;
    },
    finish() {
      active = false;
    },
  };
}