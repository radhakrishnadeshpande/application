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
