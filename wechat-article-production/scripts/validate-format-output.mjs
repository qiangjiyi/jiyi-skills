#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { parseArgs, required, nonEmpty, packageFile } from "./shared.mjs";

function frontmatter(content) {
  const match = content.match(/^---\s*\n([\s\S]*?)\n---\s*(?:\n|$)/);
  return match ? match[1] : null;
}

function fieldValue(block, name) {
  const match = block?.match(new RegExp(`^${name}:\\s*(?:["']([^"']+)["']|([^#\\n]+))$`, "mi"));
  return (match?.[1] || match?.[2] || "").trim();
}

function check(checks, name, passed, detail) {
  checks.push({ name, passed, detail });
}

async function validate({ packageDir, analysisFile, formattedFile, reportFile }) {
  const checks = [];
  const errors = [];
  let analysis = "";
  let formatted = "";

  for (const [label, file] of [["analysis", analysisFile], ["formatted", formattedFile]]) {
    const present = await nonEmpty(file);
    check(checks, `${label}-file`, present, present ? "non-empty file" : "missing or empty file");
    if (!present) errors.push(`${label} file is missing or empty`);
  }

  if (await nonEmpty(analysisFile)) analysis = await readFile(analysisFile, "utf8");
  const analysisSections = [
    "Highlights & Key Insights",
    "Structure Assessment",
    "Reader-Important Information",
    "Formatting Issues",
    "Typos Found",
  ];
  for (const section of analysisSections) {
    const present = new RegExp(`^##\\s+${section.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}\\s*$`, "mi").test(analysis);
    check(checks, `analysis-section:${section}`, present, present ? "section present" : "required native analysis section missing");
    if (!present) errors.push(`analysis section missing: ${section}`);
  }

  if (await nonEmpty(formattedFile)) formatted = await readFile(formattedFile, "utf8");
  const metadata = frontmatter(formatted);
  check(checks, "formatted-frontmatter", Boolean(metadata), metadata ? "frontmatter present" : "formatted output has no YAML frontmatter");
  if (!metadata) {
    errors.push("formatted output has no YAML frontmatter");
  } else {
    for (const field of ["title", "summary"]) {
      const value = fieldValue(metadata, field);
      const present = Boolean(value);
      check(checks, `formatted-frontmatter:${field}`, present, present ? "non-empty field" : `missing ${field}`);
      if (!present) errors.push(`formatted frontmatter field missing: ${field}`);
    }
    const body = formatted.replace(/^---\s*\n[\s\S]*?\n---\s*(?:\n|$)/, "").trim();
    const bodyPresent = Boolean(body);
    check(checks, "formatted-body", bodyPresent, bodyPresent ? "non-empty article body" : "formatted body is empty");
    if (!bodyPresent) errors.push("formatted body is empty");
  }

  const report = {
    schema_version: "1.0",
    passed: errors.length === 0,
    package_dir: packageDir,
    files: {
      analysis: relative(packageDir, analysisFile),
      formatted: relative(packageDir, formattedFile),
    },
    checks,
    errors,
  };
  await mkdir(dirname(reportFile), { recursive: true });
  await writeFile(reportFile, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return report;
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = resolve(required(args, "package-dir"));
  const analysisFile = packageFile(packageDir, args["analysis-file"] || "analysis.md");
  const formattedFile = packageFile(packageDir, args["formatted-file"] || "formatted.md");
  const reportFile = packageFile(packageDir, args["report-file"] || "format-validation.json");
  const report = await validate({ packageDir, analysisFile, formattedFile, reportFile });
  console.log(JSON.stringify({ ok: report.passed, report_file: reportFile, errors: report.errors }));
  if (!report.passed) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
