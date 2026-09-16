/** Pi-extension progress helpers. Run with: node --test tests/pi/ */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
	countDone,
	formatElapsed,
	isWatchDetails,
	oneLineStatus,
} from "../../integrations/pi/roastmyharness/core.ts";

const base = (over = {}) => ({
	stream: true,
	experiment_id: "exp-1",
	state: "RUNNING",
	final: false,
	recent: [],
	summaries: [],
	...over,
});

describe("isWatchDetails", () => {
	it("accepts live watch details", () => {
		assert.equal(isWatchDetails(base()), true);
	});
	it("rejects validate-style responses and junk", () => {
		assert.equal(isWatchDetails({ ok: true, state: "ready_for_confirmation" }), false);
		assert.equal(isWatchDetails({ stream: true }), false);
		assert.equal(isWatchDetails({}), false);
		assert.equal(isWatchDetails(null), false);
		assert.equal(isWatchDetails("exp-1"), false);
	});
});

describe("countDone", () => {
	it("counts P/F/E as done over every cell", () => {
		const details = base({ matrix: { control: { t1: "P", t2: "." }, bare: { t1: "E", t2: "H" } } });
		assert.deepEqual(countDone(details), { done: 2, total: 4 });
	});
	it("is 0/0 before the first snapshot arrives", () => {
		assert.deepEqual(countDone(base()), { done: 0, total: 0 });
	});
});

describe("oneLineStatus", () => {
	it("stays compact with no progress", () => {
		assert.equal(oneLineStatus(base()), "state=RUNNING done=0/0");
	});
	it("surfaces running trials and elapsed time", () => {
		const text = oneLineStatus(base({ running: [["agents-md", "t1"]], elapsed_sec: 65 }));
		assert.match(text, /running=1 \(agents-md\/t1\)/);
		assert.match(text, /elapsed=1m05s/);
	});
	it("truncates long running lists", () => {
		const text = oneLineStatus(base({
			running: [["a", "t1"], ["b", "t2"], ["c", "t3"]],
		}));
		assert.match(text, /running=3 \(a\/t1, b\/t2, …\)/);
	});
	it("marks detached runs", () => {
		assert.match(oneLineStatus(base({ detached: true })), /detached/);
	});
});

describe("formatElapsed", () => {
	it("formats seconds, minutes, and hours", () => {
		assert.equal(formatElapsed(5), "5s");
		assert.equal(formatElapsed(65), "1m05s");
		assert.equal(formatElapsed(3661), "1h1m");
	});
	it("guards non-finite input", () => {
		assert.equal(formatElapsed(Number.NaN), "0s");
		assert.equal(formatElapsed(-3), "0s");
	});
});
