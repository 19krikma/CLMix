# clmix-feedback

Cloudflare Worker behind the apps' Feedback screen. It checks each
submission, rate-limits it, and files it as a GitHub issue. The GitHub
token lives only in this Worker's secrets.

    POST /api/feedback  ->  201 filed | 409 already filed | 400 invalid
                            429 over the limit | 502 GitHub refused

Limits: 10 per install id and 30 per IP address in any 24 hours. The
address is hashed and only kept as a counter. Each submission's `id` is
remembered for 30 days, so a resend after a dropped connection is not
filed twice.

## Setup

1. **GitHub token.** Go to GitHub > Settings > Developer settings >
   Fine-grained tokens and generate a new token:
   - Repository access: only the repository feedback should land in
   - Permissions: Issues = Read and write (Metadata = Read is added
     automatically)

2. **Labels** (once). In that repository, create the labels `feedback`,
   `bug-report`, `feature-request`, `general-feedback` and `other`.
   You can do it under Issues > Labels, or with the token from step 1:

       for l in feedback bug-report feature-request general-feedback other; do
         curl -s -X POST -H "Authorization: Bearer $TOKEN" \
           -H "Accept: application/vnd.github+json" \
           https://api.github.com/repos/OWNER/REPO/labels -d "{\"name\":\"$l\"}"
       done

3. **Deploy** (needs Node.js and a free Cloudflare account), from this
   folder:

       npx wrangler login
       npx wrangler secret put GITHUB_TOKEN
       npx wrangler secret put GITHUB_OWNER
       npx wrangler secret put GITHUB_REPOSITORY
       npx wrangler deploy

   `deploy` prints the URL, e.g.
   `https://clmix.19krikma.workers.dev`.

4. **Point the apps at it.** In `android/gradle.properties`:

       clmix.feedbackUrl=https://clmix.19krikma.workers.dev/api/feedback

5. **Test:**

       curl -i -X POST https://clmix.19krikma.workers.dev/api/feedback \
         -H "Content-Type: application/json" \
         -d '{"category":"Other","message":"Test from curl, please ignore.","version":"0.0.0","platform":"Android","installId":"00000000-0000-4000-8000-000000000000","includeDiagnostics":false,"diagnostics":null}'

   This should return 201, and the issue should appear in the repository.

`npx wrangler tail` shows the Worker's logs live. It logs only GitHub
status codes, never messages or the token.
