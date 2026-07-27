import { createHash } from "node:crypto";
import { access, readFile, readdir, realpath, stat, writeFile, rename, mkdir } from "node:fs/promises";
import { constants } from "node:fs";
import { delimiter, dirname, isAbsolute, relative, resolve } from "node:path";
import { homedir } from "node:os";

export const RUNNER_VERSION = "1.2.0";

export const STAGES = Object.freeze({
  format: { skill: "baoyu-format-markdown", roles: ["analysis", "formatted_markdown"] },
  cover: { skill: "baoyu-cover-image", roles: ["cover_image", "prompt"] },
  illustrate: { skill: "baoyu-article-illustrator", roles: ["outline", "prompt", "body_image", "illustrated_markdown"] },
  layout: { skill: "gzh-design", roles: ["body_html", "preview_html"] },
});

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function writeJsonAtomic(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.tmp-${process.pid}`;
  await writeFile(temp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temp, path);
}

export async function readableFile(path) {
  try {
    await access(path, constants.R_OK);
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

export function expandHome(path) {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return resolve(homedir(), path.slice(2));
  return path;
}

export function parseDotenv(text) {
  const values = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const normalized = line.startsWith("export ") ? line.slice(7).trim() : line;
    const match = normalized.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

export function envCandidatePaths(baseDir) {
  return [
    process.env.WECHAT_PUBLISHER_ENV_FILE,
    resolve(expandHome(baseDir || process.cwd()), ".env.local"),
    resolve(expandHome(baseDir || process.cwd()), ".env"),
    resolve(homedir(), ".config/wechat-pipeline/.env.local"),
    resolve(homedir(), ".config/wechat-pipeline/.env"),
  ].filter(Boolean);
}

export async function loadFileEnv(explicit, baseDir) {
  if (explicit) {
    const path = resolve(expandHome(explicit));
    if (!(await readableFile(path))) throw new Error(`Cannot read env file: ${path}`);
    return { env: parseDotenv(await readFile(path, "utf8")), envFile: path };
  }
  for (const candidate of envCandidatePaths(baseDir)) {
    if (await readableFile(candidate)) {
      return { env: parseDotenv(await readFile(candidate, "utf8")), envFile: candidate };
    }
  }
  return { env: {}, envFile: null };
}

export function isWithin(parent, child) {
  const rel = relative(resolve(parent), resolve(child));
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

export function parseSkillMetadata(text) {
  const frontmatter = text.match(/^---\s*\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!frontmatter) return { name: "", version: "" };
  const name = frontmatter[1].match(/^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m)?.[1]?.trim() || "";
  const version = frontmatter[1].match(/^version:\s*["']?([^"'\r\n]+?)["']?\s*$/m)?.[1]?.trim() || "unknown";
  return { name, version };
}

async function isDirectory(path) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

async function findSkillFiles(root, maxDepth = 4) {
  if (!(await isDirectory(root))) return [];
  const result = [];
  const queue = [{ path: root, depth: 0 }];
  while (queue.length) {
    const current = queue.shift();
    let entries = [];
    try {
      entries = await readdir(current.path, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if ([".git", "node_modules", "__pycache__"].includes(entry.name)) continue;
      const path = resolve(current.path, entry.name);
      if (entry.name === "SKILL.md") {
        result.push(path);
        continue;
      }
      if (current.depth >= maxDepth) continue;
      const directory = entry.isDirectory() || (entry.isSymbolicLink() && await isDirectory(path));
      if (directory) queue.push({ path, depth: current.depth + 1 });
    }
  }
  return result;
}

export function defaultSkillRoots(extra = [], includeDefaults = true) {
  const configured = String(process.env.WECHAT_PIPELINE_SKILL_ROOTS || "")
    .split(delimiter).map((item) => item.trim()).filter(Boolean);
  return [...new Set([
    ...extra,
    ...(includeDefaults ? [
      process.env.CODEX_HOME ? resolve(process.env.CODEX_HOME, "skills") : null,
      resolve(homedir(), ".codex/skills"),
      resolve(homedir(), ".agents/skills"),
      resolve(homedir(), ".claude/skills"),
      ...configured,
    ] : []),
  ].filter(Boolean).map((item) => resolve(item)))];
}

export async function locateSkill(name, extraRoots = [], includeDefaults = true) {
  for (const root of defaultSkillRoots(extraRoots, includeDefaults)) {
    for (const file of await findSkillFiles(root)) {
      try {
        const text = await readFile(file, "utf8");
        const metadata = parseSkillMetadata(text);
        if (metadata.name === name) {
          const manifest = await hashSkillDirectory(dirname(file));
          return {
            ...metadata,
            file,
            sha256: sha256(text),
            manifest_sha256: sha256(stableJson(manifest)),
            file_manifest: manifest,
          };
        }
      } catch {
        // Continue scanning; unreadable files are unavailable.
      }
    }
  }
  throw new Error(`Installed Skill not found or unreadable: ${name}`);
}

export async function hashSkillDirectory(root) {
  const ignored = new Set([".git", ".DS_Store", "__pycache__", "node_modules"]);
  const files = {};
  const queue = [resolve(root)];
  const visited = new Set();
  while (queue.length) {
    const directory = queue.shift();
    let canonical;
    try {
      canonical = await realpath(directory);
    } catch {
      continue;
    }
    if (visited.has(canonical)) continue;
    visited.add(canonical);
    let entries = [];
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (ignored.has(entry.name)) continue;
      const path = resolve(directory, entry.name);
      let metadata;
      try {
        metadata = await stat(path);
      } catch {
        continue;
      }
      if (metadata.isDirectory()) {
        queue.push(path);
      } else if (metadata.isFile()) {
        files[relative(resolve(root), path)] = sha256(await readFile(path));
      }
    }
  }
  return Object.fromEntries(Object.entries(files).sort(([a], [b]) => a.localeCompare(b)));
}

export async function loadAndValidateRequest(path) {
  const request = JSON.parse(await readFile(resolve(path), "utf8"));
  const allowed = new Set(["stage", "skill", "run_dir", "input_file", "output_dir", "mode", "constraints", "user_preferences"]);
  const unknown = Object.keys(request).filter((key) => !allowed.has(key));
  if (unknown.length) throw new Error(`Stage request has unsupported fields: ${unknown.join(", ")}`);
  const required = ["stage", "skill", "run_dir", "input_file", "output_dir", "mode"];
  for (const key of required) if (!request[key]) throw new Error(`Stage request missing: ${key}`);
  const contract = STAGES[request.stage];
  if (!contract) throw new Error(`Unsupported stage: ${request.stage}`);
  if (request.skill !== contract.skill) throw new Error(`Stage ${request.stage} requires Skill ${contract.skill}, got ${request.skill}`);
  if (!["skill-default", "auto-recommended", "confirm"].includes(request.mode)) throw new Error(`Invalid stage mode: ${request.mode}`);
  for (const key of ["run_dir", "input_file", "output_dir"]) {
    if (!isAbsolute(request[key])) throw new Error(`${key} must be an absolute path`);
    request[key] = resolve(request[key]);
  }
  if (!isWithin(request.run_dir, request.output_dir)) throw new Error("output_dir must be inside run_dir");
  if (!(await readableFile(request.input_file))) throw new Error(`Stage input is not readable: ${request.input_file}`);
  request.constraints ||= {};
  request.user_preferences ||= {};
  return request;
}

export function parseJsonLines(text) {
  const events = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    if (!line.trim().startsWith("{")) continue;
    try { events.push(JSON.parse(line)); } catch { /* CLI warnings are not JSON events. */ }
  }
  return events;
}
