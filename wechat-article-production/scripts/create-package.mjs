#!/usr/bin/env node

import { mkdir, realpath } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { parseArgs, required } from "./shared.mjs";

const DEFAULT_ROOT = join(homedir(), "Workspace/exports/wechat-articles");

function safeRoot(value) {
  const root = resolve(value);
  if (!isAbsolute(value)) throw new Error(`--root must be an absolute path: ${value}`);
  if (/\0|[\r\n]/.test(value)) throw new Error("--root contains a control character");
  return root;
}

function slugify(value) {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!slug) throw new Error("Article slug is empty after normalization");
  return slug;
}

function timestamp(args) {
  if (args.timestamp) return args.timestamp;
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

const args = parseArgs(process.argv.slice(2));
try {
  const requestedRoot = safeRoot(args.root || process.env.WECHAT_ARTICLE_EXPORT_ROOT || DEFAULT_ROOT);
  await mkdir(requestedRoot, { recursive: true });
  const root = await realpath(requestedRoot);
  if (root === "/" || root === resolve(homedir())) {
    throw new Error("--root must be an export/workspace directory, not the filesystem root or user home");
  }
  const packageDir = resolve(join(root, `${slugify(required(args, "slug"))}-${timestamp(args)}`));
  const packageRelative = relative(root, packageDir);
  if (!packageRelative || packageRelative.startsWith("..") || isAbsolute(packageRelative)) {
    throw new Error("package directory escaped the export root");
  }
  // A package must be new. This prevents a retry from silently reusing a partial run.
  await mkdir(packageDir);
  const resolvedPackageDir = await realpath(packageDir);
  const result = { ok: true, package_dir: resolvedPackageDir, root };
  // Machine callers should receive a path by default. JSON is opt-in so command
  // substitution cannot accidentally turn an object into a filesystem path.
  console.log(args.json ? JSON.stringify(result) : resolvedPackageDir);
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
