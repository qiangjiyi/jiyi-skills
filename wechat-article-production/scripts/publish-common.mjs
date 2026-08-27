import { access, stat } from "node:fs/promises";
import { constants } from "node:fs";
import { resolve } from "node:path";
import { homedir } from "node:os";

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
