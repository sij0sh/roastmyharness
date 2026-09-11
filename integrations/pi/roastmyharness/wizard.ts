import { homedir } from "node:os";
import { join } from "node:path";
import { readdir, readFile, stat } from "node:fs/promises";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { runBridgeJson, type ExecHost } from "./core.ts";
import {
	controlOptions,
	detectGitHubUrl,
	expandPath,
	fullId,
	historicTaskOptions,
	orderModels,
	orderThinkingLevels,
	recommendedThinkingFor,
	resolveHistoricTasks,
	resolveStandardTasks,
	sampleTasks,
	standardTaskOptions,
	supportedThinkingLevels,
	taskLabel,
	type TaskChoice,
	type WizardAnswers,
	type WizardContextData,
	type WizardModel,
} from "./wizard-options.ts";

export async function stageGitHubSource(
	host: ExecHost,
	rawText: string,
): Promise<{ url: string; rev: string; staged_path: string } | null> {
	const url = detectGitHubUrl(rawText);
	if (!url) return null;
	const slug = url
		.replace(/^https:\/\/github\.com\//, "")
		.replace(/\.git$/, "")
		.toLowerCase()
		.replace(/[^a-z0-9._-]+/g, "-");
	const dest = join(homedir(), ".roastmyharness", "cache", "sources", slug);
	const cloned = await host.exec("git", ["clone", "--depth", "1", url, dest], {
		timeout: 180_000,
	});
	if (cloned.code !== 0) {
		throw new Error((cloned.stderr || cloned.stdout || `git clone exited ${cloned.code}`).slice(0, 500));
	}
	const head = await host.exec("git", ["rev-parse", "HEAD"], { cwd: dest, timeout: 15_000 });
	const sha = (head.stdout ?? "").trim();
	if (head.code !== 0 || !/^[0-9a-f]{40}$/.test(sha)) {
		throw new Error("Cloned the repo but could not resolve its commit SHA");
	}
	return { url, rev: sha, staged_path: dest };
}

async function recentTaskRoots(): Promise<string[]> {
	const override = process.env.ROAST_MY_HARNESS_RUNS_DIR;
	const runsRoot = override?.trim() || join(homedir(), ".local", "share", "roastmyharness", "runs");
	let entries;
	try {
		entries = await readdir(runsRoot, { withFileTypes: true });
	} catch {
		return [];
	}
	const roots = new Map<string, number>();
	for (const entry of entries) {
		if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
		const manifestPath = join(runsRoot, entry.name, "manifest.json");
		let text: string;
		let mtime: number;
		try {
			text = await readFile(manifestPath, "utf8");
			mtime = (await stat(manifestPath)).mtimeMs;
		} catch {
			continue;
		}
		let tasksPath: unknown;
		try {
			tasksPath = (JSON.parse(text) as { tasks_path?: unknown }).tasks_path;
		} catch {
			continue;
		}
		if (typeof tasksPath !== "string" || !tasksPath) continue;
		const root = expandPath(tasksPath, process.cwd());
		const known = roots.get(root);
		if (known === undefined || mtime > known) roots.set(root, mtime);
	}
	return [...roots.entries()]
		.sort((a, b) => b[1] - a[1])
		.slice(0, 3)
		.map(([root]) => root);
}

async function fetchContext(
	host: ExecHost,
	taskRoot: string,
	model: string,
	thinking: string,
): Promise<WizardContextData | null> {
	let response;
	try {
		response = await runBridgeJson(host, [
			"_bridge",
			"wizard-context",
			taskRoot,
			"--model",
			model,
			"--thinking",
			thinking,
		]);
	} catch {
		return null;
	}
	if (!response.ok) return null;
	return response as unknown as WizardContextData;
}

export async function collectWizard(
	host: ExecHost,
	args: string,
	ctx: ExtensionContext,
	prefill = "",
): Promise<{ answers: WizardAnswers; stagedNote: string } | null> {
	if (!ctx.hasUI) throw new Error("The wizard needs an interactive Pi session");

	const variantAnswer = await ctx.ui.editor(
		"Step 1/6 - What variant should run? " +
			"Accepted: a local extension path with its entry file, a pinned npm package, " +
			"a skill path, a GitHub repo URL (staged to a local path for you), " +
			"or a tool restriction such as bash only.",
		prefill,
	);
	if (variantAnswer === undefined || !variantAnswer.trim()) return null;
	const variantRequest = variantAnswer.trim();
	let stagedNote = "";
	if (detectGitHubUrl(variantRequest)) {
		try {
			const staged = await stageGitHubSource(host, variantRequest);
			if (staged) {
				stagedNote =
					`Staged GitHub source ${staged.url} @ ${staged.rev.slice(0, 12)} ` +
					`at local path ${staged.staged_path}. Use this absolute local path ` +
					`in the spec and record the URL plus short SHA in the hypothesis.`;
			}
		} catch (error) {
			ctx.ui.notify(
				`GitHub staging failed, continuing with the raw request: ${error instanceof Error ? error.message : String(error)}`,
				"warning",
			);
		}
	}

	const scoped = ctx.scopedModels.map((item) => item.model);
	const candidates = scoped.length ? scoped : ctx.modelRegistry.getAvailable();
	const models: WizardModel[] = candidates.map((m) => ({
		provider: String(m.provider),
		id: m.id,
		name: m.name,
		reasoning: m.reasoning,
		thinkingLevelMap: m.thinkingLevelMap as WizardModel["thinkingLevelMap"],
	}));
	if (!models.length) throw new Error("Pi has no authenticated models available");
	const currentId = ctx.model ? `${String(ctx.model.provider)}/${ctx.model.id}` : undefined;
	const ordered = orderModels(models, currentId);
	const modelOptions = ordered.map(({ model, recommended }) =>
		recommended ? `${fullId(model)} (recommended)` : fullId(model),
	);
	const modelChoice = await ctx.ui.select("Step 2/6 - Model", modelOptions);
	if (modelChoice === undefined) return null;
	const picked = ordered.find(
		({ model }) => modelChoice === fullId(model) || modelChoice === `${fullId(model)} (recommended)`,
	);
	if (!picked) throw new Error(`Unknown model: ${modelChoice}`);
	const selected = picked.model;
	const modelRef = fullId(selected);

	const levels = supportedThinkingLevels(selected);
	let thinking: string;
	if (levels.length === 1) {
		thinking = levels[0];
	} else {
		const orderedLevels = orderThinkingLevels(levels, recommendedThinkingFor(selected));
		const levelChoice = await ctx.ui.select("Step 3/6 - Thinking level", orderedLevels);
		if (levelChoice === undefined) return null;
		thinking = levelChoice;
	}

	const searchRoots: string[] = [];
	const addRoot = (root: string) => {
		if (!searchRoots.includes(root)) searchRoots.push(root);
	};
	if (args.trim()) addRoot(expandPath(args.trim(), ctx.cwd));
	addRoot(join(ctx.cwd, "tasks", "deepswe", "tasks"));
	addRoot(ctx.cwd);
	for (const recent of await recentTaskRoots()) addRoot(recent);
	let data: WizardContextData | null = null;
	for (const root of searchRoots) {
		data = await fetchContext(host, root, modelRef, thinking);
		if (data) break;
	}
	if (!data) {
		ctx.ui.notify(`No Pier tasks found. Searched: ${searchRoots.join(", ")}.`, "error");
		return null;
	}

	const controls = controlOptions(data.historic.count);
	const controlChoice = await ctx.ui.select(
		"Step 4/6 - Control",
		controls.map((c) => c.label),
	);
	if (controlChoice === undefined) return null;
	const control = controls.find((c) => c.label === controlChoice)?.value ?? "fresh";

	let choice: TaskChoice;
	if (control === "historic") {
		const options = historicTaskOptions(data);
		const picked_tasks = await ctx.ui.select(
			"Step 5/6 - Tasks",
			options.map((o) => o.label),
		);
		if (picked_tasks === undefined) return null;
		choice = options.find((o) => o.label === picked_tasks)?.value ?? "historic";
	} else {
		const options = standardTaskOptions(data);
		const picked_tasks = await ctx.ui.select(
			"Step 5/6 - Tasks",
			options.map((o) => o.label),
		);
		if (picked_tasks === undefined) return null;
		choice = options.find((o) => o.label === picked_tasks)?.value ?? "full";
	}

	let taskIds: string[];
	if (choice === "historic") {
		taskIds = [...data.historic.task_ids];
	} else if (choice !== "custom") {
		taskIds = resolveStandardTasks(choice, data);
	} else {
		const pool = control === "historic" ? data.historic.task_ids : data.discovered;
		const ceiling = control === "historic" ? undefined : pool.length;
		const hint = control === "historic"
			? `Count of 1 or more (${data.historic.count} historic tasks on disk; more adds curated tasks)`
			: `Count from 1 to ${pool.length} (tasks are picked at random)`;
		while (true) {
			const value = await ctx.ui.input("Step 5/6 - How many tasks?", hint);
			if (value === undefined) return null;
			const count = Number(value.trim());
			if (Number.isInteger(count) && count >= 1 && (ceiling === undefined || count <= ceiling)) {
				taskIds = control === "historic"
					? resolveHistoricTasks(count, data, selected.id)
					: sampleTasks(pool, count);
				break;
			}
			ctx.ui.notify(
				ceiling === undefined ? "Choose a count of 1 or more." : `Choose a count from 1 to ${ceiling}.`,
				"warning",
			);
		}
	}
	if (!taskIds.length) {
		ctx.ui.notify("The chosen task set is empty.", "error");
		return null;
	}

	const repChoice = await ctx.ui.select("Step 6/6 - Repetitions", [
		"1 (single run)",
		"2 repetitions",
		"3 repetitions",
		"4 repetitions",
	]);
	if (repChoice === undefined) return null;
	const repetitions = Number(repChoice.slice(0, 1));

	const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
	const experimentName = `roast-${stamp.toLowerCase()}`;
	return {
		answers: {
			variantRequest,
			modelProvider: selected.provider,
			modelId: selected.id,
			thinking,
			control,
			taskRoot: data.task_root,
			taskIds,
			taskLabel: taskLabel(choice, taskIds),
			experimentName,
			repetitions,
			historicCount: data.historic.count,
		},
		stagedNote,
	};
}
