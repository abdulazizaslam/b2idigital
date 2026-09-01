# Press-release RSS feed — automatic publishing

Keeps this feed up to date without anyone touching it:

**https://b2idigital.com/hubfs/press-release-feed.xml**

Every hour a free GitHub job reads the press-release blog, rebuilds a clean
full-text feed, and replaces the hosted file in place. The web address never
changes, so the distributor never needs a new link.

## Files

| File | What it does |
|---|---|
| `build_pressrelease_feed.py` | Builds the clean feed from HubSpot's own feed |
| `publish_feed.py` | Builds, compares with what's live, uploads if changed |
| `.github/workflows/press-release-feed.yml` | Runs it hourly |

## One-time setup

### 1. Create a HubSpot token

In HubSpot: **Settings → Integrations → Private Apps → Create private app**

- Name: `Press release feed`
- **Scopes** tab: tick **files** (write access)
- Create, then copy the token

### 2. Put these files in a GitHub repo

The repo can be private. It needs:

```
build_pressrelease_feed.py
publish_feed.py
.github/workflows/press-release-feed.yml
```

### 3. Add the token to the repo

**Settings → Secrets and variables → Actions → New repository secret**

- Name: `HUBSPOT_TOKEN`  (exactly this)
- Value: the token from step 1

### 4. Test it

Open the **Actions** tab, select "Publish press-release RSS feed",
click **Run workflow**. It should finish green.

## Day to day

Nothing. Publish a release to the press-release blog and it appears in the
feed within the hour.

To skip the wait, hit **Run workflow** in the Actions tab right after
publishing.

## Safety features

- Refuses to publish an empty feed, so a HubSpot hiccup can't wipe the file
- Skips the upload when nothing has changed
- After uploading, re-downloads the live feed and checks it parses, has items,
  and contains no tracking pixel, no double-CDATA, and no News content
- Any failure turns the run red and GitHub emails you

## Running it by hand instead

If you'd rather not use GitHub:

```bash
set HUBSPOT_TOKEN=your-token-here
python publish_feed.py
```

Add `--dry-run` to build without uploading, `--force` to upload even when
unchanged.

## Known limits

- Up to one hour's delay unless you trigger it manually
- The feed shows one item because only one post is published to the
  press-release blog; it fills up as you publish more
- `b2idigital.com/press-release/rss.xml` still serves HubSpot's raw feed.
  Pointing it at the clean file needs a URL redirect in
  **Settings → Domains & URLs → URL Redirects**:
  - Original: `https://b2idigital.com/press-release/rss.xml`
  - Redirect to: `https://b2idigital.com/hubfs/press-release-feed.xml`
