#!/usr/bin/env node

import { copyFile, mkdir, readFile, rm, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { homedir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseArgs, required } from "./shared.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const CREATE_SCRIPT = join(SCRIPT_DIR, "create-package.mjs");
const MANIFEST_SCRIPT = join(SCRIPT_DIR, "execution-manifest.mjs");
const DEFAULT_STAGES = "prepare,format,cover,illustrate,typeset,validate,publish";

function runNode(script, args) {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "child script failed").trim();
    throw new Error(detail);
  }
  return result.stdout.trim();
}

async function sha256(file) {
  const content = await readFile(file);
  return createHash("sha256").update(content).digest("hex");
}

async function exists(file) {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}

// Seed the jiyi-little-dancer reference image into the package so the
// illustrate stage's handoff validation (which requires illustrations/ip-reference.png
// with used_for_identity=true) has its input. Without this, the illustrate stage
// fails because nothing automated ever creates that file.
async function seedLittleDancerReference(packageDir, illustrationReference) {
  const target = illustrationReference
    ? (isAbsolute(illustrationReference) ? illustrationReference : join(packageDir, illustrationReference))
    : join(packageDir, "illustrations", "ip-reference.png");
  if (await exists(target)) return;
  const candidates = [
    join(dirname(dirname(SCRIPT_DIR)), "jiyi-little-dancer-illustrations", "assets", "little-dancer-reference-sheet.png"),
    join(homedir(), ".agents", "skills", "jiyi-little-dancer-illustrations", "assets", "little-dancer-reference-sheet.png"),
  ];
  for (const src of candidates) {
    if (await exists(src)) {
      await mkdir(dirname(target), { recursive: true });
      await copyFile(src, target);
      console.error(JSON.stringify({ ok: true, seeded: target, source: src }));
      return;
    }
  }
  console.error(JSON.stringify({
    ok: false,
    warning: `jiyi ip-reference not seeded; place ${target} manually before the illustrate stage`,
    candidates,
  }));
}

const args = parseArgs(process.argv.slice(2));
let packageDir = null;
try {
  const source = resolve(required(args, "source"));
  const sourceHash = await sha256(source);
  const slug = required(args, "slug");
  const createArgs = ["--slug", slug];
  if (args.root) createArgs.push("--root", args.root);
  if (args.timestamp) createArgs.push("--timestamp", args.timestamp);
  packageDir = runNode(CREATE_SCRIPT, createArgs);
  if (!isAbsolute(packageDir) || /[\r\n{}[\]"']/.test(packageDir)) {
    throw new Error(`create-package must return exactly one clean absolute path: ${packageDir}`);
  }
  const packageInfo = await stat(packageDir);
  if (!packageInfo.isDirectory()) throw new Error(`create-package returned a non-directory path: ${packageDir}`);

  await copyFile(source, join(packageDir, "source.md"));

  const manifestArgs = [
    "init",
    "--file", join(packageDir, "execution-manifest.json"),
    "--package-dir", packageDir,
    "--source", source,
    "--source-sha256", sourceHash,
    "--input-kind", "raw-markdown",
    "--input-path", "source.md",
    "--execution-stages", args["execution-stages"] || DEFAULT_STAGES,
    "--illustration-skill", args["illustration-skill"] || "jiyi-little-dancer-illustrations",
    "--native-dispatcher", "Skill",
    "--native-dispatcher-available", String(args["native-dispatcher-available"] ?? "true"),
  ];
  for (const key of ["cover-text-override", "illustration-reference", "publish-requested"]) {
    if (args[key] !== undefined) manifestArgs.push(`--${key}`, String(args[key]));
  }
  runNode(MANIFEST_SCRIPT, manifestArgs);
  const effectiveIllustrationSkill = args["illustration-skill"] || "jiyi-little-dancer-illustrations";
  if (effectiveIllustrationSkill === "jiyi-little-dancer-illustrations") {
    await seedLittleDancerReference(packageDir, args["illustration-reference"]);
  }
  runNode(MANIFEST_SCRIPT, [
    "stage-complete",
    "--file", join(packageDir, "execution-manifest.json"),
    "--stage", "prepare",
    "--outputs", "source.md,execution-manifest.json",
  ]);
  console.log(packageDir);
} catch (error) {
  if (packageDir) {
    await rm(packageDir, { recursive: true, force: true }).catch(() => {});
  }
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
