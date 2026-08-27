#!/usr/bin/env node

import { basename, resolve } from "node:path";
import { readFile, rename } from "node:fs/promises";
import { parseArgs, required, packageFile, nonEmpty } from './shared.mjs';


async function sameContent(first, second) {
  try {
    const [a, b] = await Promise.all([readFile(first), readFile(second)]);
    return a.equals(b);
  } catch {
    return false;
  }
}

async function moveDerivedOutput(packageDir, derived, canonical) {
  if (await nonEmpty(canonical)) {
    if (await nonEmpty(derived) && !(await sameContent(canonical, derived))) {
      throw new Error(`Canonical format output conflicts with a newer native output: ${canonical}`);
    }
    return "canonical";
  }
  if (!(await nonEmpty(derived))) return null;
  await rename(derived, canonical);
  return "normalized";
}

const args = parseArgs(process.argv.slice(2));
try {
  const packageDir = resolve(required(args, "package-dir"));
  const sourceFile = required(args, "source-file");
  const sourcePath = packageFile(packageDir, sourceFile);
  if (!(await nonEmpty(sourcePath))) throw new Error(`Source file is missing or empty: ${sourceFile}`);
  const sourceName = basename(sourcePath);
  const stem = sourceName.replace(/\.[^.]+$/, "");
  const sourceAnalysis = packageFile(packageDir, `${stem}-analysis.md`);
  const sourceFormatted = packageFile(packageDir, `${stem}-formatted.md`);
  const analysis = packageFile(packageDir, "analysis.md");
  const formatted = packageFile(packageDir, "formatted.md");

  const analysisStatus = await moveDerivedOutput(packageDir, sourceAnalysis, analysis);
  const formattedStatus = await moveDerivedOutput(packageDir, sourceFormatted, formatted);
  const missing = [];
  if (!(await nonEmpty(analysis))) missing.push("analysis.md");
  if (!(await nonEmpty(formatted))) missing.push("formatted.md");
  if (missing.length) {
    throw new Error(`Format outputs are missing: ${missing.join(", ")}; expected native outputs ${stem}-analysis.md and ${stem}-formatted.md`);
  }

  console.log(JSON.stringify({
    ok: true,
    package_dir: packageDir,
    source_outputs: {
      analysis: `${stem}-analysis.md`,
      formatted: `${stem}-formatted.md`,
    },
    canonical_outputs: ["analysis.md", "formatted.md"],
    status: { analysis: analysisStatus, formatted: formattedStatus },
  }));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
