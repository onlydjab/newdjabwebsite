# DJ AB Website

A single-page site for DJ AB (Auburn, AL), plus a local admin dashboard for editing content.

## Repo structure

```
public/              ← the live static site (this is what gets deployed to GitHub Pages)
  index.html
  sitemap.xml
  robots.txt
  images/
  audio/
  epk/
dashboard.html        ← local admin panel — NOT deployed, NOT public
server.py             ← local-only tool that powers the dashboard
config.json           ← local site config (gitignored — not committed)
config.example.json   ← template for config.json, safe to commit
submissions.json      ← booking-form submissions captured locally (gitignored, contains client PII)
.env                  ← local secrets (gitignored — not committed)
.env.example          ← template for .env, safe to commit
Start Dashboard.command
```

`public/` is the only folder that gets published. `dashboard.html`, `server.py`, `config.json`,
and `submissions.json` stay out of the deployed site entirely — the dashboard is a local editing
tool only, it is never exposed to the internet.

## Running the dashboard locally

1. Copy `.env.example` to `.env` and fill in:
   - `EMAIL_APP_PASSWORD` — a Gmail App Password (create one at
     https://myaccount.google.com/apppasswords, requires 2-Step Verification).
   - `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` — your own login for the dashboard.
     The dashboard refuses every request until `DASHBOARD_PASSWORD` is set.
2. Copy `config.example.json` to `config.json` if it doesn't already exist, and adjust as needed.
3. Run `python3 server.py` (or double-click `Start Dashboard.command`).
4. Visit `http://localhost:3456/` for the site preview, `http://localhost:3456/dashboard`
   for the admin panel (you'll be prompted for the username/password from your `.env`).

No pip installs needed — `server.py` only uses the Python standard library.

### A note on the dashboard and the deployed site

Editing **About/Services/Mixes/Genres/Availability text** through the dashboard writes directly
into `public/index.html`, so those changes are picked up the next time you commit and push.

Editing **photos, the EPK file, or social links** through the dashboard updates `config.json`
(local only) and the files under `public/images` / `public/epk`. Because the deployed site is
static (no backend to read `config.json` at runtime), if you swap an image or the EPK file via
the dashboard, also open `public/index.html` and update the matching `<img src="...">` /
Press-Kit reference by hand before pushing, so the live site matches what you set locally.

## Deploying (GitHub Pages)

1. Create a new GitHub repository (see steps below).
2. From this folder: `git init`, `git add`, `git commit` (already done for you locally — see
   the wrap-up summary for what's been committed).
3. Add the GitHub remote and push:
   ```
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
4. On GitHub: **Settings → Pages** → Source: "Deploy from a branch" → Branch: `main`,
   folder: `/public`. Save.
5. GitHub will publish the site at `https://<your-username>.github.io/<your-repo>/`
   within a minute or two.
6. Once you own `onlydjab.com` (see next section), add it under
   **Settings → Pages → Custom domain**, and GitHub will create a `CNAME` file in `public/`
   for you (commit it if it doesn't show up automatically).

## Contact form (Formspree)

The contact form now posts to Formspree instead of the old `/api/contact` endpoint, since
GitHub Pages can't run a backend.

1. Sign up free at https://formspree.io and create a new form (free tier: 50 submissions/month).
2. Copy the form endpoint it gives you (looks like `https://formspree.io/f/xxxxxxxx`).
3. In `public/index.html`, find the line:
   ```js
   const FORMSPREE_ENDPOINT = 'https://formspree.io/f/YOUR_FORM_ID';
   ```
   and replace `YOUR_FORM_ID` with your real form ID.
4. Formspree will also email new submissions straight to whatever address you signed up with —
   no Gmail App Password / server needed for this on the live site.

## Registering onlydjab.com and pointing it at the site

1. Register `onlydjab.com` through any registrar (Namecheap, Google Domains successor
   Squarespace Domains, Cloudflare Registrar, etc.) — check availability first.
2. In GitHub: **Settings → Pages → Custom domain**, enter `onlydjab.com`. This creates/updates
   a `CNAME` file in `public/` with that domain.
3. At your registrar, add these DNS records (GitHub Pages' current apex IPs):
   ```
   A     @     185.199.108.153
   A     @     185.199.109.153
   A     @     185.199.110.153
   A     @     185.199.111.153
   CNAME www   <your-username>.github.io
   ```
4. Back in GitHub Pages settings, check **Enforce HTTPS** once the certificate provisions
   (can take up to ~24 hrs after DNS propagates).
5. Once live, submit `https://onlydjab.com/sitemap.xml` to Google Search Console and claim
   your Google Business Profile using the new domain (see the SEO audit's recommendations).
