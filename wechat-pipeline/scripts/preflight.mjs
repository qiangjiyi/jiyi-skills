#!/usr/bin/env node

/* ── preflight-skills ── */

import { access, readFile, readdir, stat } from "node:fs/promises";
import { constants } from "node:fs";
import { basename, delimiter, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import process from "node:process";
import { expandHome, loadFileEnv } from "./stage-common.mjs";

const ignoredDirectories = new Set([".git", "node_modules", "__pycache__"]);

const dependencies = {
  skills: [
    { name: "baoyu-format-markdown", purpose: "文章格式化、标题与摘要处理" },
    { name: "baoyu-cover-image", purpose: "微信公众号封面生成" },
    { name: "baoyu-article-illustrator", purpose: "正文配图分析与生成" },
    { name: "gzh-design", purpose: "微信公众号 HTML 排版与校验" },
  ],
};

async function isDirectory(path) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

async function findSkillFiles(root, maxDepth = 4) {
  if (!(await isDirectory(root))) return [];
  const found = [];
  const queue = [{ path: root, depth: 0 }];
  while (queue.length) {
    const current = queue.shift();
    let entries;
    try {
      entries = await readdir(current.path, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (ignoredDirectories.has(entry.name)) continue;
      const path = resolve(current.path, entry.name);
      if (entry.name === "SKILL.md") {
        found.push(path);
        continue;
      }
      if (current.depth >= maxDepth) continue;
      let directory = entry.isDirectory();
      if (entry.isSymbolicLink()) directory = await isDirectory(path);
      if (directory) queue.push({ path, depth: current.depth + 1 });
    }
  }
  return found;
}

function readSkillName(text) {
  const frontmatter = text.match(/^---\s*\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!frontmatter) return "";
  const name = frontmatter[1].match(/^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m);
  return name ? name[1].trim() : "";
}

async function buildInstalledIndex(roots) {
  const files = new Set();
  for (const root of roots) {
    for (const path of await findSkillFiles(root)) files.add(path);
  }
  const installed = new Map();
  for (const path of files) {
    try {
      await access(path, constants.R_OK);
      const name = readSkillName(await readFile(path, "utf8"));
      if (name && !installed.has(name)) installed.set(name, path);
    } catch {
      // An unreadable SKILL.md is intentionally treated as unavailable.
    }
  }
  return installed;
}

function defaultRoots() {
  const roots = [
    process.env.CODEX_HOME ? resolve(process.env.CODEX_HOME, "skills") : null,
    resolve(homedir(), ".codex/skills"),
    resolve(homedir(), ".agents/skills"),
    resolve(homedir(), ".claude/skills"),
  ].filter(Boolean);
  const configured = String(process.env.WECHAT_PIPELINE_SKILL_ROOTS || "")
    .split(delimiter)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((p) => resolve(expandHome(p)));
  return [...roots, ...configured];
}

async function runSkillsCheck(args) {
  const roots = [...new Set([...args.roots, ...(args.defaultRoots ? defaultRoots() : [])])];
  const installed = await buildInstalledIndex(roots);
  const required = dependencies.skills || [];
  const found = required
    .filter((item) => installed.has(item.name))
    .map((item) => ({ ...item, skill_file: installed.get(item.name) }));
  const missing = required.filter((item) => !installed.has(item.name));
  const result = { ok: missing.length === 0, roots, found, missing };

  if (args.json) {
    console.log(JSON.stringify(result, null, 2));
  } else if (result.ok) {
    console.log("微信公众号流水线依赖预检通过：");
    for (const item of found) console.log(`- ${item.name}：${item.skill_file}`);
  } else {
    console.error("微信公众号流水线依赖预检失败，尚未开始执行。");
    console.error("\n缺少必需 Skill：");
    for (const item of missing) console.error(`- ${item.name}：${item.purpose}`);
    if (found.length) {
      console.error("\n已检测到：");
      for (const item of found) console.error(`- ${item.name}`);
    }
    console.error("\n请安装缺失 Skill 后重新运行。本次不得生成文章产物或调用微信接口。");
  }
  if (!result.ok) process.exitCode = 1;
}

/* ── preflight-runtime ── */

export function resolveRuntime(runtime = "auto", agentBin = "") {
  if (!["auto", "codex", "claude"].includes(runtime)) throw new Error(`Unsupported Agent runtime: ${runtime}`);
  if (runtime === "auto") {
    const binaryName = basename(agentBin || "").toLowerCase();
    if (binaryName.includes("claude")) runtime = "claude";
    else if (binaryName.includes("codex")) runtime = "codex";
    else runtime = process.env.CLAUDECODE === "1" || process.env.CLAUDE_CODE_ENTRYPOINT ? "claude" : "codex";
  }
  return {
    runtime,
    agentBin: agentBin || (runtime === "claude" ? "claude" : "codex"),
  };
}

export function inspectRuntime(agentBin, requestedRuntime = "auto") {
  const selection = resolveRuntime(requestedRuntime, agentBin);
  const version = spawnSync(selection.agentBin, ["--version"], { encoding: "utf8" });
  if (version.status !== 0) {
    throw new Error(`${selection.runtime} CLI is unavailable: ${version.stderr || version.error?.message || selection.agentBin}`);
  }
  const helpArgs = selection.runtime === "codex" ? ["exec", "--help"] : ["--help"];
  const help = spawnSync(selection.agentBin, helpArgs, { encoding: "utf8" });
  if (help.status !== 0) throw new Error(`Cannot inspect ${selection.runtime} runtime: ${help.stderr || help.error?.message || selection.agentBin}`);
  const required = selection.runtime === "codex"
    ? ["--json", "--output-schema", "--output-last-message", "--ignore-user-config"]
    : ["--print", "--output-format", "--json-schema", "--resume", "--permission-mode"];
  const missing = required.filter((flag) => !help.stdout.includes(flag));
  if (missing.length) throw new Error(`${selection.runtime} CLI lacks required capabilities: ${missing.join(", ")}`);
  const diagnostics = { models_cache: null, warnings: [] };
  if (selection.runtime === "claude") {
    return {
      ok: true,
      driver: "claude-print",
      runtime: selection.runtime,
      agent_bin: selection.agentBin,
      agent_version: version.stdout.trim(),
      capabilities: required,
      diagnostics,
    };
  }
  const cachePath = resolve(process.env.CODEX_HOME || resolve(homedir(), ".codex"), "models_cache.json");
  try {
    const cache = JSON.parse(readFileSync(cachePath, "utf8"));
    const models = Array.isArray(cache.models) ? cache.models : [];
    const missingCount = models.filter((model) => !("supports_reasoning_summaries" in model)).length;
    diagnostics.models_cache = { path: cachePath, model_count: models.length, missing_supports_reasoning_summaries: missingCount };
    if (missingCount) diagnostics.warnings.push(`models cache is incompatible with this CLI (${missingCount}/${models.length} entries miss supports_reasoning_summaries)`);
  } catch (error) {
    diagnostics.models_cache = { path: cachePath, error: error.message };
  }
  return {
    ok: true,
    driver: "codex-exec",
    runtime: selection.runtime,
    agent_bin: selection.agentBin,
    agent_version: version.stdout.trim(),
    codex_bin: selection.agentBin,
    codex_version: version.stdout.trim(),
    capabilities: required,
    diagnostics,
  };
}

function runRuntimeCheck(args) {
  const result = inspectRuntime(args.agentBin, args.runtime);
  if (args.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`Stage Runner runtime ready: ${result.agent_version} (${result.driver})`);
    for (const warning of result.diagnostics.warnings) console.warn(`Runtime warning: ${warning}; use --minimal-runtime only as an explicit isolation diagnostic`);
  }
}

/* ── preflight-account ── */

function accountKey(account, suffix) {
  const normalized = account.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
  return `WECHAT_${normalized}_${suffix}`;
}

function accountValue(env, account, suffix) {
  return account === "default" ? env[`WECHAT_${suffix}`] || "" : env[accountKey(account, suffix)] || "";
}

async function runAccountCheck(args) {
  const loaded = await loadFileEnv(args.envFile, args.baseDir);
  const env = { ...loaded.env, ...process.env };
  const envFile = loaded.envFile;
  const configured = String(env.WECHAT_ACCOUNTS || "").split(",").map((item) => item.trim()).filter(Boolean);
  if (configured.length && !configured.includes(args.account)) {
    throw new Error(`账号未列入 WECHAT_ACCOUNTS：${args.account}`);
  }
  const missing = [];
  if (!accountValue(env, args.account, "APP_ID")) missing.push("APP_ID");
  if (!accountValue(env, args.account, "APP_SECRET")) missing.push("APP_SECRET");
  const author = String(accountValue(env, args.account, "AUTHOR") || "").trim();
  const authorBio = String(accountValue(env, args.account, "AUTHOR_BIO") || "").trim();
  if (!author) missing.push("AUTHOR");
  if (missing.length) throw new Error(`账号 ${args.account} 缺少配置：${missing.join(", ")}`);
  if ([...author].length > 16) throw new Error("作者字段不能超过 16 个字符");
  const result = { ok: true, account: args.account, author, author_bio: authorBio, env_file: envFile };
  console.log(args.json ? JSON.stringify(result, null, 2) : `微信账号预检通过：${args.account}（作者：${author}）`);
}

/* ── CLI ── */

function usage() {
  console.log(`用法：
  node preflight.mjs skills [--skill-root <path>] [--no-default-roots] [--json]
  node preflight.mjs runtime [--runtime <auto|codex|claude>] [--agent-bin <path>] [--json]
  node preflight.mjs account --account <账号标识> [--env-file <path>] [--base-dir <path>] [--json]

skills：检查四个下游 Skill 是否已安装。
runtime：检查 Codex 或 Claude Code Stage Agent Runner 是否可用。
account：检查微信账号配置是否完整（不输出 App Secret 或 Access Token）。`);
}

function parseArgs(argv) {
  if (!argv.length || argv[0] === "--help" || argv[0] === "-h") return { help: true };
  const subcommand = argv[0];
  if (!["skills", "runtime", "account"].includes(subcommand)) {
    throw new Error(`未知子命令：${subcommand}。可用：skills, runtime, account`);
  }
  const args = { subcommand, json: false };
  const rest = argv.slice(1);
  if (subcommand === "skills") {
    args.roots = [];
    args.defaultRoots = true;
    for (let i = 0; i < rest.length; i += 1) {
      const arg = rest[i];
      if (arg === "--json") { args.json = true; continue; }
      if (arg === "--no-default-roots") { args.defaultRoots = false; continue; }
      if (arg === "--skill-root") {
        const value = rest[++i];
        if (!value || value.startsWith("--")) throw new Error("缺少 --skill-root 的值");
        args.roots.push(resolve(expandHome(value)));
        continue;
      }
      throw new Error(`未知参数：${arg}`);
    }
  } else if (subcommand === "runtime") {
    args.runtime = process.env.WECHAT_PIPELINE_AGENT_RUNTIME || "auto";
    args.agentBin = process.env.WECHAT_PIPELINE_AGENT_BIN || process.env.WECHAT_PIPELINE_CODEX_BIN || "";
    for (let i = 0; i < rest.length; i += 1) {
      const arg = rest[i];
      if (arg === "--json") { args.json = true; continue; }
      if (arg === "--runtime") { args.runtime = rest[++i]; continue; }
      if (arg === "--agent-bin") { args.agentBin = rest[++i]; continue; }
      if (arg === "--codex-bin") {
        args.runtime = "codex";
        args.agentBin = rest[++i];
        continue;
      }
      throw new Error(`未知参数：${arg}`);
    }
  } else if (subcommand === "account") {
    args.baseDir = process.cwd();
    for (let i = 0; i < rest.length; i += 1) {
      const arg = rest[i];
      if (arg === "--json") { args.json = true; continue; }
      if (["--account", "--env-file", "--base-dir"].includes(arg)) {
        const value = rest[++i];
        if (!value || value.startsWith("--")) throw new Error(`缺少 ${arg} 的值`);
        if (arg === "--account") args.account = value;
        else if (arg === "--env-file") args.envFile = value;
        else args.baseDir = value;
        continue;
      }
      throw new Error(`未知参数：${arg}`);
    }
    if (!args.account) throw new Error("缺少 --account");
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return usage();
  if (args.subcommand === "skills") await runSkillsCheck(args);
  else if (args.subcommand === "runtime") runRuntimeCheck(args);
  else await runAccountCheck(args);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`预检错误：${error.message}`);
    process.exitCode = 1;
  });
}
