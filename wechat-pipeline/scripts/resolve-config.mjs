#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { resolve } from "node:path";
import process from "node:process";
import { expandHome, parseDotenv, readableFile, loadFileEnv } from "./stage-common.mjs";

/* ── execution-mode ── */

const MODE_DEFAULTS = { illustration_mode: "auto-recommended", layout_mode: "auto-recommended" };

function normalizeMode(value, key) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["auto", "auto-recommended", "recommended", "true", "1"].includes(normalized)) return "auto-recommended";
  if (["confirm", "ask", "false", "0"].includes(normalized)) return "confirm";
  throw new Error(`${key} must be auto-recommended or confirm, got: ${value}`);
}

async function resolveMode(args) {
  const loaded = await loadFileEnv(args.envFile);
  const env = { ...loaded.env, ...process.env };
  const globalMode = env.WECHAT_PIPELINE_RECOMMENDATION_MODE || "";
  const illustrationRaw = env.WECHAT_PIPELINE_ILLUSTRATION_MODE || globalMode || MODE_DEFAULTS.illustration_mode;
  const layoutRaw = env.WECHAT_PIPELINE_LAYOUT_MODE || globalMode || MODE_DEFAULTS.layout_mode;
  return {
    illustration_mode: normalizeMode(illustrationRaw, "WECHAT_PIPELINE_ILLUSTRATION_MODE"),
    layout_mode: normalizeMode(layoutRaw, "WECHAT_PIPELINE_LAYOUT_MODE"),
    env_file: loaded.envFile,
  };
}

/* ── output-dir ── */

function absolutePath(path) {
  return resolve(expandHome(String(path).trim()));
}

async function readConfiguredOutputDir(explicitEnvFile) {
  if (explicitEnvFile) {
    const path = absolutePath(explicitEnvFile);
    if (!(await readableFile(path))) throw new Error(`配置文件不存在：${path}`);
    const env = parseDotenv(await readFile(path, "utf8"));
    return { value: env.WECHAT_PIPELINE_OUTPUT_DIR || "", envFile: path };
  }

  const candidates = [
    process.env.WECHAT_PUBLISHER_ENV_FILE,
    resolve(process.cwd(), ".env.local"),
    resolve(process.cwd(), ".env"),
    resolve(homedir(), ".config/wechat-pipeline/.env.local"),
    resolve(homedir(), ".config/wechat-pipeline/.env"),
  ].filter(Boolean).map(absolutePath);

  for (const path of candidates) {
    if (!(await readableFile(path))) continue;
    const env = parseDotenv(await readFile(path, "utf8"));
    if (env.WECHAT_PIPELINE_OUTPUT_DIR) {
      return { value: env.WECHAT_PIPELINE_OUTPUT_DIR, envFile: path };
    }
  }
  return { value: "", envFile: null };
}

async function resolveOutputDir(args) {
  if (args.outputDir) {
    return { outputDir: absolutePath(args.outputDir), source: "command-line", envFile: null };
  }
  if (process.env.WECHAT_PIPELINE_OUTPUT_DIR?.trim()) {
    return { outputDir: absolutePath(process.env.WECHAT_PIPELINE_OUTPUT_DIR), source: "process-environment", envFile: null };
  }
  const configured = await readConfiguredOutputDir(args.envFile);
  if (configured.value.trim()) {
    return { outputDir: absolutePath(configured.value), source: "dotenv", envFile: configured.envFile };
  }
  if (process.env.XDG_DATA_HOME?.trim()) {
    return {
      outputDir: resolve(absolutePath(process.env.XDG_DATA_HOME), "wechat-pipeline/exports"),
      source: "xdg-data-home",
      envFile: null,
    };
  }
  return {
    outputDir: resolve(homedir(), ".local/share/wechat-pipeline/exports"),
    source: "home-fallback",
    envFile: null,
  };
}

/* ── CLI ── */

function usage() {
  console.log(`用法：
  node resolve-config.mjs mode [--env-file <path>] [--json]
  node resolve-config.mjs output-dir [--output-dir <path>] [--env-file <path>] [--json]

mode：解析插图和排版阶段的执行模式（auto-recommended / confirm）。
output-dir：按"命令参数 → 进程环境变量 → .env → XDG_DATA_HOME → HOME"解析流水线输出根目录。`);
}

function parseArgs(argv) {
  if (!argv.length || argv[0] === "--help" || argv[0] === "-h") return { help: true };
  const subcommand = argv[0];
  if (subcommand !== "mode" && subcommand !== "output-dir") {
    throw new Error(`未知子命令：${subcommand}。可用：mode, output-dir`);
  }
  const args = { subcommand, json: false };
  for (let i = 1; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") { args.json = true; continue; }
    if (arg === "--output-dir" || arg === "--env-file") {
      const value = argv[++i];
      if (!value || value.startsWith("--")) throw new Error(`缺少 ${arg} 的值`);
      args[arg === "--output-dir" ? "outputDir" : "envFile"] = value;
      continue;
    }
    throw new Error(`未知参数：${arg}`);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return usage();

  if (args.subcommand === "mode") {
    const result = await resolveMode(args);
    if (args.json) console.log(JSON.stringify(result, null, 2));
    else console.log(`illustration=${result.illustration_mode}\nlayout=${result.layout_mode}`);
  } else {
    const result = await resolveOutputDir(args);
    console.log(args.json
      ? JSON.stringify({ output_dir: result.outputDir, source: result.source, env_file: result.envFile }, null, 2)
      : result.outputDir);
  }
}

main().catch((error) => {
  console.error(`错误：${error.message}`);
  process.exitCode = 1;
});
