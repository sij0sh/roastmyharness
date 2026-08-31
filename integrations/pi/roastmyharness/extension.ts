import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";
import {
	AUTHOR_CHILD_ENV,
	DEFAULT_RECENT_TRIALS,
	ROAST_ACTIONS,
	WATCH_INTERVAL_SEC,
	buildArgs,
	roastBinary,
	summarize,
	type AuthorDetails,
	type RoastDetails,
	type RoastResponse,
	type ServiceAction,
	type WatchDetails,
} from "./core.ts";
import {
	WIDGET_ID,
	authorExperiment,
	renderAuthorResult,
	runCommandFlow,
} from "./author-flow.ts";
import {
	renderWatchResult,
	streamStartedExperiment,
	streamWatch,
	type WatchParams,
} from "./watch.ts";

const TOOL_NAME = "roast_harness";

export default function (pi: ExtensionAPI) {
	if (process.env[AUTHOR_CHILD_ENV] === "1") return;
	let wizardRunning = false;
	let toolUsedThisTurn = false;

	const showTool = () => {
		const active = pi.getActiveTools();
		if (!active.includes(TOOL_NAME)) pi.setActiveTools([...active, TOOL_NAME]);
	};
	const hideTool = () => {
		const active = pi.getActiveTools();
		if (active.includes(TOOL_NAME)) {
			pi.setActiveTools(active.filter((name) => name !== TOOL_NAME));
		}
	};

	pi.on("session_start", () => hideTool());
	pi.on("agent_settled", () => {
		if (toolUsedThisTurn) {
			toolUsedThisTurn = false;
			return;
		}
		hideTool();
	});

	const launchWizard = async (args: string, ctx: ExtensionContext) => {
		if (!ctx.hasUI) {
			ctx.ui.notify("/roastmyharness requires an interactive Pi session.", "error");
			return;
		}
		if (!ctx.isIdle()) {
			ctx.ui.notify("Wait for the current agent turn to finish.", "warning");
			return;
		}
		if (wizardRunning) {
			ctx.ui.notify("The RoastMyHarness wizard is already open.", "warning");
			return;
		}
		wizardRunning = true;
		showTool();
		try {
			await runCommandFlow(pi, args, ctx);
		} catch (error) {
			ctx.ui.setStatus(WIDGET_ID, undefined);
			ctx.ui.setWidget(WIDGET_ID, undefined);
			ctx.ui.notify(
				`RoastMyHarness failed: ${error instanceof Error ? error.message : String(error)}`,
				"error",
			);
		} finally {
			wizardRunning = false;
		}
	};
	const command = {
		description: "Configure, validate, and launch a harness comparison",
		handler: launchWizard,
	};
	pi.registerCommand("roastmyharness", command);

	pi.registerTool({
		name: "roast_harness",
		label: "RoastMyHarness",
		description:
			"Configure and run harness-comparison experiments. author opens the wizard, uses an isolated " +
			"Pi context to create the spec, and validates it; prepare validates an existing TOML file; " +
			"start launches an approved plan_id and streams live progress until completion " +
			"(watch=false returns after launch); watch attaches to a running experiment; status polls once; " +
			"cancel requests graceful cancellation; report regenerates artifacts.",
		promptSnippet:
			"Author, validate, launch, monitor, and report harness-comparison experiments",
		promptGuidelines: [
			"The /roastmyharness command runs its own wizard with no model call; roast_harness author is only for model-initiated authoring.",
			"After roast_harness author or prepare returns ready_for_confirmation, present the plan and wait for explicit user approval before calling start.",
			"roast_harness start streams live progress until the experiment finishes; aborting only detaches the watch. Use cancel to stop a run.",
			"Read roast_harness results as JSON. When validation returns needs_input, resolve listed questions instead of guessing.",
		],
		parameters: Type.Object({
			action: StringEnum(ROAST_ACTIONS, {
				description: "Orchestration action to perform.",
			}),
			task_root: Type.Optional(
				Type.String({ description: "Task dataset path hint for the author wizard." }),
			),
			spec_path: Type.Optional(
				Type.String({ description: "Experiment TOML path (required for prepare)." }),
			),
			plan_id: Type.Optional(
				Type.String({ description: "Plan id from prepare (required for start)." }),
			),
			experiment_id: Type.Optional(
				Type.String({
					description: "Experiment id (required for status, watch, cancel, report).",
				}),
			),
			watch: Type.Optional(
				Type.Boolean({
					description:
						"After start: stream live progress until final. Default true; set false to return immediately after launch.",
				}),
			),
			interval_sec: Type.Optional(
				Type.Number({
					description: `Watch poll interval in seconds (default ${WATCH_INTERVAL_SEC}).`,
				}),
			),
			recent: Type.Optional(
				Type.Number({
					description: `Trial events kept for display (default ${DEFAULT_RECENT_TRIALS}).`,
				}),
			),
			skip_docker: Type.Optional(
				Type.Boolean({ description: "Skip docker preflight checks." }),
			),
		}),
		executionMode: "sequential",
		async execute(toolCallId, params, signal, onUpdate, ctx) {
			void toolCallId;
			toolUsedThisTurn = true;

			if (params.action === "author") {
				if (wizardRunning) throw new Error("The RoastMyHarness wizard is already open");
				wizardRunning = true;
				try {
					return await authorExperiment(
						pi, params.task_root ?? "", ctx, signal, onUpdate, params.skip_docker ?? false,
					);
				} finally {
					wizardRunning = false;
				}
			}
			if (params.action === "prepare" && !params.spec_path) {
				throw new Error("spec_path is required for prepare");
			}
			if (params.action === "start" && !params.plan_id) {
				throw new Error("plan_id is required for start");
			}
			if (["status", "watch", "cancel", "report"].includes(params.action) && !params.experiment_id) {
				throw new Error(`experiment_id is required for ${params.action}`);
			}

			if (params.action === "start" && ctx.hasUI) {
				const ok = await ctx.ui.confirm(
					"RoastMyHarness",
					`Launch experiment plan ${params.plan_id}?`,
				);
				if (!ok) {
					return {
						content: [{ type: "text", text: "launch cancelled by user" }],
						details: {},
					};
				}
			}

			if (params.interval_sec !== undefined &&
				(!Number.isFinite(params.interval_sec) || params.interval_sec < 0.2)) {
				throw new Error("interval_sec must be a finite number at least 0.2");
			}
			if (params.recent !== undefined &&
				(!Number.isFinite(params.recent) || params.recent < 1)) {
				throw new Error("recent must be a finite number at least 1");
			}
			const wantsWatch =
				params.action === "watch" ||
				(params.action === "start" && params.watch !== false);
			const watchParams: WatchParams = {
				interval_sec: params.interval_sec,
				recent: params.recent,
			};

			if (params.action === "watch") {
				return await streamWatch(params.experiment_id as string, watchParams, signal, onUpdate);
			}

			const argv = buildArgs({ ...params, action: params.action as ServiceAction });
			onUpdate?.({
				content: [{ type: "text", text: `running: ${roastBinary()} ${argv.join(" ")}` }],
				details: {},
			});
			let result;
			try {
				result = await pi.exec(roastBinary(), argv, { signal, timeout: 120_000 });
			} catch (error) {
				throw new Error(
					`failed to run ${roastBinary()}: ${error instanceof Error ? error.message : String(error)}`,
				);
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
				const err = parsed.error;
				throw new Error(`error ${err.code ?? "unknown"}: ${err.message ?? stdout}`);
			}
			if (result.code !== 0 && !parsed) {
				const text = (result.stderr.trim() || stdout || `exit code ${result.code}`).slice(0, 4000);
				throw new Error(text);
			}

			if (wantsWatch && parsed?.experiment_id) {
				return await streamStartedExperiment(parsed.experiment_id, watchParams, signal, onUpdate);
			}

			const text = parsed
				? summarize(parsed)
				: (stdout || "ok").slice(0, 4000);
			return {
				content: [{ type: "text", text }],
				details: parsed ?? {},
			};
		},

		renderCall(args, theme, _context) {
			let text = theme.fg("toolTitle", theme.bold("roast_harness ")) +
				theme.fg("accent", args.action ?? "?");
			if (args.task_root) text += theme.fg("dim", ` ${args.task_root}`);
			if (args.spec_path) text += theme.fg("dim", ` ${args.spec_path}`);
			if (args.plan_id) text += theme.fg("dim", ` ${args.plan_id}`);
			if (args.experiment_id) text += theme.fg("dim", ` ${args.experiment_id}`);
			if (args.action === "start" && args.watch === false) {
				text += theme.fg("muted", " (no watch)");
			}
			return new Text(text, 0, 0);
		},

		renderResult(result, { expanded, isPartial }, theme, _context) {
			const details = result.details as RoastDetails | undefined;
			if (details && (details as AuthorDetails).kind === "author") {
				return renderAuthorResult(details as AuthorDetails, { expanded, isPartial }, theme);
			}
			if (details && "stream" in details && details.stream === true) {
				return renderWatchResult(details as WatchDetails, { expanded }, theme);
			}
			const part = result.content.find((c) => c.type === "text");
			return new Text(part && "text" in part ? part.text : "(no output)", 0, 0);
		},
	});
}
