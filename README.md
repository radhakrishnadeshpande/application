# OCR App

Web page for Indian-language OCR on the IIIT-H ilocr models.

| Feature | Model | Server route |
|---|---|---|
| Printed text | `shashiocr` | `https://ilocr.iiit.ac.in/ocr/infer` |
| Handwritten text | `V-01.11.00.00` | `https://ilocr.iiit.ac.in/ocr/infer` |
| Table extraction | OhMyTable structure + `V-01.13.01.00` cell OCR (handwritten tables: `V-01.11.00.00`) | `https://ilocr.iiit.ac.in/layout/table` |

## What is in this repository

| Folder / file | What it is | Where it runs |
|---|---|---|
| `index.html` | The web page | GitHub Pages |
| `ocr-proxy/` | Adds CORS headers so the page can call the IIIT servers | Render (only if needed, see below) |
| `server/table/` | Table route `/layout/table` (copy of `layout-parser/server/modules/table`) | Layout server |
| `server/table_docker/` | OhMyTable container `layout:table` (finds tables and cells) | Layout server |

## Is Render needed?

Only because of CORS. A browser page on `github.io` is not allowed to call
`ilocr.iiit.ac.in` unless that server sends CORS headers for your page.

- **IIIT servers send CORS headers for your page:** Render is not needed. Set
  `apiUrl` and `tableUrl` in `index.html` to the `ilocr.iiit.ac.in` URLs.
  See `server/README.md` for the lines to add.
- **They don't:** keep the Render proxy (`ocr-proxy/`). It forwards both
  `/ocr/infer` and `/layout/table`.

## 1. Proxy on Render (skip if CORS is enabled on the IIIT servers)

- New Web Service, Root Directory: `ocr-proxy`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: `ALLOWED_ORIGINS` = `https://<your-username>.github.io,http://localhost:8000`

Check `https://<your-service>.onrender.com/health`.

## 2. Point the page at the APIs

In `index.html`, in the `CONFIG` block:

```js
apiUrl:   "https://<your-service>.onrender.com/ocr/infer",
tableUrl: "https://<your-service>.onrender.com/layout/table",
printedVersion: "shashiocr",
handwrittenVersion: "V-01.11.00.00",
```

Without Render, use `https://ilocr.iiit.ac.in/ocr/infer` and
`https://ilocr.iiit.ac.in/layout/table`.

## 3. Publish the page (GitHub Pages)

Settings → Pages → Deploy from a branch → `main` / `/ (root)`.
The site appears at `https://<your-username>.github.io/<repo-name>/`.

## 4. Table server

See `server/README.md`.

## 5. Reference samples

The **Reference samples** section at the top of the page shows evaluated pages
for three domains: printed documents, handwritten documents and table
extraction. Pick a domain and a language, click one of the five pages to open
it, then press **Read this sample** or **Extract this table**. The sample is
read the same way as an uploaded page (`/ocr/infer` or `/layout/table`), with
the language and writing type set to match the sample. Only the images are
shown. The CRR and WRR used to choose them are not published.

The samples come from `samples/manifest.json`. Build it from the approved
selection summary (CSV or JSON, one row per selected page):

```
domain,language,image_url,crr,wrr,report_url,sample_id,modality,cropped
Printed Documents,Hindi,https://.../page_012.png,98.7,95.2,https://.../report.html,page_012,,
Table Extraction,Telugu,https://.../tbl_3.jpg,96.1,90.4,https://.../report.html,tbl_3,printed,true
```

```
pip install pillow
python tools/build_samples.py selection.csv --check     # fetch every image, report problems, write nothing
python tools/build_samples.py selection.csv --strict    # copy images to samples/<domain>/<lang>/, write the manifest
```

- Required columns: `domain`, `language`, `image_url`. Language names or codes
  both work (`Odia` or `ori`). `crr`, `wrr` and `report_url` may stay in the
  file but are not copied into `samples/`, which is published with the site.
- `modality` (tables only): `printed` (default) or `handwritten`, which picks the table cell model.
- `cropped` (tables only): `true` sends `mode=table`, `false` sends `mode=auto`;
  empty uses the "already cropped" checkbox.
- A URL that cannot be fetched, a file that is not an image, a duplicate image
  or a bad row is listed under "Left out" and the script exits with status 1.
  Nothing is substituted. `--strict` also fails unless there are 7 languages ×
  5 samples per domain.
- Images are copied into the site so the page can read them. With
  `--no-download` the original URLs are kept, which only works if their server
  sends CORS headers.

Commit `samples/` and the page shows the samples. Until then, each domain
says that no samples have been added. Set `samplesUrl: ""` in `CONFIG` to hide
the section.

## Tests

```
pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest tests
```

The tests build a 105-sample fixture selection (synthetic images stamped
"TEST FIXTURE", in a temp folder) with `tools/build_samples.py`. They open
every sample of all 21 domain-language combinations in headless Chromium with
the OCR servers mocked, and check what the page sends. They also check the
existing upload, convert and table workflow. Screenshots and a JSON log go to
`test-output/`. `OCR_LIVE=1` also sends one real sample per domain to the
servers in `CONFIG`.

## Push to GitHub

```
cd ocr-app
git init
git add .
git commit -m "OCR app: web page, CORS proxy, table server"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```
