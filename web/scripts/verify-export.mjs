import { readdir, stat } from "node:fs/promises";
import { resolve, relative } from "node:path";

const root = resolve(process.argv[2] || "out");
const invalid = [];
async function inspect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const path = resolve(directory, entry.name);
    if (entry.name.startsWith("__next.")) invalid.push(relative(root, path));
    else await inspect(path);
  }
}
try {
  await stat(resolve(root, "login/index.html"));
  await inspect(root);
  if (invalid.length) {
    throw new Error(
      "Invalid segmented static export: " +
        invalid.join(", ") +
        ". Build the production artifact on Linux; do not rename or duplicate segment files.",
    );
  }
  console.log("Static export verified: no nested segment resource paths.");
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
}
