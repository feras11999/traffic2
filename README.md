# Route 343 GTFS-Realtime Collector

This repository collects TfNSW GTFS-Realtime vehicle-position data for Sydney bus route 343 using GitHub Actions. Your computer does not need to remain on.

## Before running

1. Open the repository on GitHub.
2. Go to **Settings → Secrets and variables → Actions**.
3. Create a repository secret named exactly:

   `TFNSW_API_KEY`

4. Paste your TfNSW Open Data API key as the value.

Never put the API key inside the Python file.

## Upload these files

Upload the complete project while preserving this path:

```text
.github/workflows/collect-route343.yml
```

GitHub may hide folders beginning with a dot on some upload screens. The folder must still be present in the repository after upload.

## Start collection now

1. Open **Actions**.
2. Select **Collect Route 343**.
3. Click **Run workflow**.
4. Select the `main` branch.
5. Click the green **Run workflow** button.

The workflow also starts every six hours in Sydney time. Each run lasts up to 340 minutes and uploads its CSV as a workflow artifact.

## Download collected data

1. Open **Actions**.
2. Open a completed workflow run.
3. Scroll to **Artifacts**.
4. Download the `route343-...` ZIP.

## Collection settings

The workflow currently uses:

- Route: 343
- Poll interval: 15 seconds
- Stop date: Sunday 26 July 2026 at 11:00 PM Sydney time
- Maximum runtime per GitHub job: 340 minutes

To change the final stop time, edit:

```yaml
STOP_AT_SYDNEY: "2026-07-26T23:00:00"
```

## Important limitation

Scheduled GitHub Actions jobs can be delayed during periods of high demand. For an exact assessment collection window, manually start the workflow shortly before the required time and stop/cancel it after the window, or use the timestamp fields to filter the downloaded CSV.
