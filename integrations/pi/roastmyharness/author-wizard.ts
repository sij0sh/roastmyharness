import { mkdir, readFile, readdir, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { extname, join, relative, resolve } from "node:path";
import type { Api, Model, Usage } from "@earendil-works/pi-ai";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
	DEFAULT_PI_VERSION,
	SUITE_SCREEN_SIZE,
	type AuthorDetails,
	type RoastResponse,
} from "./core.ts";
import {
	bundledDeepSwe,
	discoverTaskIds,
	expandPath,
	localPiPackages,
	supportedThinkingLevels,
	type AuthorRequest,
	type ControlMode,
	type TaskMode,
	type WizardAnswers,
} from "./author-support.ts";

export function outputPathFor(ctx: ExtensionContext, specPath: string): string {
	const root = resolve(ctx.cwd, ".pi-files", "roastmyharness");
	const output = resolve(ctx.cwd, specPath.replace(/^@/, ""));
	const rel = relative(root, output);
	if (rel.startsWith("..") || resolve(root, rel) !== output || extname(output) !== ".toml") {
		throw new Error("Spec output must be inside .pi-files/roastmyharness");
	}
	return output;
}

const RUNS_DIR_ENV = "ROAST_MY_HARNESS_RUNS_DIR";

function runsRoot(): string {
	const override = process.env[RUNS_DIR_ENV];
	if (override?.trim()) return expandPath(override, process.cwd());
	return join(homedir(), ".local", "share", "roastmyharness", "runs");
}

