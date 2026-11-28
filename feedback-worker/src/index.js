// clmix-feedback: takes anonymous feedback from the CLMix apps and files
// it as a GitHub issue. The GitHub token lives only in this Worker's
// secrets; the apps never see it.
//
//   POST /api/feedback   {id, category, message, version, platform,
//                         installId, createdUtc, includeDiagnostics,
//                         diagnostics}
//
// 201 filed, 409 that id was already filed (the app treats it as sent),
// 400 invalid, 429 over the limit, 502 GitHub refused (the app retries).

import { DurableObject } from "cloudflare:workers";

const CATEGORIES = {
  "Bug Report": "bug-report",
  "Feature Request": "feature-request",
  "General Feedback": "general-feedback",
  "Other": "other",
};
const PLATFORMS = ["Android", "iOS", "Windows", "Linux", "macOS"];

const MIN_MESSAGE = 10;
const MAX_MESSAGE = 5000;
const MAX_DIAGNOSTICS = 2000;

const DAY_MS = 24 * 60 * 60 * 1000;
const PER_INSTALL_PER_DAY = 10;
// An install id is whatever the client says it is, so a second limit by
// address stops one sender minting ids to get round the first. Generous,
// since a whole team on one venue's Wi-Fi shares an address.
const PER_ADDRESS_PER_DAY = 30;
// How long a filed id is remembered, to answer a resend with 409.
const SEEN_MS = 30 * DAY_MS;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function reply(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function validate(p) {
  if (!p || typeof p !== "object" || Array.isArray(p)) return "payload malformed";
  if (!(p.category in CATEGORIES)) return "category invalid";
  if (typeof p.message !== "string") return "message missing";
  const message = p.message.trim();
  if (message.length < MIN_MESSAGE) return "message too short";
  if (message.length > MAX_MESSAGE) return "message too long";
  if (typeof p.installId !== "string" || !UUID.test(p.installId)) return "installId missing";
  if (p.id != null && (typeof p.id !== "string" || !UUID.test(p.id))) return "id invalid";
  if (typeof p.version !== "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(p.version)) return "version invalid";
  if (!PLATFORMS.includes(p.platform)) return "platform invalid";
  if (p.diagnostics != null) {
    if (p.includeDiagnostics !== true) return "diagnostics not opted in";
    if (typeof p.diagnostics !== "string" || p.diagnostics.length > MAX_DIAGNOSTICS) return "diagnostics invalid";
  }
  return null;
}

// Feedback is filed in a repository other people can read, so whatever a
// user types must not ping anyone: "@name" and "#123" are broken with a
// zero-width space, which GitHub does not link.
function defang(text) {
  return text.replace(/@/g, "@​").replace(/#(\d)/g, "#​$1");
}

function issueBody(p, submitted) {
  const lines = [
    "# User Feedback",
    "",
    defang(p.message.trim()),
    "",
    "---",
    "",
    "## Category", "", p.category, "",
    "## Platform", "", p.platform, "",
    "## Version", "", p.version, "",
    "## Installation ID", "", "`" + p.installId + "`", "",
    "## Submitted", "", submitted,
  ];
  if (p.includeDiagnostics && p.diagnostics) {
    lines.push("", "## Diagnostics", "", "~~~text", p.diagnostics.replace(/~~~/g, "~ ~ ~"), "~~~");
  }
  return lines.join("\n");
}

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/feedback") return reply(404, { error: "not found" });
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (request.method !== "POST") return reply(405, { error: "method not allowed" });

    let payload;
    try {
      const text = await request.text();
      if (text.length > 20000) return reply(400, { error: "payload too large" });
      payload = JSON.parse(text);
    } catch {
      return reply(400, { error: "payload malformed" });
    }
    const invalid = validate(payload);
    if (invalid) return reply(400, { error: invalid });

    // Names only, never values.
    const missing = ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPOSITORY"].filter((k) => !env[k]);
    if (missing.length) {
      console.log(`Missing Worker secrets: ${missing.join(", ")}`);
      return reply(503, { error: "feedback service not configured" });
    }

    // One limiter object per install id, holding its recent sends and the
    // ids it has filed; another per (hashed) address.
    const install = env.LIMITER.get(env.LIMITER.idFromName("install:" + payload.installId.toLowerCase()));
    const begun = await install.begin(payload.id ?? null, PER_INSTALL_PER_DAY, DAY_MS, SEEN_MS);
    if (begun === "duplicate") return reply(409, { error: "already received" });
    if (begun === "limited") return reply(429, { error: "too many submissions" });

    const ip = request.headers.get("CF-Connecting-IP");
    if (ip) {
      const salted = await sha256(ip + ":" + (env.GITHUB_REPOSITORY ?? ""));
      const address = env.LIMITER.get(env.LIMITER.idFromName("ip:" + salted));
      if ((await address.begin(null, PER_ADDRESS_PER_DAY, DAY_MS, SEEN_MS)) === "limited") {
        await install.refund();
        return reply(429, { error: "too many submissions" });
      }
    }

    const submitted = typeof payload.createdUtc === "string" && !isNaN(Date.parse(payload.createdUtc))
      ? new Date(payload.createdUtc).toISOString()
      : new Date().toISOString();

    const github = await fetch(
      `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPOSITORY}/issues`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "clmix-feedback-worker",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          title: `[${payload.category}] ${payload.platform} v${payload.version}`,
          body: issueBody(payload, submitted),
          labels: ["feedback", CATEGORIES[payload.category]],
        }),
      },
    );

    if (!github.ok) {
      // Status only: GitHub's error body can echo the request.
      // 401 bad token, 403 token lacks Issues: write on this repo,
      // 404 owner/repository wrong or the token can't see it.
      console.log(`GitHub issue create failed: HTTP ${github.status} for ${env.GITHUB_OWNER}/${env.GITHUB_REPOSITORY}`);
      await install.refund();
      return reply(502, { error: "could not file feedback" });
    }

    if (payload.id) await install.filed(payload.id);
    return reply(201, { ok: true });
  },
};

// Sliding-window counter, plus the feedback ids already filed. Keeps
// timestamps and ids only - no message, no address.
export class FeedbackLimiter extends DurableObject {
  async begin(feedbackId, limit, windowMs, seenMs) {
    const now = Date.now();
    const seen = (await this.ctx.storage.get("seen")) ?? {};
    for (const [id, at] of Object.entries(seen)) if (now - at > seenMs) delete seen[id];
    await this.ctx.storage.put("seen", seen);
    if (feedbackId && seen[feedbackId]) return "duplicate";

    const sends = ((await this.ctx.storage.get("sends")) ?? []).filter((t) => now - t < windowMs);
    if (sends.length >= limit) {
      await this.ctx.storage.put("sends", sends);
      return "limited";
    }
    sends.push(now);
    await this.ctx.storage.put("sends", sends);
    return "ok";
  }

  // Gives back the last send, for one that was never filed.
  async refund() {
    const sends = (await this.ctx.storage.get("sends")) ?? [];
    sends.pop();
    await this.ctx.storage.put("sends", sends);
  }

  async filed(feedbackId) {
    const seen = (await this.ctx.storage.get("seen")) ?? {};
    seen[feedbackId] = Date.now();
    await this.ctx.storage.put("seen", seen);
  }
}
