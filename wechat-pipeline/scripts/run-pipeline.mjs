#!/usr/bin/env node

import { constants } from "node:fs";
import { access, copyFile, mkdir, readFile, readdir } from "node:fs/promises";
import { spawn, spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import { readableFile, sha256, stableJson, writeJsonAtomic } from "./stage-common.mjs";

const scriptDir = resolve(dirname(fileURLToPath(import.meta.url)));
const runnerScript = resolve(scriptDir, "stage-runner.mjs");

function parseArgs(argv) {
  const args = {
    source: null, runDir: null, account: null, envFile: null, publish: false,
    resume: false,
    runtime: process.env.WECHAT_PIPELINE_AGENT_RUNTIME || "auto",
    agentBin: process.env.WECHAT_PIPELINE_AGENT_BIN || process.env.WECHAT_PIPELINE_CODEX_BIN || "",
    skillRoots: [],
    includeDefaultSkillRoots: true,
    minimalRuntime: process.env.WECHAT_PIPELINE_MINIMAL_RUNTIME === "1",
    uploadConcurrency: Number(process.env.WECHAT_UPLOAD_CONCURRENCY || 3),
    timeoutMs: Number(process.env.WECHAT_PIPELINE_STAGE_TIMEOUT_MS || 30 * 60 * 1000),
    illustrationMode: "auto-recommended", layoutMode: "auto-recommended",
    startFrom: null, formattedMarkdown: null, illustratedMarkdown: null,
    bodyHtml: null, coverImage: null, title: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--source") args.source = argv[++index];
    else if (arg === "--run-dir") args.runDir = argv[++index];
    else if (arg === "--account") args.account = argv[++index];
    else if (arg === "--env-file") args.envFile = argv[++index];
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
    else if (arg === "--upload-concurrency") args.uploadConcurrency = Number(argv[++index]);
    else if (arg === "--timeout-ms") args.timeoutMs = Number(argv[++index]);
    else if (arg === "--illustration-mode") args.illustrationMode = argv[++index];
    else if (arg === "--layout-mode") args.layoutMode = argv[++index];
    else if (arg === "--publish") args.publish = true;
    else if (arg === "--resume") args.resume = true;
    else if (arg === "--start-from") args.startFrom = argv[++index];
    else if (arg === "--formatted-markdown") args.formattedMarkdown = argv[++index];
    else if (arg === "--illustrated-markdown") args.illustratedMarkdown = argv[++index];
    else if (arg === "--body-html") args.bodyHtml = argv[++index];
    else if (arg === "--cover-image") args.coverImage = argv[++index];
    else if (arg === "--title") args.title = argv[++index];
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!args.runDir) throw new Error("--run-dir is required");
  if (!args.resume && !args.startFrom && !args.source) throw new Error("--source is required for a new run (or use --start-from)");
  if (args.publish && !args.account) throw new Error("--account is required with --publish");
  if (args.startFrom && !["cover", "layout", "publish"].includes(args.startFrom)) {
    throw new Error("--start-from must be one of: cover, layout, publish");
  }
  if (args.startFrom === "cover" && !args.formattedMarkdown) throw new Error("--formatted-markdown is required with --start-from cover");
  if (args.startFrom === "layout" && !args.illustratedMarkdown) throw new Error("--illustrated-markdown is required with --start-from layout");
  if (args.startFrom === "layout" && !args.coverImage) throw new Error("--cover-image is required with --start-from layout");
  if (args.startFrom === "publish" && !args.bodyHtml) throw new Error("--body-html is required with --start-from publish");
  if (args.startFrom === "publish" && !args.coverImage) throw new Error("--cover-image is required with --start-from publish");
  if (args.startFrom === "publish" && !args.title) throw new Error("--title is required with --start-from publish");
  if (!Number.isSafeInteger(args.timeoutMs) || args.timeoutMs <= 0) throw new Error("--timeout-ms must be a positive integer");
  if (!Number.isSafeInteger(args.uploadConcurrency) || args.uploadConcurrency < 1 || args.uploadConcurrency > 8) {
    throw new Error("--upload-concurrency must be an integer from 1 to 8");
  }
  return args;
}

function artifact(receipt, role) {
  const match = receipt.agent?.result?.artifacts?.find((item) => item.role === role);
  if (!match) throw new Error(`Stage ${receipt.stage} has no artifact role: ${role}`);
  return resolve(match.path);
}

function skipReceipt(stage, rolePathPairs) {
  return {
    stage,
    status: "skipped",
    agent: { thread_id: null, result: { artifacts: rolePathPairs.map(([role, path]) => ({ role, path: resolve(path) })) } },
    skill: { name: "", version: "external", sha256: "" },
    artifact_validation: { passed: true },
    request: { sha256: null },
  };
}

function runPreflight(script, scriptArgs, label) {
  const result = spawnSync(process.execPath, [resolve(scriptDir, script), ...scriptArgs], { encoding: "utf8" });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) throw new Error(`${label} failed`);
  return result.stdout;
}

