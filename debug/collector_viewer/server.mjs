import { createServer } from "node:http";
import { readFile, readdir, stat } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { dirname, extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "../..");
const defaultDataRoot = resolve(__dirname, "../work/collectors");
const publicRoot = resolve(__dirname, "public");

const args = parseArgs(process.argv.slice(2));
const port = Number(args.port || process.env.PORT || 5174);
const host = args.host || process.env.HOST || "127.0.0.1";
const dataRoot = resolve(args.data || process.env.RADAR_COLLECTOR_DATA || defaultDataRoot);
const watch = Boolean(args.watch);

const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);

function parseArgs(items) {
  const parsed = {};
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (!item.startsWith("--")) {
      continue;
    }
    const [rawKey, rawValue] = item.slice(2).split("=", 2);
    const next = items[index + 1];
    parsed[rawKey] = rawValue ?? (next && !next.startsWith("--") ? next : true);
    if (rawValue === undefined && next && !next.startsWith("--")) {
      index += 1;
    }
  }
  return parsed;
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": watch ? "no-store" : "max-age=2",
  });
  response.end(JSON.stringify(payload));
}

function sendNotFound(response) {
  response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
  response.end("Not found");
}

function isInside(base, target) {
  const relative = normalize(target).replace(base, "");
  return target === base || (target.startsWith(base + sep) && !relative.startsWith(`..${sep}`));
}

async function listJsonlFiles(root) {
  const files = [];

  async function walk(folder) {
    let entries = [];
    try {
      entries = await readdir(folder, { withFileTypes: true });
    } catch (error) {
      if (error.code === "ENOENT") {
        return;
      }
      throw error;
    }

    await Promise.all(
      entries.map(async (entry) => {
        const path = join(folder, entry.name);
        if (entry.isDirectory()) {
          if (entry.name === "state" || entry.name === "__pycache__") {
            return;
          }
          await walk(path);
          return;
        }
        if (entry.isFile() && entry.name.endsWith(".jsonl")) {
          files.push(path);
        }
      }),
    );
  }

  await walk(root);
  files.sort();
  return files;
}

function getObservedAt(event, fallbackMs) {
  const raw = event?.time?.observed_at ?? event?.observed_at ?? event?.timestamp;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string") {
    if (/^\d+$/.test(raw)) {
      return Number(raw);
    }
    const parsed = Date.parse(raw);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallbackMs || 0;
}

function compactEvent(event, filePath, lineNumber, fallbackMs) {
  const observedAt = getObservedAt(event, fallbackMs);
  const source = event.source && typeof event.source === "object" ? event.source : {};
  const subject = event.subject && typeof event.subject === "object" ? event.subject : {};
  const anchor = event.anchor && typeof event.anchor === "object" ? event.anchor : {};
  const content = event.content && typeof event.content === "object" ? event.content : {};
  const provenance = event.provenance && typeof event.provenance === "object" ? event.provenance : {};
  const relativePath = filePath.replace(dataRoot + sep, "");

  return {
    id: event.id || `${relativePath}:${lineNumber}`,
    collectorId: event.collector_id || "unknown.collector",
    observedAt,
    sourceType: source.type || "",
    sourceApp: source.app || "",
    subjectKind: subject.kind || "",
    title: subject.title || anchor.name || event.id || "Untitled event",
    text: String(content.text || ""),
    anchorType: anchor.type || "",
    action: anchor.name || "",
    contextApp: event.context?.active_app || "",
    contextWindow: event.context?.active_window_title || "",
    artifactCount: Array.isArray(event.artifacts) ? event.artifacts.length : 0,
    provenanceType: provenance.source_type || "",
    sourceUri: provenance.source_uri || "",
    filePath: relativePath,
    lineNumber,
    raw: event,
  };
}

async function readEvents(query) {
  const files = await listJsonlFiles(dataRoot);
  const events = [];
  const errors = [];
  const fallbackTimes = new Map();

  for (const filePath of files) {
    const fileStats = await stat(filePath);
    fallbackTimes.set(filePath, fileStats.mtimeMs);
    const reader = createInterface({
      input: createReadStream(filePath, { encoding: "utf-8" }),
      crlfDelay: Infinity,
    });

    let lineNumber = 0;
    for await (const line of reader) {
      lineNumber += 1;
      const trimmed = line.trim();
      if (!trimmed) {
        continue;
      }
      try {
        const event = JSON.parse(trimmed);
        events.push(compactEvent(event, filePath, lineNumber, fallbackTimes.get(filePath)));
      } catch (error) {
        errors.push({
          filePath: filePath.replace(dataRoot + sep, ""),
          lineNumber,
          message: error.message,
        });
      }
    }
  }

  return filterEvents(events, query, errors);
}

function filterEvents(events, query, errors) {
  const collector = query.get("collector") || "";
  const source = query.get("source") || "";
  const search = (query.get("q") || "").trim().toLowerCase();
  const from = Number(query.get("from") || 0);
  const to = Number(query.get("to") || 0);
  const limit = Math.min(Math.max(Number(query.get("limit") || 500), 1), 5000);

  const filtered = events
    .filter((event) => !collector || event.collectorId === collector)
    .filter((event) => !source || event.sourceType === source || event.sourceApp === source)
    .filter((event) => !from || event.observedAt >= from)
    .filter((event) => !to || event.observedAt <= to)
    .filter((event) => {
      if (!search) {
        return true;
      }
      const haystack = [
        event.collectorId,
        event.sourceType,
        event.sourceApp,
        event.subjectKind,
        event.title,
        event.text,
        event.action,
        event.contextApp,
        event.contextWindow,
        event.provenanceType,
        event.sourceUri,
        event.filePath,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(search);
    })
    .sort((a, b) => b.observedAt - a.observedAt)
    .slice(0, limit);

  const collectors = [...new Set(events.map((event) => event.collectorId))].sort();
  const sources = [
    ...new Set(events.flatMap((event) => [event.sourceType, event.sourceApp]).filter(Boolean)),
  ].sort();

  return {
    dataRoot,
    totalEvents: events.length,
    returnedEvents: filtered.length,
    collectors,
    sources,
    errors,
    events: filtered,
  };
}

async function serveStatic(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);
  const requestedPath = url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname);
  const filePath = resolve(publicRoot, requestedPath.slice(1));
  if (!isInside(publicRoot, filePath)) {
    sendNotFound(response);
    return;
  }

  try {
    const body = await readFile(filePath);
    response.writeHead(200, {
      "Content-Type": mimeTypes.get(extname(filePath)) || "application/octet-stream",
      "Cache-Control": watch ? "no-store" : "max-age=60",
    });
    response.end(body);
  } catch (error) {
    if (error.code === "ENOENT" || error.code === "EISDIR") {
      sendNotFound(response);
      return;
    }
    sendJson(response, 500, { error: error.message });
  }
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`);

  if (url.pathname === "/api/health") {
    sendJson(response, 200, {
      ok: true,
      dataRoot,
      repoRoot,
    });
    return;
  }

  if (url.pathname === "/api/events") {
    try {
      const payload = await readEvents(url.searchParams);
      sendJson(response, 200, payload);
    } catch (error) {
      sendJson(response, 500, { error: error.message, dataRoot });
    }
    return;
  }

  await serveStatic(request, response);
});

server.listen(port, host, () => {
  console.log(`Collector viewer: http://${host}:${port}`);
  console.log(`Data root: ${dataRoot}`);
});
