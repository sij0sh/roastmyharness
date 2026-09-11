import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { roastBinary } from "./core.ts";

export const EXPECTED_ENGINE_VERSION = "0.1.0";
export const EXPECTED_ADAPTER_PROTOCOL = 1;

export type EngineStatus =
	| { kind: "ok"; version: string }
	| { kind: "missing"; hint: string }
	| { kind: "mismatch"; found: string; hint: string };

export const UV_ENGINE_INSTALL =
	'uv tool install --from git+https://github.com/sij0sh/roastmyharness roastmyharness';
export const UV_ENGINE_UPGRADE = 'uv tool upgrade roastmyharness --from git+https://github.com/sij0sh/roastmyharness';

function parseVersion(stdout: string): string | null {
	try {
		const parsed = JSON.parse(stdout) as { ok?: unknown; version?: unknown };
		if (parsed.ok === true && typeof parsed.version === "string") return parsed.version;
	} catch {}
	const match = /roastmyharness\s+(\S+)/.exec(stdout.trim());
	return match?.[1] ?? null;
}

export async function checkEngine(host: Pick<ExtensionAPI, "exec">): Promise<EngineStatus> {
	let result;
	try {
		result = await host.exec(roastBinary(), ["_bridge", "version"], { timeout: 15_000 });
	} catch {
		return { kind: "missing", hint: `Engine not found. Install it: ${UV_ENGINE_INSTALL}` };
	}
	if (result.code !== 0) {
		return { kind: "missing", hint: `Engine call failed. Reinstall it: ${UV_ENGINE_INSTALL}` };
	}
	const found = parseVersion(result.stdout);
	if (!found) {
		return { kind: "missing", hint: `Engine returned no version. Reinstall it: ${UV_ENGINE_INSTALL}` };
	}
	if (found !== EXPECTED_ENGINE_VERSION) {
		return {
			kind: "mismatch",
			found,
			hint: `Engine ${found} differs from extension ${EXPECTED_ENGINE_VERSION}. Update it: ${UV_ENGINE_UPGRADE}`,
		};
	}
	return { kind: "ok", version: found };
}
