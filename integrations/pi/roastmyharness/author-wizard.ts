import { mkdir, readFile, readdir, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { extname, join, relative, resolve } from "node:path";
import type { Api, Model, Usage } from "@earendil-works/pi-ai";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
	DEFAULT_PI_VERSION,
	type AuthorDetails,
	type CatalogResponse,
	type EvalType,
	type ExecHost,
	type RoastResponse,
} from "./core.ts";
import {
	detectGitHubUrl,
	discoverTaskIds,
	expandPath,
	fetchCatalog,
	localPiPackages,
	stageGitHubSource,
	supportedThinkingLevels,
	type AuthorRequest,
	type ControlMode,
	type HistoryScope,
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

async function choosePreset(
	ctx: ExtensionContext,
	catalog: CatalogResponse | null,
	discovered: string[],
): Promise<{ id: string | null; label: string | null; pool: string[] } | null> {
	const presets = catalog?.presets ?? [];
	if (!presets.length) return { id: null, label: null, pool: discovered };
	const options = [
		`Full task set (${discovered.length} tasks, no preset)`,
		...presets.map((preset) => `${preset.label} (preset ${preset.id})`),
	];
	const choice = await ctx.ui.select("Step 2/8 - Task preset", options);
	if (choice === undefined) return null;
	if (choice.startsWith("Full task set")) return { id: null, label: null, pool: discovered };
	const preset = presets.find((entry) => choice.endsWith(`(preset ${entry.id})`));
	if (!preset) throw new Error(`Unknown task preset: ${choice}`);
	const missing = preset.tasks.filter((id) => !discovered.includes(id));
	if (missing.length) {
		ctx.ui.notify(
			`Preset ${preset.id} tasks missing from ${discovered.length} discovered tasks: ${missing.join(", ")}`,
			"warning",
		);
	}
	const pool = preset.tasks.filter((id) => discovered.includes(id));
	if (!pool.length) {
		ctx.ui.notify(
			`Preset ${preset.id} has no tasks on disk; pick another preset or pass a task path.`,
			"warning",
		);
		return null;
	}
	return { id: preset.id, label: preset.label, pool };
}

function externalEvalId(taskRoot: string): string {
	const base = taskRoot.split(/[\\/]/).filter(Boolean).pop() ?? "external";
	const slug = base.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
	return slug || "external";
}

/**
 * Benchmark-mode step: Recommended (bundled DeepSWE) / Custom (frozen
 * generated eval beside the task root) / Existing (plain external task
 * set). The eval descriptor beside the task root decides which modes
 * are offered; the choice freezes into the spec's [evaluation] block.
 */
async function chooseEvaluation(
	ctx: ExtensionContext,
	catalog: CatalogResponse | null,
	taskRoot: string,
	discovered: string[],
): Promise<{
	type: EvalType;
	id: string | null;
	revision: string | null;
	preset: { id: string | null; label: string | null; pool: string[] };
} | null> {
	const evalInfo = catalog?.eval ?? null;
	const hasBundled = Boolean(catalog?.benchmark);
	const options: string[] = [];
	if (evalInfo) {
		options.push(`Custom evaluation ${evalInfo.id} (frozen contract, ${discovered.length} tasks)`);
	}
	if (hasBundled) {
		options.push(`Recommended benchmark (${catalog?.benchmark}, ${discovered.length} tasks)`);
	}
	options.push(`Existing task set (${discovered.length} tasks, no frozen contract)`);
	if (options.length === 1) {
		return {
			type: "external",
			id: externalEvalId(taskRoot),
			revision: null,
			preset: { id: null, label: null, pool: discovered },
		};
	}
	const choice = await ctx.ui.select("Step 2/8 - Benchmark", options);
	if (choice === undefined) return null;
	if (choice.startsWith("Custom evaluation") && evalInfo) {
		return {
			type: "generated",
			id: evalInfo.id,
			revision: evalInfo.revision,
			preset: { id: null, label: null, pool: discovered },
		};
	}
	if (choice.startsWith("Recommended benchmark")) {
		const preset = await choosePreset(ctx, catalog, discovered);
		if (!preset) return null;
		return { type: "bundled", id: "deepswe", revision: null, preset };
	}
	return {
		type: "external",
		id: externalEvalId(taskRoot),
		revision: null,
		preset: { id: null, label: null, pool: discovered },
	};
}

async function chooseTasks(
	ctx: ExtensionContext,
	mode: TaskMode,
	available: string[],
): Promise<{ ids: string[]; includeAll: boolean } | null> {
	if (mode === "full") return { ids: available, includeAll: true };
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
	host: ExecHost,
	args: string,
	ctx: ExtensionContext,
	prefill = "",
): Promise<{ answers: WizardAnswers; request: AuthorRequest; catalog: CatalogResponse | null } | null> {
	if (!ctx.hasUI) {
		throw new Error("Spec authoring requires an interactive Pi session");
	}

	const variantAnswer = await ctx.ui.editor(
		"Step 1/8 - Which variants should run? " +
			"Accepted: a local extension path with its entry file, a pinned npm package, a skill path, " +
			"a GitHub repo URL (cloned to a staged local path for you), or a tool restriction such as bash only. " +
			"The coding harness uses this data to search up the exact paths.",
		prefill,
	);
	if (variantAnswer === undefined || !variantAnswer.trim()) return null;
	let variantRequest = variantAnswer.trim();
	let stagedSources: AuthorRequest["staged_sources"] = undefined;
	if (detectGitHubUrl(variantRequest)) {
		try {
			const staged = await stageGitHubSource(host, variantRequest);
			stagedSources = [staged];
			variantRequest = `${variantRequest}\nStaged GitHub source: ${staged.url} @ ${staged.rev.slice(0, 12)} available at local path ${staged.staged_path}. Use this absolute local path in the spec; record the URL plus short SHA in the hypothesis.`;
		} catch (error) {
			ctx.ui.notify(`GitHub staging failed, continuing with the raw request: ${error instanceof Error ? error.message : String(error)}`, "warning");
		}
	}

	const discovered = await discoverTaskRoot(ctx, args);
	const catalog = await fetchCatalog(host, discovered.root);
	const evaluation = await chooseEvaluation(ctx, catalog, discovered.root, discovered.ids);
	if (!evaluation) return null;
	const preset = evaluation.preset;

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
	const calibrated = new Map<string, { rate: string; samples: string; distance: number; thinking: string | null }>();
	for (const profile of catalog?.profiles ?? []) {
		if (!profile.full_id || profile.expected_rate === null || calibrated.has(profile.full_id)) continue;
		calibrated.set(profile.full_id, {
			rate: `${(profile.expected_rate * 100).toFixed(1)}%`,
			samples: profile.samples !== null ? String(profile.samples) : "?",
			distance: profile.distance ?? 1,
			thinking: profile.thinking,
		});
	}
	const recommended = [...calibrated.entries()].sort((a, b) => a[1].distance - b[1].distance)[0]?.[0];
	const modelOptions = modelIds.map((id) => {
		const entry = calibrated.get(id);
		if (!entry) return id;
		const star = id === recommended ? " ★ recommended" : "";
		return `${id} · calibrated ${entry.rate} over ${entry.samples} runs${star}`;
	});
	const modelChoice = await ctx.ui.select("Step 3/8 - Model", modelOptions);
	if (modelChoice === undefined) return null;
	const modelId = modelIds.find((id) => modelChoice === id || modelChoice.startsWith(`${id} ·`));
	if (!modelId) throw new Error(`Unknown model: ${modelChoice}`);
	const selectedModel = models.get(modelId) as Model<Api>;
	const matchedProfile = calibrated.get(modelId);
	if (!matchedProfile) {
		ctx.ui.notify("No calibration data for this model — this run sets the baseline.", "info");
	}
	const thinkingOptions = supportedThinkingLevels(selectedModel);
	let thinking: string;
	if (thinkingOptions.length === 1) {
		thinking = thinkingOptions[0];
	} else {
		const profileThinking = matchedProfile?.thinking;
		const ordered = profileThinking && thinkingOptions.includes(profileThinking)
			? [profileThinking, ...thinkingOptions.filter((level) => level !== profileThinking)]
			: thinkingOptions;
		const options = ordered.map((level) =>
			level === profileThinking && level === ordered[0] ? `${level} (profile default)` : level);
		const chosen = await ctx.ui.select("Step 4/8 - Thinking mode", options);
		if (chosen === undefined) return null;
		thinking = chosen.endsWith(" (profile default)")
			? chosen.slice(0, -" (profile default)".length)
			: chosen;
	}

	const repChoice = await ctx.ui.select("Step 5/8 - Repetitions", [
		"1 (single run)",
		"2 repetitions",
		"3 repetitions",
		"4 repetitions",
	]);
	if (repChoice === undefined) return null;
	const repetitions = Number(repChoice.slice(0, 1));

	const controlChoice = await ctx.ui.select(
		"Step 6/8 - Control",
		["Include a fresh control", "Reuse historic controls", "Exclude the control"],
	);
	if (controlChoice === undefined) return null;
	const control: ControlMode = controlChoice === "Include a fresh control"
		? "fresh"
		: controlChoice === "Reuse historic controls"
			? "historic"
			: "excluded";
	let historyScope: HistoryScope = "hybrid";
	let sentinelTasks = 4;
	if (control === "historic") {
		const scopeChoice = await ctx.ui.select(
			"History scope",
			["hybrid (recommended)", "intersection (strict)"],
		);
		if (scopeChoice === undefined) return null;
		historyScope = scopeChoice.startsWith("intersection") ? "intersection" : "hybrid";
		while (true) {
			const value = await ctx.ui.input(
				"How many fresh sentinel tasks?",
				"Count 0 or more (default 4)",
			);
			if (value === undefined) return null;
			const trimmed = value.trim() || "4";
			if (/^\d+$/.test(trimmed)) {
				sentinelTasks = Number(trimmed);
				break;
			}
			ctx.ui.notify("Choose a count of 0 or more.", "warning");
		}
	}

	const taskModeChoice = await ctx.ui.select(
		"Step 7/8 - How many tasks?",
		[
			"1 task (random)",
			`Full pool (${preset.pool.length} tasks)`,
			"Custom count (random)",
		],
	);
	if (taskModeChoice === undefined) return null;
	const taskMode: TaskMode = taskModeChoice === "1 task (random)"
		? "one"
		: taskModeChoice.startsWith("Full pool") ? "full" : "custom";
	const taskSelection = await chooseTasks(ctx, taskMode, preset.pool);
	if (!taskSelection) return null;

	const hypothesisAnswer = await ctx.ui.input(
		"Hypothesis (optional)",
		"One falsifiable sentence, frozen into the spec; blank lets the author draft it",
	);
	if (hypothesisAnswer === undefined) return null;
	const hypothesis = hypothesisAnswer.trim();

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
		hypothesis,
		repetitions,
		preset: preset.id,
		presetLabel: preset.label,
		evalType: evaluation.type,
		evalId: evaluation.id,
		evalRevision: evaluation.revision,
		historyScope,
		sentinelTasks,
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
				...(preset.id ? { preset: preset.id } : {}),
			},
			evaluation: {
				type: evaluation.type,
				...(evaluation.id ? { id: evaluation.id } : {}),
				...(evaluation.revision ? { revision: evaluation.revision } : {}),
			},
			control,
			...(control === "historic"
				? { history_scope: historyScope, sentinel_tasks: sentinelTasks }
				: {}),
			execution: { repetitions },
			variant_request: variantRequest.trim(),
		},
		discovered_local_pi_packages: await localPiPackages(ctx.cwd),
		...(stagedSources ? { staged_sources: stagedSources } : {}),
		...(hypothesis ? { hypothesis } : {}),
	};
	return { answers, request, catalog };
}

export interface AuthorOutcome {
	prepared: RoastResponse;
	request: AuthorRequest;
	spec_text: string;
	details: AuthorDetails;
	usage: Usage;
}