async function recentTaskRoots(): Promise<string[]> {
	let runDirs;
	try {
		runDirs = await readdir(runsRoot(), { withFileTypes: true });
	} catch {
		return [];
	}
	const roots = new Map<string, number>();
	for (const entry of runDirs) {
		if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
		const manifestPath = join(runsRoot(), entry.name, "manifest.json");
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
		.map(([root]) => root);
}

async function discoverTaskRoot(
	ctx: ExtensionContext,
	argument: string,
): Promise<{ root: string; ids: string[] }> {
	const candidates: string[] = [];
	const add = (candidate: string) => {
		if (!candidates.includes(candidate)) candidates.push(candidate);
	};
	if (argument.trim()) add(expandPath(argument, ctx.cwd));
	const bundled = await bundledDeepSwe();
	if (bundled) add(join(bundled.root, "tasks"));
	add(ctx.cwd);
	for (const recent of await recentTaskRoots()) add(recent);
	for (const candidate of candidates) {
		const ids = await discoverTaskIds(candidate);
		if (ids.length) return { root: candidate, ids };
	}
	throw new Error(
		`No Pier tasks found. Searched: ${candidates.join(", ")}. ` +
			`Pass a task dataset path: /roastmyharness <path>`,
	);
}

function sampleTasks(ids: string[], count: number): string[] {
	const pool = [...ids];
	const picked: string[] = [];
	for (let i = 0; i < count && pool.length; i++) {
		picked.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
	}
	return picked.sort((a, b) => a.localeCompare(b));
}

async function chooseTasks(
	ctx: ExtensionContext,
	mode: TaskMode,
	available: string[],
	curated?: string[],
): Promise<{ ids: string[]; includeAll: boolean } | null> {
	if (mode === "full") return { ids: available, includeAll: true };
	if (mode === "curated30" || mode === "curated60") {
		if (!curated) return null;
		const missing = curated.filter((id) => !available.includes(id));
		if (missing.length) {
			ctx.ui.notify(
				`Curated suite tasks missing from ${available.length} discovered tasks: ${missing.join(", ")}`,
				"warning",
			);
			return null;
		}
		const size = mode === "curated30" ? Math.min(SUITE_SCREEN_SIZE, curated.length) : curated.length;
		return { ids: [...curated].slice(0, size).sort((a, b) => a.localeCompare(b)), includeAll: false };
	}
	if (mode === "one") {
		return available.length === 1
			? { ids: available, includeAll: false }
			: { ids: sampleTasks(available, 1), includeAll: false };
	}
	while (true) {
		const value = await ctx.ui.input(
			"How many tasks?",
			`Count from 1 to ${available.length} (tasks are picked at random; full set is ${available.length})`,
		);
		if (value === undefined) return null;
		const trimmed = value.trim();
		if (/^\d+$/.test(trimmed)) {
			const count = Number(trimmed);
			if (count >= 1 && count <= available.length) {
				return { ids: sampleTasks(available, count), includeAll: count === available.length };
			}
		}
		ctx.ui.notify(`Choose a count from 1 to ${available.length}.`, "warning");
	}
}

export async function collectWizard(
	args: string,
	ctx: ExtensionContext,
	prefill = "",
): Promise<{ answers: WizardAnswers; request: AuthorRequest } | null> {
	if (!ctx.hasUI) {
		throw new Error("Spec authoring requires an interactive Pi session");
	}

	const variantRequest = await ctx.ui.editor(
		"Step 1/6 - Which variants should run? " +
			"Accepted: a local extension path with its entry file, a pinned npm package, or a skill path. " +
			"The coding harness uses this data to search up the exact paths.",
		prefill,
	);
	if (variantRequest === undefined || !variantRequest.trim()) return null;

	const includeControl = await ctx.ui.select(
		"Step 2/6 - Control",
		["Include a control", "Exclude the control"],
	);
	if (includeControl === undefined) return null;
	const control: ControlMode = includeControl === "Include a control" ? "fresh" : "excluded";

	const scoped = ctx.scopedModels.map((item) => item.model);
	const candidates = scoped.length ? scoped : ctx.modelRegistry.getAvailable();
	const models = new Map<string, Model<Api>>();
	for (const model of candidates) models.set(`${model.provider}/${model.id}`, model);
	const currentId = ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : undefined;
	const modelIds = [...models.keys()].sort((a, b) => {
		if (a === currentId) return -1;
		if (b === currentId) return 1;
		return a.localeCompare(b);
	});
	if (!modelIds.length) throw new Error("Pi has no authenticated models available");
	const modelChoice = await ctx.ui.select("Step 3/6 - Model", modelIds);
	if (modelChoice === undefined) return null;
	const selectedModel = models.get(modelChoice) as Model<Api>;
	const thinkingOptions = supportedThinkingLevels(selectedModel);
	let thinking: string;
	if (thinkingOptions.length === 1) {
		thinking = thinkingOptions[0];
	} else {
		const chosen = await ctx.ui.select("Step 4/6 - Thinking mode", thinkingOptions);
		if (chosen === undefined) return null;
		thinking = chosen;
	}

	const bundled = await bundledDeepSwe();
	let curated: string[] | undefined;
	if (bundled) {
		const suiteChoice = await ctx.ui.select(
			"Step 5/6 - Test suite",
			Object.entries(bundled.suites).map(([id, suite]) => `${suite.label} (${id})`),
		);
		if (suiteChoice === undefined) return null;
		const suite = Object.values(bundled.suites).find((entry) => suiteChoice.startsWith(entry.label));
		if (!suite) throw new Error(`Unknown test suite: ${suiteChoice}`);
		curated = [...suite.signal, ...suite.confirmation];
	}

	const taskModeChoice = await ctx.ui.select(
		"Step 6/6 - How many tasks?",
		bundled
			? [
				"1 task (random)",
				`${SUITE_SCREEN_SIZE} tasks (curated)`,
				`${curated?.length} tasks (curated)`,
				"Full task set",
				"Custom count (random)",
			]
			: ["1 task", "Full task set", "Custom count"],
	);
	if (taskModeChoice === undefined) return null;
	const taskMode: TaskMode = taskModeChoice === "1 task" || taskModeChoice === "1 task (random)"
		? "one"
		: taskModeChoice === "Full task set" ? "full"
		: taskModeChoice === `${SUITE_SCREEN_SIZE} tasks (curated)` ? "curated30"
		: taskModeChoice?.endsWith("tasks (curated)") ? "curated60"
		: "custom";
	const discovered = await discoverTaskRoot(ctx, args);
	const taskSelection = await chooseTasks(ctx, taskMode, discovered.ids, curated);
	if (!taskSelection) return null;

	const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
	const experimentName = `roast-${stamp.toLowerCase()}`;
	const outputDir = join(ctx.cwd, ".pi-files", "roastmyharness");
	await mkdir(outputDir, { recursive: true });
	const specPath = outputPathFor(ctx, join(outputDir, `${experimentName}.toml`));
	const answers: WizardAnswers = {
		variantRequest: variantRequest.trim(),
		control,
		modelProvider: selectedModel.provider,
		modelId: selectedModel.id,
		thinking,
		taskRoot: discovered.root,
		taskIds: taskSelection.ids,
		includeAllTasks: taskSelection.includeAll,
		experimentName,
	};
	const request: AuthorRequest = {
		output_path: specPath,
		experiment: {
			name: experimentName,
			pi_version: DEFAULT_PI_VERSION,
			thinking,
			model: { provider: selectedModel.provider, id: selectedModel.id },
			tasks: {
				path: discovered.root,
				include: taskSelection.includeAll ? ["*"] : taskSelection.ids,
				exclude: [],
			},
			control,
			variant_request: variantRequest.trim(),
		},
		discovered_local_pi_packages: await localPiPackages(ctx.cwd),
	};
	return { answers, request };
}

export interface AuthorOutcome {
	prepared: RoastResponse;
	request: AuthorRequest;
	spec_text: string;
	details: AuthorDetails;
	usage: Usage;
}
