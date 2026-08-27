#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

const SKILL_ID = "jiyi-little-dancer-illustrations";
const DEFAULT_BACKEND = "auto";
const ALLOWED_MODES = new Set(["auto", "ask"]);

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const value = argv[index + 1];
    if (value && !value.startsWith("--")) {
      result[key] = value;
      index += 1;
    } else {
      result[key] = true;
    }
  }
  return result;
}

function validateBackend(value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("preferred_image_backend must be a non-empty string");
  }
  const backend = value.trim();
  if (ALLOWED_MODES.has(backend) || /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(backend)) return backend;
  throw new Error(`invalid image backend: ${backend}`);
}

async function readPreference(file) {
  try {
    const source = await readFile(file, "utf8");
    const match = source.match(/^preferred_image_backend:\s*(?:["']([^"']+)["']|([^#\s]+))/m);
    if (!match) return null;
    return { backend: validateBackend(match[1] || match[2]), file };
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw new Error(`cannot read ${file}: ${error.message}`);
  }
}

async function resolveBackend(args) {
  if (args.requested !== undefined) {
    return { backend: validateBackend(args.requested), source: "request", file: null };
  }

  const home = homedir();
  const projectDir = resolve(args["project-dir"] || process.cwd());
  const configHome = process.env.XDG_CONFIG_HOME || join(home, ".config");
  const candidates = [
    join(projectDir, ".jiyi-skills", SKILL_ID, "EXTEND.md"),
    join(configHome, ".jiyi-skills", SKILL_ID, "EXTEND.md"),
    join(home, ".jiyi-skills", SKILL_ID, "EXTEND.md"),
  ];
  for (const file of candidates) {
    const preference = await readPreference(file);
    if (preference) return { ...preference, source: "extend" };
  }
  return { backend: DEFAULT_BACKEND, source: "default", file: null };
}

try {
  const result = await resolveBackend(parseArgs(process.argv.slice(2)));
  console.log(JSON.stringify({ ok: true, skill: SKILL_ID, ...result }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
