import { homedir } from "node:os";
import { join, resolve } from "node:path";

export type ControlMode = "fresh" | "excluded" | "historic";

export interface WizardModel {
	provider: string;
	id: string;
	name: string;
	reasoning: boolean;
	thinkingLevelMap?: Record<string, string | null | undefined>;
}

export interface WizardSuites {
	luna_signal: string[];
	luna_confirmation: string[];
	glm_signal: string[];
	glm_confirmation: string[];
}

export interface WizardContextData {
	task_root: string;
	discovered: string[];
	suites: WizardSuites;
	historic: { count: number; task_ids: string[]; runs: Record<string, number> };
}

export interface WizardAnswers {
	variantRequest: string;
	modelProvider: string;
	modelId: string;
	thinking: string;
	control: ControlMode;
	taskRoot: string;
	taskIds: string[];
	taskLabel: string;
	experimentName: string;
	repetitions: number;
	historicCount: number;
}

const THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

const RECOMMENDATIONS = [
	{ key: "luna", thinking: "high", needles: ["luna"], prefer: undefined as string | undefined },
	{ key: "spark", thinking: "high", needles: ["spark"], prefer: "1.3" },
	{ key: "glm-flash", thinking: "max", needles: ["glm", "flash"], prefer: "5.3" },
] as const;

export function fullId(model: Pick<WizardModel, "provider" | "id">): string {
	return `${model.provider}/${model.id}`;
}

function haystack(model: WizardModel): string {
	return `${model.provider}/${model.id} ${model.name}`.toLowerCase();
}

export function matchRecommended(
	models: WizardModel[],
	needles: readonly string[],
	prefer?: string,
): WizardModel | undefined {
	const hits = models.filter((m) => needles.every((n) => haystack(m).includes(n)));
	if (!hits.length) return undefined;
	if (prefer) {
		const preferred = hits.filter((m) => haystack(m).includes(prefer));
		if (preferred.length) return preferred[0];
	}
	return hits[0];
}

export function orderModels(
	models: WizardModel[],
	currentId?: string,
): { model: WizardModel; recommended: boolean }[] {
	const pinned: WizardModel[] = [];
	for (const rec of RECOMMENDATIONS) {
		const hit = matchRecommended(
			models.filter((m) => !pinned.includes(m)),
			rec.needles,
			rec.prefer,
		);
		if (hit) pinned.push(hit);
	}
	const rest = models
		.filter((m) => !pinned.includes(m))
		.sort((a, b) => {
			if (fullId(a) === currentId) return -1;
			if (fullId(b) === currentId) return 1;
			return fullId(a).localeCompare(fullId(b));
		});
	return [
		...pinned.map((model) => ({ model, recommended: true })),
		...rest.map((model) => ({ model, recommended: false })),
	];
}

export function recommendedThinkingFor(model: WizardModel): string | undefined {
	const rec = RECOMMENDATIONS.find((r) =>
		r.needles.every((n) => haystack(model).includes(n)),
	);
	return rec?.thinking;
}

export function supportedThinkingLevels(model: WizardModel): string[] {
	const map = model.thinkingLevelMap;
	if (map) {
		const supported = THINKING_LEVELS.filter(
			(level) => map[level] !== null && map[level] !== undefined,
		);
		if (supported.length) return supported;
	}
	return model.reasoning ? [...THINKING_LEVELS] : ["off"];
}

export function orderThinkingLevels(levels: string[], recommended?: string): string[] {
	if (recommended && levels.includes(recommended)) {
		return [recommended, ...levels.filter((l) => l !== recommended)];
	}
	return levels;
}

export function controlOptions(
	historicCount: number,
): { label: string; value: ControlMode }[] {
	const options = [{ label: "Fresh control", value: "fresh" as ControlMode }];
	if (historicCount > 0) {
		options.push({
			label: `Historic control (${historicCount} tasks)`,
			value: "historic",
		});
	}
	options.push({ label: "No control", value: "excluded" });
	return options;
}

export type TaskChoice =
	| "smoke"
	| "luna30"
	| "luna60"
	| "glm30"
	| "glm60"
	| "full"
	| "historic"
	| "custom";

