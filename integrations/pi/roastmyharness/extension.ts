import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
	SUBMIT_TOOL,
	bridgeArgs,
	runBridgeJson,
	summarize,
	type RoastResponse,
} from "./core.ts";
import { RUN_CARD_TYPE, postRunCard } from "./cards.ts";
import { renderRunCard, streamBridgeRun } from "./watch.ts";

const WIDGET_ID = "roastmyharness-widget";

const SPEC_TEMPLATE = (facts: WizardFacts) => `schema_version = 3
name = "${facts.name}"

model = "${facts.model}"
thinking = "${facts.thinking}"
pi_version = "${facts.piVersion}"

[tasks]
path = "${facts.taskRoot}"
include = ["*"]

[[variants]]
id = "${facts.variantId}"

[[variants.extensions]]
path = "${facts.extensionPath}"
entry = "${facts.extensionEntry}"
`;

interface WizardFacts {
	name: string;
	model: string;
	thinking: string;
	piVersion: string;
	taskRoot: string;
	variantId: string;
	extensionPath: string;
	extensionEntry: string;
}

async function collectFacts(ctx: ExtensionContext): Promise<WizardFacts | undefined> {
	const ask = async (prompt: string, initial = ""): Promise<string | undefined> => {
		try {
			return await ctx.ui.input(prompt, initial);
		} catch {
			return undefined;
		}
	};
	const name = (await ask("Experiment name?", "test-my-extension"))?.trim();
	if (!name) return undefined;
	const taskRoot = (await ask("Benchmark task root?", "tasks/deepswe/tasks"))?.trim();
	if (!taskRoot) return undefined;
	const extensionPath = (await ask("Extension path under test?", "../my-extension"))?.trim();
	if (!extensionPath) return undefined;
	const extensionEntry = (await ask("Extension entry?", "src/index.ts"))?.trim() || "src/index.ts";
	const variantId = (await ask("Variant id?", "my-ext"))?.trim() || "my-ext";
	const model = (await ask("Model (provider/model from pi models.json)?", "openai-codex/gpt-5.6-luna"))?.trim() || "openai-codex/gpt-5.6-luna";
	const thinking = (await ask("Thinking level?", "high"))?.trim() || "high";
	const piVersion = (await ask("Pi version (latest or x.y.z)?", "latest"))?.trim() || "latest";
	return { name, model, thinking, piVersion, taskRoot, variantId, extensionPath, extensionEntry };
}

export default function (pi: ExtensionAPI) {
	let wizardRunning = false;

	const hideSubmitTool = () => {
		const active = pi.getActiveTools();
		if (active.includes(SUBMIT_TOOL)) pi.setActiveTools(active.filter((name) => name !== SUBMIT_TOOL));
	};
	const showSubmitTool = () => {
		const active = pi.getActiveTools();
		if (!active.includes(SUBMIT_TOOL)) pi.setActiveTools([...active, SUBMIT_TOOL]);
	};

	pi.on("session_start", () => hideSubmitTool());

	pi.registerTool({
		name: SUBMIT_TOOL,
		label: "Submit roast experiment",
		description: "Validate a wizard-authored experiment TOML and launch it with live progress. Only valid during the active /roastmyharness wizard.",
		parameters: Type.Object({ spec_path: Type.String({ description: "Experiment TOML path the session wrote." }) }),
		execute: async (_id, params, signal, onUpdate, ctx) => {
			if (!wizardRunning) {
				throw new Error(`${SUBMIT_TOOL} is only valid during the active /roastmyharness wizard`);
			}
			const target = (params as { spec_path: string }).spec_path;
			onUpdate?.({ content: [{ type: "text", text: `validating ${target}` }], details: {} });
			let prepared: RoastResponse;
			try {
				prepared = await runBridgeJson(pi, bridgeArgs("validate", target), { signal });
			} catch (error) {
				return { content: [{ type: "text", text: `validation failed: ${error instanceof Error ? error.message : String(error)}` }], details: {} };
			}
			if (!prepared.ok || !prepared.plan_id) {
				return { content: [{ type: "text", text: summarize(prepared) }], details: prepared };
			}
			const planId = prepared.plan_id;
			const confirmed = ctx.hasUI
				? await ctx.ui.confirm("RoastMyHarness", `Launch ${prepared.experiment?.trials ?? "?"} trials?`)
				: true;
			if (!confirmed) {
				return { content: [{ type: "text", text: "launch cancelled by user" }], details: prepared };
			}
			ctx.ui.setStatus(WIDGET_ID, `running plan ${planId}`);
			try {
				const watched = await streamBridgeRun(["_bridge", "run", planId], planId, signal, (text, details) => {
					onUpdate?.({ content: [{ type: "text", text }], details });
				});
				postRunCard(pi, watched);
				return { content: [{ type: "text", text: `experiment ${watched.experiment_id}: ${watched.state}` }], details: watched };
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
			}
		},
	});

	pi.registerCommand("roastmyharness", {
		description: "Configure, validate, and launch a Pi harness comparison",
		handler: async (_args: string, ctx: ExtensionContext) => {
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
			showSubmitTool();
			try {
				const facts = await collectFacts(ctx);
				if (!facts) {
					ctx.ui.notify("RoastMyHarness wizard cancelled.", "info");
					return;
				}
				const specPath = `${ctx.cwd}/.pi-files/roastmyharness/${facts.name}.toml`;
				const request =
					`RoastMyHarness experiment request (from /roastmyharness wizard):\n` +
					`- name: ${facts.name}\n- model: ${facts.model}\n- thinking: ${facts.thinking}\n` +
					`- pi_version: ${facts.piVersion}\n- tasks: ${facts.taskRoot}\n` +
					`- variant ${facts.variantId}: extension ${facts.extensionPath} entry ${facts.extensionEntry}\n\n` +
					`Write this experiment as schema_version = 3 TOML to ${specPath} ` +
					`(draft below), fix any validation problems, then call ` +
					`${SUBMIT_TOOL} with { "spec_path": "${specPath}" }.\n\n` +
					"```toml\n" + SPEC_TEMPLATE(facts) + "```\n";
				ctx.ui.notify("Wizard facts collected. Write the TOML, then submit it.", "info");
				pi.sendMessage({ content: request, display: true, details: {} });
			} finally {
				ctx.ui.setStatus(WIDGET_ID, undefined);
				hideSubmitTool();
				wizardRunning = false;
			}
		},
	});

	pi.registerMessageRenderer(RUN_CARD_TYPE, (message, options, theme) =>
		renderRunCard(message.details as never, options.expanded, theme as never));
}
