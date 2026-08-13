# LEDstudio Events RSS

This project creates an RSS feed for Beekeeper/commUNity from LEDstudio's public event data. An event first enters the feed 14 calendar days before its event date. The feed is rebuilt every six hours and published with GitHub Pages.

## Public addresses

- Post-content preview and status page: `https://vcu-community.github.io/ledstudio-events-rss/`
- RSS feed: `https://vcu-community.github.io/ledstudio-events-rss/rss.xml`
- Source events: `https://ledstudio.vcu.edu/news-and-events/events/`

## Publication rules

The generator reads the public Google Sheet `Main` tab already used by the LEDstudio events page. It uses the `America/New_York` calendar date.

An event appears in the feed when all of these are true:

1. Its release date—event date minus 14 days—has arrived.
2. Its event date has not passed.
3. It has a registration link.

If an eligible event has no registration link, only that event is delayed. It enters the feed on the next successful run after the link is added. Other eligible events continue to publish.

It is valid for the generated feed to contain zero items. It is not valid for the source sheet to contain zero rows or to be missing required columns.

## commUNity post content

Each RSS item includes:

- Title
- Full event date
- Start time and end time when provided
- Location
- Full description
- Link to the anchored event on the LEDstudio website

Registration, resources, tags, and recording links are intentionally excluded from the post body. The registration link is still required in the source before an event can enter the feed. Readers use the single anchored LEDstudio event link to reach registration and any available resources.

The RSS description uses plain text with spaced vertical-bar separators (` | `). commUNity strips RSS description HTML, so explicit text separators preserve readable boundaries even when it collapses paragraph and line-break markup.

## Stable event identity

The RSS GUID prevents commUNity from creating the same post more than once.

The generator uses `EVENT UID` when that column exists and has a value. Otherwise, it uses `EVENT ID ANCHOR`. Once an event has been published, its chosen UID must never change—even if its date, title, or URL changes.

For the strongest rescheduling protection, add an `EVENT UID` column to the sheet and assign a permanent value such as `led-2026-001` when each event is created. Do not calculate that value from the event date or title.

RSS can create a new commUNity post, but it should not be expected to edit or retract a post reliably after import. Handle cancellations or material changes directly in commUNity unless a separate API integration is added later.

## Repository setup

1. Add all files in this project to the root of the public `VCU-Community/ledstudio-events-rss` repository. Preserve the `.github/workflows/publish.yml` path.
2. In the repository, open **Settings → Pages**.
3. Under **Build and deployment**, select **GitHub Actions** as the source.
4. Open **Actions**, select **Test, generate, and publish RSS**, and run the workflow manually if the first push did not start it.
5. Confirm the status page and direct `rss.xml` URL open.
6. Validate the public feed before connecting it to commUNity.

The first live feed can contain every registered upcoming event already within its 14-day window. On August 13, 2026, that initial behavior includes the August 18 and August 27 events.

## Local verification

Python 3.11 or newer is required. The project has no third-party Python dependencies.

```bash
python -m unittest discover -v
python generate_feed.py
```

Generated files are written to `public/index.html` and `public/rss.xml`. The `public` directory is intentionally ignored by Git because GitHub Pages receives it as a workflow artifact.

The generated `index.html` renders the same title and flattened plain-text body placed in each RSS item so it can be used as a content preview. commUNity still controls its own final typography and automatic URL styling.

For a repeatable historical-date check:

```bash
python generate_feed.py --today 2026-08-13
```

For an offline fixture check:

```bash
python generate_feed.py \
  --today 2026-08-13 \
  --source-file tests/fixtures/events_gviz.txt
```

## Failure and warning behavior

The workflow fails without deploying when:

- The sheet request fails.
- The source contains no rows.
- Required columns or event fields are missing.
- A date or time cannot be parsed.
- A URL is unsafe or invalid.
- Two different events have the same GUID.
- The generated RSS is not well-formed XML.

A missing registration link produces a warning and delays that event; it does not fail the whole feed.

## Maintenance

Review the project when the workflow fails, the sheet columns change, the source page is redesigned, or commUNity stops importing items. Enable workflow failure notifications for the repository maintainers.

The workflow uses the current major releases of GitHub's official checkout, Python setup, Pages configuration, Pages artifact, and Pages deployment actions as verified in August 2026.
