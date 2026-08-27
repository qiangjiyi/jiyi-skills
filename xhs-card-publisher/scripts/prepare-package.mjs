#!/usr/bin/env node
import { copyFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, values) => {
  if (value.startsWith("--")) pairs.push([value.slice(2), values[index + 1]]);
  return pairs;
}, []));
if (!args.source || !args.slug) throw new Error("Usage: --source <file.md> --slug <slug>");
const safeSlug = String(args.slug).toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, "-").replace(/^-|-$/g, "").slice(0, 80);
if (!safeSlug) throw new Error("slug must contain letters, numbers, or Chinese characters");
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "").replace("T", "-");
const packageDir = path.join("/Users/jiyi/Workspace/exports/xhs-cards", `${safeSlug}-${stamp}`);
await mkdir(path.join(packageDir, "illustrations"), { recursive: true });
await mkdir(path.join(packageDir, "cards"), { recursive: true });
await copyFile(path.resolve(args.source), path.join(packageDir, "source.md"));
await writeFile(path.join(packageDir, "manifest.json"), JSON.stringify({
  version: 1, slug: safeSlug, created_at: new Date().toISOString(), source: "source.md", status: "prepared", cards: []
}, null, 2) + "\n");
console.log(packageDir);
