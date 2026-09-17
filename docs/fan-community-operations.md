# Fan community pilot

- Sapporo only: nickname 1–12 Unicode code points, message 1–20 (NFC normalized).
- Every submission, including nickname, stays `pending`. Word filtering is supplementary, never an automatic approval mechanism.
- Review names and messages for abuse, personal data, impersonation, ads and off-topic content before approving. A report immediately sets `reported`, hiding the message pending a second review. Do not reapprove without checking the reason.
- No replies, likes or popularity sorting. Newest 30 approved messages are shown. No seeded/fabricated fan posts.
- Moderation requires private database access. There is intentionally no unauthenticated administrative web endpoint.

From a trusted terminal with the existing DATABASE_URL injected:

```
python scripts/moderate_fan_messages.py pending
python scripts/moderate_fan_messages.py approve --id MESSAGE_ID
python scripts/moderate_fan_messages.py reject --id MESSAGE_ID
```

Never run `pending` in public GitHub Actions logs or publish screenshots of the queue. Neon console/authorized database tools may also inspect `fan_messages`; keep unpublished text private. A daily human review is recommended; no automatic human review or guaranteed turnaround is promised.

A browser receives a random deletion token once. Only its hash is stored server-side. It can delete its own submission, even before publication. The browser stores at most 20 deletion receipts. Clearing browser storage loses the receipt; a public post can still be reported for removal. Pending/rejected/reported rows expire after 30 days, approved rows after 180 days, via static-build cleanup (normally twice daily).

# Attention ranking

All 60 club pages are measured with the same code after five visible seconds. Preview hosts and webdriver sessions do not count. Same club + connection source + 30-minute UTC bucket deduplicated on the server, with a five-second cross-club burst limit. This is filtered page attention, not total raw PV or unique people; shared networks can be undercounted. Client-side measurement can miss blocked JS, offline requests or network failures. It does not claim proof against sophisticated manipulation.

No raw IP or device fingerprint is stored by this feature. A database-generated secret HMACs the source address with the event scope and time bucket. Render ingress forwarding is used only in Render; source-address hashes are retained in deduplication keys for at most eight days, rate-limit keys two days. Hosting access logs have the host's own policy. The secret is never sent to clients.

`fan-rankings.json` contains league, club, rank/tie and update time, never counts or identifiers. It is generated alongside static HTML, served by Cloudflare and refreshed twice a day. A zero-count club has no rank. Equal counts share competition ranks (1,1,3). Actual league results are unrelated. API `/api/fan/rankings` permits fresh verification without exposing counts.

HTML and ranking reads remain on Cloudflare. Only background measurement and message actions call the existing Render API. A cold API cannot block static page rendering.
