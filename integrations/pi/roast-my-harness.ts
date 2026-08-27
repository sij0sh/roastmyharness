










import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";

const ROAST_ACTIONS = ["prepare", "start", "status", "cancel", "report"] as const;
type RoastAction = (typeof ROAST_ACTIONS)[number];


interface RoastResponse {
	ok?: boolean;
	state?: string;
	plan_id?: string;
	spec_path?: string;
	experiment_id?: string;
	experiment?: {
		tasks: number;
		arms: number;
		trials: number;
		max_parallel: number;
		model: string;
	};
	warnings?: string[];
	next_action?: string;
	questions?: Array<{ field: string; message: string; choices: string[] }>;
	[key: string]: unknown;
}

function roastBinary(): string {
	return process.env.ROAST_MY_HARNESS_BIN || "roast-my-harness";
}

function buildArgs(params: {
	action: RoastAction;
	spec_path?: string;
	plan_id?: string;
	experiment_id?: string;
	skip_docker?: boolean;
}): string[] {
	const argv = ["tool", params.action];
	switch (params.action) {
		case "prepare":
			argv.push(params.spec_path ?? "");
			break;
		case "start":
			argv.push(params.plan_id ?? "");
			break;
		case "status":
		case "cancel":
		case "report":
			argv.push(params.experiment_id ?? "");
			break;
	}
	if (params.skip_docker) argv.push("--skip-docker");
	return argv;
}

function summarize(r: RoastResponse): string {
	if (r.state === "needs_input") {
		const qs = (r.questions ?? [])
			.map((q) => `  - ${q.field}: ${q.message}`)
			.join("\n");
		return `needs_input:\n${qs}`;
	}
	if (r.state === "ready_for_confirmation") {
		const e = r.experiment;
		return `ready_for_confirmation plan=${r.plan_id}: ${e?.trials ?? "?"} trials ` +
			`(${e?.tasks ?? "?"} tasks x ${e?.arms ?? "?"} arms), max_parallel=${e?.max_parallel ?? "?"}, ` +
			`model=${e?.model ?? "?"}` +
			(r.warnings?.length ? `; warnings: ${r.warnings.join("; ")}` : "");
	}
	const parts = [`state=${r.state ?? "unknown"}`];
	if (r.experiment_id) parts.push(`experiment=${r.experiment_id}`);
	if (r.started !== undefined) parts.push(`started=${r.started}`);
	if (r.state === "COMPLETE") parts.push("final=true");
	return parts.join(" ");
}

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "roast_harness",
		label: "RoastMyHarness",
		description:
			"Run harness-comparison experiments via the roast-my-harness service. " +
			"prepare validates an experiment TOML and returns a plan for user approval; " +
			"start launches an approved plan_id in the background; status polls an " +
			"experiment; cancel requests graceful cancellation; report regenerates artifacts.",
		promptSnippet:
			"Prepare, launch, monitor, and report harness-comparison experiments via the roast-my-harness service",
		promptGuidelines: [
			"Use roast_harness instead of shell commands when comparing harness configurations: prepare, then ask the user to approve the plan, then start with the returned plan_id.",
			"Read roast_harness results as JSON; when prepare returns needs_input, resolve the listed questions instead of guessing.",
		],
		parameters: Type.Object({
			action: StringEnum(ROAST_ACTIONS, {
				description: "Orchestration action to perform.",
			}),
			spec_path: Type.Optional(
				Type.String({ description: "Experiment TOML path (required for prepare)." }),
			),
			plan_id: Type.Optional(
				Type.String({ description: "Plan id from prepare (required for start)." }),
			),
			experiment_id: Type.Optional(
				Type.String({
					description: "Experiment id (required for status, cancel, report).",
				}),
			),
			skip_docker: Type.Optional(
				Type.Boolean({ description: "Skip docker preflight checks." }),
			),
		}),
		executionMode: "sequential",
		async execute(toolCallId, params, signal, onUpdate, ctx) {
			void toolCallId;
			
			if (params.action === "prepare" && !params.spec_path) {
				return { content: [{ type: "text", text: "spec_path is required for prepare" }], isError: true };
			}
			if (params.action === "start" && !params.plan_id) {
				return { content: [{ type: "text", text: "plan_id is required for start" }], isError: true };
			}
			if (["status", "cancel", "report"].includes(params.action) && !params.experiment_id) {
				return {
					content: [{ type: "text", text: `experiment_id is required for ${params.action}` }],
					isError: true,
				};
			}

			
			
			
			if (params.action === "start" && ctx.hasUI) {
				const ok = await ctx.ui.confirm(
					"RoastMyHarness",
					`Launch experiment plan ${params.plan_id}?`,
				);
				if (!ok) {
					return {
						content: [{ type: "text", text: "launch cancelled by user" }],
					};
				}
			}

			const argv = buildArgs(params);
			onUpdate?.({
				content: [{ type: "text", text: `running: ${roastBinary()} ${argv.join(" ")}` }],
			});
			let result;
			try {
				result = await pi.exec(roastBinary(), argv, { signal, timeout: 120_000 });
			} catch (error) {
				return {
					content: [
						{
							type: "text",
							text: `failed to run ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
						},
					],
					isError: true,
				};
			}

			const stdout = result.stdout.trim();
			let parsed: RoastResponse | null = null;
			if (stdout) {
				try {
					parsed = JSON.parse(stdout) as RoastResponse;
				} catch {
					
				}
			}
			if (result.code !== 0 && parsed?.error) {
				const err = parsed.error as { code?: string; message?: string };
				return {
					content: [
						{ type: "text", text: `error ${err.code ?? "unknown"}: ${err.message ?? stdout}` },
					],
					isError: true,
				};
			}
			if (result.code !== 0 && !parsed) {
				const text = (result.stderr.trim() || stdout || `exit code ${result.code}`).slice(0, 4000);
				return { content: [{ type: "text", text }], isError: true };
			}
			
			const text = parsed
				? summarize(parsed)
				: (stdout || "ok").slice(0, 4000);
			return {
				content: [{ type: "text", text }],
				details: parsed ?? {},
			};
		},
	});
}