function runPreflightJson(script, scriptArgs, label) {
  const result = spawnSync(process.execPath, [resolve(scriptDir, script), ...scriptArgs, "--json"], { encoding: "utf8" });
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) throw new Error(`${label} failed`);
  try {
    return JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`${label} returned invalid JSON: ${error.message}`);
  }
}

function runChild(command, commandArgs) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(command, commandArgs, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; process.stdout.write(chunk); });
    child.stderr.on("data", (chunk) => { stderr += chunk; process.stderr.write(chunk); });
    child.on("error", rejectPromise);
    child.on("close", (status, signal) => resolvePromise({ status, signal, stdout, stderr }));
  });
}

async function resolveOfflineAutocorrect() {
  const explicit = String(process.env.WECHAT_PIPELINE_AUTOCORRECT_CLI || "").trim();
  if (explicit) {
    const path = resolve(explicit);
    try {
      await access(path, constants.R_OK);
    } catch {
      throw new Error(`WECHAT_PIPELINE_AUTOCORRECT_CLI is unreadable: ${path}`);
    }
    return { available: true, cli: path, source: "environment" };
  }
  const cacheRoot = resolve(homedir(), ".npm/_npx");
  let entries = [];
  try {
    entries = await readdir(cacheRoot, { withFileTypes: true });
  } catch {
    return { available: false, cli: null, source: "not-found" };
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const candidate = resolve(cacheRoot, entry.name, "node_modules/autocorrect-node/cli.js");
    if (await readableFile(candidate)) return { available: true, cli: candidate, source: "npm-npx-cache" };
  }
  return { available: false, cli: null, source: "not-found" };
}

const toolchainFiles = [
  "run-pipeline.mjs",
  "stage-runner.mjs",
  "stage-common.mjs",
  "validate-stage-artifacts.mjs",
  "publish-wechat-article.mjs",
  "preflight.mjs",
];

async function toolchainProvenance() {
  return Object.fromEntries(await Promise.all(toolchainFiles.map(async (name) => {
    const path = resolve(scriptDir, name);
    return [name, { path, sha256: sha256(await readFile(path)) }];
  })));
}

async function existingReceipt(runDir, stage) {
  const path = resolve(runDir, ".stage-runner", stage, "receipt.json");
  if (!(await readableFile(path))) return null;
  return JSON.parse(await readFile(path, "utf8"));
}

async function executeStage(args, request) {
  const previous = await existingReceipt(args.runDir, request.stage);
  if (previous?.status === "completed" && previous.artifact_validation?.passed) {
    const expectedHash = sha256(stableJson(request));
    if (previous.request?.sha256 && previous.request.sha256 !== expectedHash) {
      throw new Error(`Stage ${request.stage} completed under a different request; archive its receipt before rerunning`);
    }
    return previous;
  }
  if (previous?.status === "needs_input") {
    const path = resolve(args.runDir, ".stage-runner", request.stage, "receipt.json");
    throw new Error(`Stage ${request.stage} needs user input. Resume its receipt first: ${path}`);
  }
  if (previous) {
    const path = resolve(args.runDir, ".stage-runner", request.stage, "receipt.json");
    throw new Error(`Stage ${request.stage} already has a non-success receipt (${previous.status}); evidence was preserved. Inspect or archive the run before retrying: ${path}`);
  }
  const requestPath = resolve(args.runDir, ".stage-runner", "requests", `${request.stage}.json`);
  await writeJsonAtomic(requestPath, request);
  const runnerArgs = [runnerScript, "--request", requestPath, "--runtime", args.runtime, "--timeout-ms", String(args.timeoutMs)];
  if (args.agentBin) runnerArgs.push("--agent-bin", args.agentBin);
  for (const root of args.skillRoots) runnerArgs.push("--skill-root", root);
  if (!args.includeDefaultSkillRoots) runnerArgs.push("--no-default-skill-roots");
  runnerArgs.push(args.minimalRuntime ? "--minimal-runtime" : "--full-runtime");
  const run = await runChild(process.execPath, runnerArgs);
  const receipt = await existingReceipt(args.runDir, request.stage);
  if (run.status === 3 && receipt) {
    const questions = receipt.agent?.result?.questions || [];
    throw new Error(`Stage ${request.stage} needs user input: ${questions.map((item) => item.question).join(" | ")}`);
  }
  if (run.status !== 0 || !receipt) throw new Error(`Stage ${request.stage} failed; inspect ${resolve(args.runDir, ".stage-runner", request.stage)}`);
  return receipt;
}

