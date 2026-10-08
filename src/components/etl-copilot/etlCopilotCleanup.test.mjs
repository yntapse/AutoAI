import assert from "node:assert/strict";
import test from "node:test";

import { getJoinPreviewRows } from "./joinPreviewModel.mjs";
import {
  createBuildGuard,
  startEtlCopilotPolling,
} from "../../services/etlCopilotPolling.mjs";

function createFakeScheduler() {
  let now = 0;
  let nextId = 0;
  const tasks = new Map();

  return {
    setInterval(callback, delay) {
      const id = ++nextId;
      tasks.set(id, { callback, delay, next: now + delay, repeat: true });
      return id;
    },
    clearInterval(id) {
      tasks.delete(id);
    },
    setTimeout(callback, delay) {
      const id = ++nextId;
      tasks.set(id, { callback, delay, next: now + delay, repeat: false });
      return id;
    },
    clearTimeout(id) {
      tasks.delete(id);
    },
    async advance(milliseconds) {
      const target = now + milliseconds;
      while (true) {
        const due = [...tasks.entries()]
          .filter(([, task]) => task.next <= target)
          .sort((left, right) => left[1].next - right[1].next)[0];
        if (!due) break;

        const [id, task] = due;
        now = task.next;
        if (task.repeat) task.next += task.delay;
        else tasks.delete(id);
        task.callback();
        await Promise.resolve();
        await Promise.resolve();
      }
      now = target;
    },
    get pendingCount() {
      return tasks.size;
    },
  };
}

function pollingOptions(overrides = {}) {
  return {
    jobId: "job-1",
    readStatus: async () => ({ status: "running" }),
    onCompleted() {},
    onFailed() {},
    onNotFound() {},
    onError(error) {
      throw error;
    },
    onTimeout() {},
    ...overrides,
  };
}

test("completed job stops the 2-second poll", async () => {
  const scheduler = createFakeScheduler();
  let reads = 0;
  let completed = 0;
  startEtlCopilotPolling(
    pollingOptions({
      scheduler,
      readStatus: async () => ({
        status: ++reads === 1 ? "running" : "completed",
      }),
      onCompleted: () => completed++,
    }),
  );

  await Promise.resolve();
  assert.equal(reads, 1);
  await scheduler.advance(2000);
  assert.equal(reads, 2);
  assert.equal(completed, 1);
  assert.equal(scheduler.pendingCount, 0);
  await scheduler.advance(90000);
  assert.equal(reads, 2);
});

test("failed job stops polling", async () => {
  const scheduler = createFakeScheduler();
  let failed = 0;
  let reads = 0;
  startEtlCopilotPolling(
    pollingOptions({
      scheduler,
      readStatus: async () => {
        reads++;
        return { status: "failed", error: { detail: "Build failed." } };
      },
      onFailed: () => failed++,
    }),
  );

  await Promise.resolve();
  assert.equal(failed, 1);
  assert.equal(reads, 1);
  assert.equal(scheduler.pendingCount, 0);
});

test("poll timeout fires at 90 seconds and clears both timers", async () => {
  const scheduler = createFakeScheduler();
  let timeouts = 0;
  startEtlCopilotPolling(
    pollingOptions({ scheduler, onTimeout: () => timeouts++ }),
  );

  await Promise.resolve();
  await scheduler.advance(89999);
  assert.equal(timeouts, 0);
  assert.equal(scheduler.pendingCount, 2);
  await scheduler.advance(1);
  assert.equal(timeouts, 1);
  assert.equal(scheduler.pendingCount, 0);
});

test("404 job-lost response stops polling", async () => {
  const scheduler = createFakeScheduler();
  let missing = 0;
  let reads = 0;
  startEtlCopilotPolling(
    pollingOptions({
      scheduler,
      readStatus: async () => {
        reads++;
        const error = new Error("Job was not found.");
        error.status = 404;
        throw error;
      },
      onNotFound: () => missing++,
    }),
  );

  await Promise.resolve();
  assert.equal(missing, 1);
  assert.equal(reads, 1);
  assert.equal(scheduler.pendingCount, 0);
});

test("a 404 status value also stops polling", async () => {
  const scheduler = createFakeScheduler();
  let missing = 0;
  startEtlCopilotPolling(
    pollingOptions({
      scheduler,
      readStatus: async () => ({ status: "404" }),
      onNotFound: () => missing++,
    }),
  );

  await Promise.resolve();
  assert.equal(missing, 1);
  assert.equal(scheduler.pendingCount, 0);
});

test("cancel clears timers and suppresses callbacks after unmount", async () => {
  const scheduler = createFakeScheduler();
  let finishRead;
  let completed = 0;
  const cancel = startEtlCopilotPolling(
    pollingOptions({
      scheduler,
      readStatus: () => new Promise((resolve) => { finishRead = resolve; }),
      onCompleted: () => completed++,
    }),
  );

  cancel();
  finishRead({ status: "completed" });
  await Promise.resolve();
  assert.equal(completed, 0);
  assert.equal(scheduler.pendingCount, 0);
});

test("build guard prevents a duplicate start until the job ends", () => {
  const guard = createBuildGuard();
  assert.equal(guard.tryStart(), true);
  assert.equal(guard.tryStart(), false);
  guard.finish();
  assert.equal(guard.tryStart(), true);
});

test("Join Preview uses the customer-to-orders plan path, not a catalog self-edge", () => {
  const customerOrders = {
    entity_table: "MLTest_LargeCustomers",
    source_table: "MLTest_LargeOrders",
    verified: true,
    hops: [
      {
        left_table: "MLTest_LargeCustomers",
        left_column: "CustomerID",
        right_table: "MLTest_LargeOrders",
        right_column: "CustomerID",
      },
    ],
  };
  const rows = getJoinPreviewRows([customerOrders]);

  assert.equal(rows.length, 1);
  assert.equal(
    rows[0].title,
    "MLTest_LargeCustomers to MLTest_LargeOrders",
  );
  assert.equal(
    rows.some((row) => row.title === "MLTest_LargeCustomers to MLTest_LargeCustomers"),
    false,
  );
});

test("Join Preview preserves a genuine self-join when the plan contains it", () => {
  const selfJoin = {
    entity_table: "Employees",
    source_table: "Employees",
    verified: true,
    hops: [
      {
        left_table: "Employees",
        left_column: "ManagerID",
        right_table: "Employees",
        right_column: "EmployeeID",
      },
    ],
  };

  const rows = getJoinPreviewRows([selfJoin]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].path.hops[0].left_table, "Employees");
  assert.equal(rows[0].path.hops[0].right_table, "Employees");
});