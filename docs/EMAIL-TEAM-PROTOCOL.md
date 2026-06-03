# Team email protocol (outbound only)

- Subjects: `[SPY Command Center] {status|permission|report|credit}: {short title}`
- Inbox: shieldinc850@gmail.com — user replies **YES** / **NO** to permission emails
- Team reads replies **manually** — do not enable Gmail/IMAP read without explicit user OK
- Implementation: `scripts/team_email.py`
- Recall: each send appends `Email sent: {subject}` to `sync/grok_outbox.md` and `sync/cursor_inbox.md`
- Rate limit: max 1 general email / 5 min; redundant cycle summaries max 1 / 15 min; permission + critical bypass
- Render: set same `EMAIL_*` / `SMTP_*` in dashboard → Manual Deploy (`CONFIRM-RENDER-EMAIL.bat` on Desktop)
- Desktop: `EMAIL-FIRST-TEAM.txt` (10-line team cheat sheet)