function frontmatterValue(markdown, key) {
  const block = markdown.match(/^---\s*\r?\n([\s\S]*?)\r?\n---/);
  const match = block?.[1].match(new RegExp(`^${key}:\\s*(.+)$`, "m"));
  return match?.[1]?.trim().replace(/^["“”']+|["“”']+$/g, "") || "";
}

async function publish(args, receipts) {
  let title = args.title || "";
  let summary = "";
  const markdownPath = artifact(receipts.illustrate, "illustrated_markdown");
  if (await readableFile(markdownPath)) {
    const markdown = await readFile(markdownPath, "utf8");
    if (!title) title = frontmatterValue(markdown, "title");
    summary = frontmatterValue(markdown, "summary");
  }
  if (!title) throw new Error("Cannot publish: no title (provide --title or illustrated Markdown with frontmatter)");
  const resultFile = resolve(args.runDir, "publish-result.json");
  const publishArgs = [resolve(scriptDir, "publish-wechat-article.mjs"),
    "--html", artifact(receipts.layout, "body_html"),
    "--cover", artifact(receipts.cover, "cover_image"),
    "--title", title, "--summary", summary, "--account", args.account,
    "--yes", "--result-file", resultFile,
    "--upload-concurrency", String(args.uploadConcurrency)];
  if (args.envFile) publishArgs.push("--env-file", args.envFile);
  const run = spawnSync(process.execPath, publishArgs, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (run.stdout) process.stdout.write(run.stdout);
  if (run.stderr) process.stderr.write(run.stderr);
  if (run.status !== 0) throw new Error("WeChat draft creation failed; draft/add was not retried by the pipeline");
  return resultFile;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  args.runDir = resolve(args.runDir);
  const toolchain = await toolchainProvenance();
  if (!["auto-recommended", "confirm"].includes(args.illustrationMode)) throw new Error(`Invalid illustration mode: ${args.illustrationMode}`);
  if (!["auto-recommended", "confirm"].includes(args.layoutMode)) throw new Error(`Invalid layout mode: ${args.layoutMode}`);
  const skillArgs = [];
  for (const root of args.skillRoots) skillArgs.push("--skill-root", root);
  if (!args.includeDefaultSkillRoots) skillArgs.push("--no-default-roots");
  runPreflight("preflight.mjs", ["skills", ...skillArgs], "Skill dependency preflight");
  const runtimeArgs = ["runtime", "--runtime", args.runtime];
  if (args.agentBin) runtimeArgs.push("--agent-bin", args.agentBin);
  runPreflight("preflight.mjs", runtimeArgs, "Stage Runner runtime preflight");
  let accountInfo = null;
  if (args.account) {
    const accountArgs = ["--account", args.account];
    if (args.envFile) accountArgs.push("--env-file", args.envFile);
    accountInfo = runPreflightJson("preflight.mjs", ["account", ...accountArgs], "WeChat account preflight");
    console.log(`微信账号预检通过：${accountInfo.account}（作者：${accountInfo.author}）`);
  }
  const formatRuntime = await resolveOfflineAutocorrect();
  if (formatRuntime.available) console.log(`Formatting runtime ready: offline autocorrect (${formatRuntime.source})`);
  else console.warn("Formatting runtime warning: offline autocorrect was not found; the Stage must not attempt a network install");
  await mkdir(args.runDir, { recursive: true });
  const sourcePath = resolve(args.runDir, "source.md");
  if (!args.resume) {
    const existing = (await readdir(args.runDir)).filter((name) => name !== ".DS_Store");
    if (existing.length) throw new Error(`New run_dir must be empty: ${args.runDir}`);
    if (args.source) await copyFile(resolve(args.source), sourcePath);
    else if (args.formattedMarkdown) await copyFile(resolve(args.formattedMarkdown), sourcePath);
  } else if (!(await readableFile(sourcePath))) {
    throw new Error(`Cannot resume without source.md: ${sourcePath}`);
  }

  const receipts = {};
  const skipFormat = Boolean(args.startFrom) && args.startFrom !== "cover";
  const skipCoverIllustrate = args.startFrom && ["layout", "publish"].includes(args.startFrom);
  const skipLayout = args.startFrom === "publish";

  if (skipFormat) {
    receipts.format = skipReceipt("format", [["formatted_markdown", args.formattedMarkdown || args.illustratedMarkdown]]);
  } else {
    receipts.format = await executeStage(args, {
      stage: "format", skill: "baoyu-format-markdown", run_dir: args.runDir,
      input_file: sourcePath, output_dir: args.runDir, mode: "skill-default",
      constraints: {
        dependency_policy: "offline-only",
        autocorrect_cli: formatRuntime.cli || "",
        avoid_runtime_package_downloads: true,
      },
      user_preferences: {},
    });
  }
  const formatted = artifact(receipts.format, "formatted_markdown");

  if (skipCoverIllustrate) {
    receipts.cover = skipReceipt("cover", [["cover_image", args.coverImage]]);
    receipts.illustrate = skipReceipt("illustrate", [["illustrated_markdown", args.illustratedMarkdown]]);
  } else {
    const coverInput = resolve(args.runDir, ".stage-runner", "inputs", "cover-source.md");
    await mkdir(dirname(coverInput), { recursive: true });
    if (!(await readableFile(coverInput))) await copyFile(formatted, coverInput);
    const coverRequest = {
      stage: "cover", skill: "baoyu-cover-image", run_dir: args.runDir,
      input_file: coverInput, output_dir: resolve(args.runDir, "cover"), mode: "skill-default",
      constraints: { aspect_ratio: "2.35:1" }, user_preferences: {},
    };
    const illustrationRequest = {
      stage: "illustrate", skill: "baoyu-article-illustrator", run_dir: args.runDir,
      input_file: formatted, output_dir: resolve(args.runDir, "imgs"), mode: args.illustrationMode,
      constraints: {}, user_preferences: {},
    };
    const parallel = await Promise.allSettled([
      executeStage(args, coverRequest),
      executeStage(args, illustrationRequest),
    ]);
    const failures = parallel.filter((item) => item.status === "rejected");
    if (failures.length) {
      throw new Error(`Parallel cover/illustrate stages failed: ${failures.map((item) => item.reason.message).join(" | ")}`);
    }
    [receipts.cover, receipts.illustrate] = parallel.map((item) => item.value);
  }

  if (skipLayout) {
    receipts.layout = skipReceipt("layout", [["body_html", args.bodyHtml]]);
  } else {
    receipts.layout = await executeStage(args, {
      stage: "layout", skill: "gzh-design", run_dir: args.runDir,
      input_file: artifact(receipts.illustrate, "illustrated_markdown"), output_dir: args.runDir,
      mode: args.layoutMode,
      constraints: {
        no_unresolved_placeholders: Boolean(accountInfo),
        author: accountInfo?.author || "",
        author_bio: accountInfo?.author_bio || "",
        omit_empty_author_bio: true,
      },
      user_preferences: accountInfo ? { author: accountInfo.author, author_bio: accountInfo.author_bio || "" } : {},
    });
  }

  const manifest = {
    version: 1, status: "completed", run_dir: args.runDir, updated_at: new Date().toISOString(),
    toolchain,
    stages: Object.fromEntries(Object.entries(receipts).map(([stage, receipt]) => [stage, {
      status: receipt.status,
      receipt: receipt.status === "skipped" ? null : resolve(args.runDir, ".stage-runner", stage, "receipt.json"),
      thread_id: receipt.agent?.thread_id || null,
      skill_sha256: receipt.skill?.sha256 || "",
    }])),
    publish: { requested: args.publish, result_file: null },
  };
  if (args.publish) manifest.publish.result_file = await publish(args, receipts);
  await writeJsonAtomic(resolve(args.runDir, "pipeline-manifest.json"), manifest);
  console.log(`Pipeline completed${args.publish ? " and WeChat draft was created" : " without publishing"}.\nManifest: ${resolve(args.runDir, "pipeline-manifest.json")}`);
}

main().catch((error) => { console.error(`Pipeline error: ${error.message}`); process.exitCode = 1; });
