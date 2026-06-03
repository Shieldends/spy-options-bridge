# Team email protocol (outbound only)

- Subjects: `[SPY-LIVE] STATUS`, `[SPY-LIVE] PERMISSION NEEDED`, `[SPY-LIVE] ACTION DONE`
- Inbox: shieldinc850@gmail.com — user replies **YES** / **NO** to permission emails
- Team reads replies **manually** — do not enable Gmail/IMAP read without explicit user OK
- Implementation: `scripts/team_email.py`
- Render: set same `EMAIL_*` / `SMTP_*` in dashboard → Manual Deploy (`CONFIRM-RENDER-EMAIL.bat` on Desktop)
