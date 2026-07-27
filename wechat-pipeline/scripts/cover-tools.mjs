#!/usr/bin/env node
import { createHash } from "node:crypto";
import { access, readFile, readdir, stat, unlink } from "node:fs/promises";
import { constants } from "node:fs";
import { resolve } from "node:path";

function parseArgs(argv) {
  if (!argv.length) throw new Error("Usage: cover-tools.mjs <normalize|validate> --cover-dir <directory>");
  const action = argv[0];
  if (action !== "normalize" && action !== "validate") {
    throw new Error(`Unknown action: ${action}. Use normalize or validate.`);
  }
  const index = argv.indexOf("--cover-dir");
  if (index < 0 || !argv[index + 1]) throw new Error("Usage: cover-tools.mjs <normalize|validate> --cover-dir <directory>");
  return { action, coverDir: resolve(argv[index + 1]) };
}

function keeperRank(name) {
  if (/^\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$/.test(name)) return 0;
  if (name === "cover.md") return 1;
  return 2;
}

async function normalize(coverDir) {
  const promptsDir = resolve(coverDir, "prompts");
  const files = (await readdir(promptsDir)).filter((name) => name.endsWith(".md")).sort();
  if (files.length === 0) throw new Error(`No prompt files found in ${promptsDir}`);

  const groups = new Map();
  for (const name of files) {
    const content = await readFile(resolve(promptsDir, name));
    const hash = createHash("sha256").update(content).digest("hex");
    const group = groups.get(hash) || [];
    group.push(name);
    groups.set(hash, group);
  }

  const removed = [];
  for (const names of groups.values()) {
    if (names.length < 2) continue;
    names.sort((a, b) => keeperRank(a) - keeperRank(b) || a.localeCompare(b));
    for (const duplicate of names.slice(1)) {
      await unlink(resolve(promptsDir, duplicate));
      removed.push(duplicate);
    }
  }

  console.log(JSON.stringify({ ok: true, removed, remaining: (await readdir(promptsDir)).filter((name) => name.endsWith(".md")).sort() }, null, 2));
}

async function validate(coverDir) {
  const cover = resolve(coverDir, "cover.png");
  await access(cover, constants.R_OK);
  if ((await stat(cover)).size === 0) throw new Error("cover.png is empty");

  const promptsDir = resolve(coverDir, "prompts");
  const files = (await readdir(promptsDir)).filter((name) => name.endsWith(".md")).sort();
  if (files.length === 0) throw new Error(`No prompt files found in ${promptsDir}`);
  const hashes = new Map();
  for (const name of files) {
    const content = await readFile(resolve(promptsDir, name));
    if (!content.toString("utf8").trim()) throw new Error(`Prompt is empty: ${name}`);
    const hash = createHash("sha256").update(content).digest("hex");
    if (hashes.has(hash)) throw new Error(`Duplicate prompt content: ${hashes.get(hash)} and ${name}`);
    hashes.set(hash, name);
  }
  console.log(JSON.stringify({ ok: true, cover, prompts: files.map((name) => resolve(promptsDir, name)) }, null, 2));
}

async function main() {
  const { action, coverDir } = parseArgs(process.argv.slice(2));
  if (action === "normalize") await normalize(coverDir);
  else await validate(coverDir);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
