import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";

const root = resolve("out");
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url || "/", "http://127.0.0.1").pathname);
    let path = resolve(root, "." + pathname);
    if (path !== root && !path.startsWith(root + sep)) {
      res.writeHead(403).end();
      return;
    }
    if ((await stat(path)).isDirectory()) {
      if (!pathname.endsWith("/")) {
        res.writeHead(308, { location: pathname + "/" }).end();
        return;
      }
      path = resolve(path, "index.html");
    }
    const body = await readFile(path);
    res.writeHead(200, {
      "content-type": types[extname(path)] || "application/octet-stream",
      "cache-control": "no-store",
    });
    res.end(req.method === "HEAD" ? undefined : body);
  } catch {
    res.writeHead(404).end("Not found");
  }
}).listen(3097, "127.0.0.1");
