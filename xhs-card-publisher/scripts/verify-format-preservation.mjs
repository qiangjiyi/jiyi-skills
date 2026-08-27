#!/usr/bin/env node
import { readFile } from "node:fs/promises";

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

const sourcePath = argument("--source");
const formattedPath = argument("--formatted");
if (!sourcePath || !formattedPath) throw new Error("Usage: --source <source.md> --formatted <formatted.md>");

const source = await readFile(sourcePath, "utf8");
const formatted = await readFile(formattedPath, "utf8");
const allowed = /\{\{(?:bg|color):#[0-9a-fA-F]{3,8}\|([\s\S]*?)\}\}|\{\{underline:(?:solid|dashed)\|([\s\S]*?)\}\}/g;
const restored = formatted.replace(allowed, (_, colored, underlined) => colored ?? underlined);

if (restored === source) {
  console.log(JSON.stringify({ passed: true, source: sourcePath, formatted: formattedPath }));
  process.exit(0);
}

const left = source.split("\n");
const right = restored.split("\n");
let line = 0;
while (line < Math.max(left.length, right.length) && left[line] === right[line]) line += 1;
throw new Error(`原文保护校验失败：第 ${line + 1} 行不一致。\n源文：${JSON.stringify(left[line] ?? "")}\n格式化后：${JSON.stringify(right[line] ?? "")}`);
