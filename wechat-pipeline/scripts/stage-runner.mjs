#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import {
  RUNNER_VERSION, hashSkillDirectory, loadAndValidateRequest, locateSkill, parseJsonLines,
  sha256, stableJson, writeJsonAtomic,
} from "./stage-common.mjs";
import { inspectRuntime, resolveRuntime } from "./preflight.mjs";
import { validateStageArtifacts } from "./validate-stage-artifacts.mjs";

const scriptDir = resolve(dirname(fileURLToPath(import.meta.url)));
const agentSchema = resolve(scriptDir, "../references/stage-agent-result.schema.json");
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000;

function parseArgs(argv) {
  const args = {
    request: null, resumeReceipt: null, revalidateReceipt: null, answerFile: null,
    runtime: process.env.WECHAT_PIPELINE_AGENT_RUNTIME || "auto",
    agentBin: process.env.WECHAT_PIPELINE_AGENT_BIN || process.env.WECHAT_PIPELINE_CODEX_BIN || "",
    skillRoots: [], includeDefaultSkillRoots: true, json: false,
    minimalRuntime: process.env.WECHAT_PIPELINE_MINIMAL_RUNTIME === "1",
    timeoutMs: Number(process.env.WECHAT_PIPELINE_STAGE_TIMEOUT_MS || DEFAULT_TIMEOUT_MS),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--request") args.request = argv[++index];
    else if (arg === "--resume-receipt") args.resumeReceipt = argv[++index];
    else if (arg === "--revalidate-receipt") args.revalidateReceipt = argv[++index];
    else if (arg === "--answer-file") args.answerFile = argv[++index];
    else if (arg === "--runtime") args.runtime = argv[++index];
    else if (arg === "--agent-bin") args.agentBin = argv[++index];
    else if (arg === "--codex-bin") {
      args.runtime = "codex";
      args.agentBin = argv[++index];
    }
    else if (arg === "--skill-root") args.skillRoots.push(argv[++index]);
    else if (arg === "--no-default-skill-roots") args.includeDefaultSkillRoots = false;
    else if (arg === "--minimal-runtime") args.minimalRuntime = true;
    else if (arg === "--full-runtime") args.minimalRuntime = false;
    else if (arg === "--timeout-ms") args.timeoutMs = Number(argv[++index]);
    else if (arg === "--json") args.json = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  const modes = [args.request, args.resumeReceipt, args.revalidateReceipt].filter(Boolean);
  if (modes.length !== 1) throw new Error("Pass exactly one of --request, --resume-receipt, or --revalidate-receipt");
  if (args.resumeReceipt && !args.answerFile) throw new Error("--answer-file is required with --resume-receipt");
  if (args.revalidateReceipt && args.answerFile) throw new Error("--answer-file is not used with --revalidate-receipt");
  if (!Number.isSafeInteger(args.timeoutMs) || args.timeoutMs <= 0) throw new Error("--timeout-ms must be a positive integer");
  Object.assign(args, resolveRuntime(args.runtime, args.agentBin));
  return args;
}

function modeInstruction(request) {
  if (request.mode === "confirm") return "保留下游 Skill 的原生确认门；需要确认时返回 needs_input，不得替用户选择。";
  if (request.mode === "auto-recommended" && request.stage === "illustrate") {
    return "直接生成，按该 Skill 自己的推荐项自动选择并继续，不用确认。";
  }
  if (request.mode === "auto-recommended" && request.stage === "layout") {
    return "一键自动排版，按题材采用该 Skill 自己推荐的主题并继续，不用确认。";
  }
  return "遵循该 Skill 自己的 EXTEND.md、默认值和确认策略；不得由外层请求替代其决策。";
}

function expectedRoles(stage) {
  return ({
    format: "analysis, formatted_markdown",
    cover: "cover_image, prompt",
    illustrate: "outline, prompt, body_image, illustrated_markdown",
    layout: "body_html, preview_html",
  })[stage];
}

function stageSpecificInstructions(request) {
  const instructions = [];
  if (request.stage === "format") {
    instructions.push(
      `流水线预设指示（优先于 Skill 的交互式确认门）：`,
      `- 如果检测到输入已是 Markdown 格式，直接采用"优化排版"模式（选项 1），不要返回 needs_input 询问处理方式。`,
      `- 标题和摘要选择仍遵循 EXTEND.md 的 auto_select 配置。`,
      `- 依赖策略为 offline-only：不得运行会联网下载依赖的 npm/npx/bun install。若 constraints.autocorrect_cli 存在，先用主脚本的 --no-spacing 完成其它处理，再用该本地 CLI 完成 CJK 间距。`,
    );
  } else if (request.stage === "cover") {
    instructions.push(
      `Stage 产物边界：只交付 Skill 原生要求的封面图片、提示词及其原生证据；除非 Skill 明确要求，不要额外创建预览 HTML。`,
    );
  } else if (request.stage === "illustrate") {
    instructions.push(
      `Stage 产物边界：只交付 Skill 原生要求的大纲、提示词、正文图片、插图版 Markdown 及其原生证据；除非 Skill 明确要求，不要额外创建预览 HTML。`,
    );
  } else if (request.stage === "layout") {
    instructions.push(
      `排版必须生成干净正文 HTML 与该 Skill 原生要求的预览 HTML。`,
      request.constraints?.no_unresolved_placeholders
        ? `当前为发布模式：不得保留任何 {{...}} 或待补素材占位符；使用 constraints.author 作为署名，简介为空时省略空简介。`
        : `当前不是发布模式：署名占位行为遵循该 Skill 原生规则。`,
    );
  }
  return instructions.length ? `\n${instructions.join("\n")}\n` : "";
}

function buildInitialPrompt(request, skill) {
  const roles = expectedRoles(request.stage);
  return `使用 $${request.skill} 完成且只完成一个下游 Stage。\n\n` +
    `你是独立的 Stage Agent，不是微信公众号流水线编排器。必须先完整读取 ${skill.file}，再读取该 Skill 为本任务直接要求的 EXTEND.md、references 和脚本说明，然后严格执行它的完整原生流程。\n` +
    `目标 SKILL.md SHA-256：${skill.sha256}；完整 Skill manifest SHA-256：${skill.manifest_sha256}。\n\n` +
    `Stage 请求：\n${JSON.stringify(request, null, 2)}\n\n` +
    `执行模式：${modeInstruction(request)}\n${stageSpecificInstructions(request)}\n` +
    `硬性边界：\n` +
    `- 只执行 ${request.skill}，不得执行其它内容 Stage，不得调用微信发布接口。\n` +
    `- 不得修改任何 Skill 的源码、EXTEND.md、全局配置或真实凭据。\n` +
    `- 只把用户明确偏好和 constraints 当输入；标题、主题、图片数量、风格、配色、提示词和后端仍由该 Skill 按自身规则决定。\n` +
    `- 所有新产物写入 output_dir。仅当该 Skill 原生要求更新输入 Markdown 时，才可更新 input_file。\n` +
    `- 只保存该 Skill 与本 Stage 明确要求的产物；不要为其它 Stage 预先制作产物。生成后必须执行它自己的校验流程。\n` +
    `- 若需要用户选择，停止生成并返回 status=needs_input 和 questions；不得猜测。\n` +
    `- 最终响应必须符合提供的 JSON Schema。\n` +
    `- artifacts 数组中的 role 值必须精确匹配以下字符串（区分大小写，不可缩写、不可变体）：${roles}。\n` +
    `- prompt 产物可以逐文件登记，也可以登记一个仅包含非空 Markdown 提示词的目录；不得把其它角色登记为目录。\n` +
    `- decisions 记录该 Skill 最终自己选择的关键选项；validation 记录实际执行的检查。\n` +
    `- execution_evidence 必须记录实际读取的 SKILL.md、任务相关 references 和实际执行的 scripts；每项使用绝对路径和 SHA-256，脚本同时记录退出码。即使某类为空也必须返回空数组。\n` +
    `不要在最终响应外输出其它说明。`;
}

function buildResumePrompt(receipt, answer) {
  const request = { ...receipt.request };
  delete request.sha256;
  return `使用 $${receipt.skill.name} 安全续接一个此前暂停的下游 Stage。你是新的续接 Agent，但必须继承下面完整、可审计的 Stage 上下文。\n\n` +
    `必须先完整读取 ${receipt.skill.file} 以及任务直接要求的 EXTEND.md、references 和脚本说明。\n\n` +
    `Stage 请求：\n${JSON.stringify(request, null, 2)}\n\n` +
    `此前 Agent 结果：\n${JSON.stringify(receipt.agent.result, null, 2)}\n\n` +
    `用户回答：\n${answer.trim()}\n\n` +
    `把用户回答用于此前 questions，不得更换已确定的其它决策。继续严格执行该 Skill 的原生流程，不得重新扮演编排器，不得执行其它 Stage或调用微信发布接口。` +
    `所有新产物仍写入 output_dir。完成后按同一 JSON Schema 返回结果；若仍需确认，继续返回 needs_input。` +
    `execution_evidence 必须重新记录本次实际读取的 Skill 文件和实际执行的脚本。`;
}

function invocationRecord({ startedAt, completedAt, exitCode, signal, timedOut, timeoutMs, stdoutPath, stderrPath, resultPath, promptHash, threadId, runtime, resumedFrom = null }) {
  return { started_at: startedAt, completed_at: completedAt, exit_code: exitCode, stdout_path: stdoutPath,
    stderr_path: stderrPath, agent_result_path: resultPath, prompt_sha256: promptHash, thread_id: threadId || null,
    runtime, resumed_from_thread_id: resumedFrom, signal: signal || null, timed_out: timedOut, timeout_ms: timeoutMs };
}

async function runCodex({ args, codexBin, prompt, stageDir, resumedFrom = null }) {
  await mkdir(stageDir, { recursive: true });
  const sequence = String((args.invocationIndex || 0) + 1).padStart(2, "0");
  const stdoutPath = resolve(stageDir, `invocation-${sequence}.jsonl`);
  const stderrPath = resolve(stageDir, `invocation-${sequence}.stderr.log`);
  const resultPath = resolve(stageDir, `agent-result-${sequence}.json`);
  const promptPath = resolve(stageDir, `invocation-${sequence}-prompt.md`);
  await writeFile(promptPath, `${prompt}\n`, "utf8");
  const startedAt = new Date().toISOString();
  const cliArgs = ["exec", "--json", "--skip-git-repo-check", "-s", "workspace-write", "-C", args.request.run_dir,
    "--output-schema", agentSchema, "-o", resultPath, "-"];
  if (args.minimalRuntime) cliArgs.splice(1, 0, "--ignore-user-config");
  const run = spawnSync(codexBin, cliArgs, { input: prompt, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
    timeout: args.timeoutMs, killSignal: "SIGTERM" });
  const completedAt = new Date().toISOString();
  const stderrText = run.stderr || run.error?.message || "";
  await writeFile(stdoutPath, run.stdout || "", "utf8");
  await writeFile(stderrPath, stderrText, "utf8");
  const events = parseJsonLines(run.stdout);
  const threadId = events.find((event) => event.type === "thread.started")?.thread_id || null;
  const timedOut = run.error?.code === "ETIMEDOUT";
  return {
    exitCode: run.status ?? 1, stdoutPath, stderrPath, resultPath, promptPath, threadId, timedOut,
    record: {
      ...invocationRecord({ startedAt, completedAt, exitCode: run.status ?? 1, stdoutPath, stderrPath,
        resultPath, promptHash: sha256(prompt), threadId, runtime: "codex", resumedFrom, signal: run.signal, timedOut, timeoutMs: args.timeoutMs }),
      runtime_diagnostics: {
        model_cache_schema_errors: (stderrText.match(/supports_reasoning_summaries/g) || []).length,
        plugin_sync_failures: (stderrText.match(/plugin bundle sync failed/g) || []).length,
        model_refresh_timeouts: (stderrText.match(/timeout waiting for child process to exit/g) || []).length,
        minimal_runtime: Boolean(args.minimalRuntime),
      },
    },
  };
}

function claudeStructuredResult(events) {
  const event = [...events].reverse().find((item) => item?.type === "result");
  if (!event) return { result: null, sessionId: null };
  let result = event.structured_output ?? event.result ?? null;
  if (typeof result === "string") {
    try { result = JSON.parse(result); } catch { /* handled as an invalid result below */ }
  }
  return { result, sessionId: event.session_id || null };
}

async function runClaude({ args, agentBin, prompt, stageDir, skill, resumedFrom = null }) {
  await mkdir(stageDir, { recursive: true });
  const sequence = String((args.invocationIndex || 0) + 1).padStart(2, "0");
  const stdoutPath = resolve(stageDir, `invocation-${sequence}.jsonl`);
  const stderrPath = resolve(stageDir, `invocation-${sequence}.stderr.log`);
  const resultPath = resolve(stageDir, `agent-result-${sequence}.json`);
  const promptPath = resolve(stageDir, `invocation-${sequence}-prompt.md`);
  await writeFile(promptPath, `${prompt}\n`, "utf8");
  const schemaText = await readFile(agentSchema, "utf8");
  const agentName = "wechat-stage-worker";
  const agents = {
    [agentName]: {
      description: `Execute only the ${args.request.skill} Stage Skill with auditable outputs`,
      prompt: "Act only as the requested Stage worker. Follow the preloaded Skill completely, preserve its native decisions, and fail closed.",
      skills: [args.request.skill],
      permissionMode: "auto",
    },
  };
  const cliArgs = [
    "-p", "--output-format", "stream-json", "--verbose",
    "--json-schema", schemaText,
    "--permission-mode", "auto",
    "--agents", JSON.stringify(agents),
    "--agent", agentName,
  ];
  if (resumedFrom) cliArgs.push("--resume", resumedFrom);
  const startedAt = new Date().toISOString();
  const run = spawnSync(agentBin, cliArgs, {
    input: prompt,
    encoding: "utf8",
    cwd: args.request.run_dir,
    maxBuffer: 64 * 1024 * 1024,
    timeout: args.timeoutMs,
    killSignal: "SIGTERM",
  });
  const completedAt = new Date().toISOString();
  const stderrText = run.stderr || run.error?.message || "";
  await writeFile(stdoutPath, run.stdout || "", "utf8");
  await writeFile(stderrPath, stderrText, "utf8");
  const events = parseJsonLines(run.stdout);
  const structured = claudeStructuredResult(events);
  if (structured.result && typeof structured.result === "object") {
    await writeFile(resultPath, `${JSON.stringify(structured.result)}\n`, "utf8");
  }
  const timedOut = run.error?.code === "ETIMEDOUT";
  return {
    exitCode: run.status ?? 1,
    stdoutPath,
    stderrPath,
    resultPath,
    promptPath,
    threadId: structured.sessionId,
    timedOut,
    record: invocationRecord({
      startedAt,
      completedAt,
      exitCode: run.status ?? 1,
      stdoutPath,
      stderrPath,
      resultPath,
      promptHash: sha256(prompt),
      threadId: structured.sessionId,
      runtime: "claude",
      resumedFrom,
      signal: run.signal,
      timedOut,
      timeoutMs: args.timeoutMs,
    }),
  };
}

async function runAgent({ args, runtime, agentBin, prompt, stageDir, skill, resumedFrom = null }) {
  if (runtime === "claude") return runClaude({ args, agentBin, prompt, stageDir, skill, resumedFrom });
  return runCodex({ args, codexBin: agentBin, prompt, stageDir, resumedFrom });
}

async function readAgentResult(run) {
  if (run.timedOut) return { status: "failed", summary: `Stage Agent timed out after ${run.record.timeout_ms} ms`, artifacts: [], decisions: [], validation: [], questions: [] };
  if (run.exitCode !== 0) return { status: "failed", summary: `Stage Agent runtime exited with ${run.exitCode}`, artifacts: [], decisions: [], validation: [], questions: [] };
  try {
    const result = JSON.parse(await readFile(run.resultPath, "utf8"));
    for (const key of ["status", "summary", "artifacts", "decisions", "validation", "questions", "execution_evidence"]) {
      if (!(key in result)) throw new Error(`missing ${key}`);
    }
    return result;
  } catch (error) {
    return { status: "failed", summary: `Invalid Stage Agent result: ${error.message}`, artifacts: [], decisions: [], validation: [], questions: [] };
  }
}

async function finalizeReceipt({ receiptPath, base, request, skill, runtime, run, agentResult, invocations }) {
  let artifactValidation = { passed: false, checks: [], errors: [] };
  if (agentResult.status === "completed") {
    artifactValidation = await validateStageArtifacts(request, agentResult, skill);
    if (!run.threadId) {
      artifactValidation.passed = false;
      artifactValidation.errors.push("Stage Agent runtime returned no auditable session/thread ID");
    }
    const currentManifest = await hashSkillDirectory(dirname(skill.file));
    const currentManifestHash = sha256(stableJson(currentManifest));
    if (currentManifestHash !== skill.manifest_sha256) {
      artifactValidation.passed = false;
      artifactValidation.errors.push("Downstream Skill files changed during Stage execution");
    } else {
      artifactValidation.checks.push("skill-manifest-stable");
    }
  }
  const status = agentResult.status === "completed" && !artifactValidation.passed ? "failed" : agentResult.status;
  const receipt = {
    receipt_version: 1,
    runner_version: RUNNER_VERSION,
    stage: request.stage,
    status,
    created_at: base?.created_at || new Date().toISOString(),
    updated_at: new Date().toISOString(),
    skill,
    request: { ...request, sha256: sha256(stableJson(request)) },
    runtime,
    agent: {
      thread_id: run.threadId,
      thread_history: [...new Set([...(base?.agent?.thread_history || (base?.agent?.thread_id ? [base.agent.thread_id] : [])), run.threadId].filter(Boolean))],
      result: agentResult,
      result_sha256: sha256(stableJson(agentResult)),
    },
    artifact_validation: artifactValidation,
    invocations,
  };
  await writeJsonAtomic(receiptPath, receipt);
  return receipt;
}

async function initial(args) {
  const request = await loadAndValidateRequest(args.request);
  const runtime = inspectRuntime(args.agentBin, args.runtime);
  const skill = await locateSkill(request.skill, args.skillRoots, args.includeDefaultSkillRoots);
  const stageDir = resolve(request.run_dir, ".stage-runner", request.stage);
  const receiptPath = resolve(stageDir, "receipt.json");
  await mkdir(request.output_dir, { recursive: true });
  await writeJsonAtomic(resolve(stageDir, "request.json"), request);
  const prompt = buildInitialPrompt(request, skill);
  const run = await runAgent({
    args: { request, invocationIndex: 0, timeoutMs: args.timeoutMs, minimalRuntime: args.minimalRuntime },
    runtime: args.runtime,
    agentBin: args.agentBin,
    prompt,
    stageDir,
    skill,
  });
  const agentResult = await readAgentResult(run);
  return finalizeReceipt({ receiptPath, request, skill, runtime, run, agentResult, invocations: [run.record] });
}

async function resume(args) {
  const receiptPath = resolve(args.resumeReceipt);
  const base = JSON.parse(await readFile(receiptPath, "utf8"));
  if (base.status !== "needs_input") throw new Error(`Only needs_input receipts can resume, got: ${base.status}`);
  if (!base.agent?.thread_id) throw new Error("Receipt has no resumable thread_id");
  const recordedRuntime = base.runtime?.runtime;
  if (recordedRuntime && recordedRuntime !== args.runtime) {
    throw new Error(`Receipt was created by ${recordedRuntime}; resume it with --runtime ${recordedRuntime}`);
  }
  if (!base.skill?.manifest_sha256) throw new Error("Receipt predates full Skill manifest tracking and cannot be resumed safely");
  const currentManifest = await hashSkillDirectory(dirname(base.skill.file));
  if (sha256(stableJson(currentManifest)) !== base.skill.manifest_sha256) {
    throw new Error("Downstream Skill files changed after the Stage paused; refusing an unsafe resume");
  }
  const request = base.request;
  delete request.sha256;
  const validated = await loadAndValidateRequest(resolve(dirname(receiptPath), "request.json"));
  const answer = await readFile(resolve(args.answerFile), "utf8");
  if (!answer.trim()) throw new Error("Answer file is empty");
  const prompt = buildResumePrompt(base, answer);
  const stageDir = dirname(receiptPath);
  const run = await runAgent({
    args: { request: validated, invocationIndex: base.invocations.length, timeoutMs: args.timeoutMs, minimalRuntime: args.minimalRuntime },
    runtime: args.runtime,
    agentBin: args.agentBin,
    prompt,
    stageDir,
    skill: base.skill,
    resumedFrom: base.agent.thread_id,
  });
  const agentResult = await readAgentResult(run);
  return finalizeReceipt({ receiptPath, base, request: validated, skill: base.skill,
    runtime: inspectRuntime(args.agentBin, args.runtime), run, agentResult, invocations: [...base.invocations, run.record] });
}

async function revalidate(args) {
  const receiptPath = resolve(args.revalidateReceipt);
  const base = JSON.parse(await readFile(receiptPath, "utf8"));
  if (base.agent?.result?.status !== "completed") {
    throw new Error(`Only receipts with a completed Agent result can be revalidated, got: ${base.agent?.result?.status || "missing"}`);
  }
  const stageDir = dirname(receiptPath);
  const request = await loadAndValidateRequest(resolve(stageDir, "request.json"));
  const expectedRequestHash = sha256(stableJson(request));
  if (base.request?.sha256 && base.request.sha256 !== expectedRequestHash) {
    throw new Error("Receipt request hash does not match request.json");
  }
  const expectedResultHash = sha256(stableJson(base.agent.result));
  if (base.agent?.result_sha256 && base.agent.result_sha256 !== expectedResultHash) {
    throw new Error("Receipt Agent result hash does not match the recorded result");
  }
  const startedAt = new Date().toISOString();
  const artifactValidation = await validateStageArtifacts(request, base.agent.result, base.skill);
  const currentManifest = await hashSkillDirectory(dirname(base.skill.file));
  if (sha256(stableJson(currentManifest)) !== base.skill.manifest_sha256) {
    artifactValidation.passed = false;
    artifactValidation.errors.push("Downstream Skill files differ from the recorded manifest");
  }
  const completedAt = new Date().toISOString();
  const validatorFile = resolve(scriptDir, "validate-stage-artifacts.mjs");
  const validatorSha256 = sha256(await readFile(validatorFile));
  const historyDir = resolve(stageDir, "receipt-history");
  const historyName = `${startedAt.replace(/[:.]/g, "-")}-${base.status}.json`;
  await writeJsonAtomic(resolve(historyDir, historyName), base);
  const receipt = {
    ...base,
    runner_version: RUNNER_VERSION,
    status: artifactValidation.passed ? "completed" : "failed",
    updated_at: completedAt,
    artifact_validation: artifactValidation,
    revalidations: [
      ...(base.revalidations || []),
      {
        started_at: startedAt,
        completed_at: completedAt,
        previous_status: base.status,
        status: artifactValidation.passed ? "completed" : "failed",
        request_sha256: expectedRequestHash,
        agent_result_sha256: expectedResultHash,
        validator_sha256: validatorSha256,
        checks: artifactValidation.checks,
        errors: artifactValidation.errors,
      },
    ],
  };
  await writeJsonAtomic(receiptPath, receipt);
  return receipt;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const receipt = args.request ? await initial(args) : args.resumeReceipt ? await resume(args) : await revalidate(args);
  if (args.json) console.log(JSON.stringify(receipt, null, 2));
  else console.log(`Stage ${receipt.stage}: ${receipt.status}\nReceipt: ${resolve(receipt.request.run_dir, ".stage-runner", receipt.stage, "receipt.json")}`);
  if (receipt.status === "needs_input") process.exitCode = 3;
  else if (receipt.status !== "completed") process.exitCode = 1;
}

main().catch((error) => { console.error(`Stage Runner error: ${error.message}`); process.exitCode = 1; });
