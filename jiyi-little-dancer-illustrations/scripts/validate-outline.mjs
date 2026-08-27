#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { parseOutline, REQUIRED_OUTLINE_FIELDS } from "./outline-utils.mjs";

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      args[key] = next;
      i += 1;
    } else args[key] = true;
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
try {
  if (!args.file) throw new Error("Missing --file");
  const file = resolve(args.file);
  const content = await readFile(file, "utf8");
  const parsed = parseOutline(content);
  const report = {
    schema_version: "1.0",
    file,
    shot_count: parsed.shots.length,
    required_fields: REQUIRED_OUTLINE_FIELDS,
    passed: parsed.errors.length === 0,
    errors: parsed.errors,
    shots: parsed.shots.map((shot) => ({ number: shot.number, title: shot.title, fields: shot.fields })),
  };
  if (args["report-file"]) {
    const reportFile = resolve(args["report-file"]);
    await mkdir(dirname(reportFile), { recursive: true });
    await writeFile(reportFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
  console.log(JSON.stringify(report, null, 2));
  if (!report.passed) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