export function standardTaskOptions(
	data: WizardContextData,
): { label: string; value: TaskChoice }[] {
	const { discovered, suites } = data;
	const luna60 = [...suites.luna_signal, ...suites.luna_confirmation];
	const glm60 = [...suites.glm_signal, ...suites.glm_confirmation];
	const options: { label: string; value: TaskChoice }[] = [];
	if (discovered.length) options.push({ label: "1 (smoke test)", value: "smoke" });
	if (suites.luna_signal.length) {
		options.push({ label: `Luna curated 30 (${suites.luna_signal.length} tasks)`, value: "luna30" });
	}
	if (luna60.length) {
		options.push({ label: `Luna curated 60 (${luna60.length} tasks)`, value: "luna60" });
	}
	if (suites.glm_signal.length) {
		options.push({ label: `GLM curated 30 (${suites.glm_signal.length} tasks)`, value: "glm30" });
	}
	if (glm60.length) {
		options.push({ label: `GLM curated 60 (${glm60.length} tasks)`, value: "glm60" });
	}
	if (discovered.length) {
		options.push({ label: `Full suite (${discovered.length} tasks)`, value: "full" });
	}
	options.push({ label: "Custom", value: "custom" });
	return options;
}

export function historicTaskOptions(
	data: WizardContextData,
): { label: string; value: TaskChoice }[] {
	return [
		{ label: `Historic tasks (${data.historic.count})`, value: "historic" },
		{ label: "Custom", value: "custom" },
	];
}

export function sampleTasks(pool: string[], count: number): string[] {
	const remaining = [...pool];
	const picked: string[] = [];
	while (picked.length < count && remaining.length) {
		const index = Math.floor(Math.random() * remaining.length);
		picked.push(remaining.splice(index, 1)[0]);
	}
	return picked.sort((a, b) => a.localeCompare(b));
}

function isGlm(modelId: string): boolean {
	return modelId.toLowerCase().includes("glm");
}

export function resolveStandardTasks(
	choice: Exclude<TaskChoice, "historic" | "custom">,
	data: WizardContextData,
): string[] {
	switch (choice) {
		case "smoke":
			return sampleTasks(data.discovered, 1);
		case "luna30":
			return [...data.suites.luna_signal];
		case "luna60":
			return [...new Set([...data.suites.luna_signal, ...data.suites.luna_confirmation])];
		case "glm30":
			return [...data.suites.glm_signal];
		case "glm60":
			return [...new Set([...data.suites.glm_signal, ...data.suites.glm_confirmation])];
		case "full":
			return [...data.discovered];
	}
}

export function resolveHistoricTasks(
	count: number,
	data: WizardContextData,
	modelId: string,
): string[] {
	const pool = data.historic.task_ids;
	if (count <= pool.length) return sampleTasks(pool, count);
	const selected = [...pool];
	const family = isGlm(modelId)
		? [...data.suites.glm_signal, ...data.suites.glm_confirmation]
		: [...data.suites.luna_signal, ...data.suites.luna_confirmation];
	for (const id of family) {
		if (selected.length >= count) break;
		if (!selected.includes(id)) selected.push(id);
	}
	for (const id of data.discovered) {
		if (selected.length >= count) break;
		if (!selected.includes(id)) selected.push(id);
	}
	return selected;
}

export function taskLabel(choice: TaskChoice, ids: string[]): string {
	const names: Record<TaskChoice, string> = {
		smoke: "smoke",
		luna30: "luna-30",
		luna60: "luna-60",
		glm30: "glm-30",
		glm60: "glm-60",
		full: "full",
		historic: "historic",
		custom: "custom",
	};
	return `${names[choice]} (${ids.length} tasks)`;
}

export function expandPath(value: string, cwd: string): string {
	const trimmed = value.trim();
	if (trimmed === "~") return homedir();
	if (trimmed.startsWith("~/")) return join(homedir(), trimmed.slice(2));
	return resolve(cwd, trimmed);
}

export function detectGitHubUrl(text: string): string | null {
	const match = text.match(/https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?(\.git)?(?=[\s"'`,]|$)/);
	return match ? match[0] : null;
}

