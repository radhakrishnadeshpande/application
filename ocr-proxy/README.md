# OCR CORS proxy

Forwards requests from the web page to the IIIT servers and adds the CORS headers the browser needs:

- `POST /ocr/infer` → `https://ilocr.iiit.ac.in/ocr/infer` (printed `shashiocr`, handwritten `V-01.11.00.00`)
- `POST /layout/table` → `https://ilocr.iiit.ac.in/layout/table` (table extraction)

Not needed if the IIIT servers already send CORS headers for your page (see `../server/README.md`).

## Test locally first

```
cd ocr-proxy
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

In another terminal:

```
curl http://localhost:8001/health
```

You should see `{"ok":true,...}`. Then test the forwarding path:

```
curl -X POST http://localhost:8001/ocr/infer -H "Content-Type: application/json" -d "{\"imageContent\":[\"...\"],\"modality\":\"handwritten\",\"level\":\"word\",\"language\":\"hi\",\"version\":\"V-01.11.00.00\",\"modelid\":\"\",\"omit\":true,\"meta\":{}}"
```

It should return the same response as the direct curl test: `[{"text":"","meta":{}}]`.

## Deploy on Render

1. Push the `ocr-proxy` folder to a GitHub repository (it can be the same repo as the page, or a separate one).
2. On render.com, click **New → Web Service** and connect the repository.
3. Settings:
   - **Root directory:** `ocr-proxy` (if it's inside a larger repo)
   - **Runtime:** Python
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Under **Environment**, add:
   - `ALLOWED_ORIGINS` = `https://<your-username>.github.io,http://localhost:8000`

   Use the origin only: scheme and domain, no repo path, no trailing slash.
5. Deploy. Your proxy URL will look like `https://ocr-proxy-xxxx.onrender.com`.

Test the table path:

```
curl -X POST http://localhost:8001/layout/table -F "file=@table.png" -F "lang=hi" -F "mode=table"
```

## Point the page at the proxy

In `index.html`, in `CONFIG`:

```js
apiUrl: "https://ocr-proxy-xxxx.onrender.com/ocr/infer",
tableUrl: "https://ocr-proxy-xxxx.onrender.com/layout/table",
```

Then publish the page to GitHub Pages as before.

## Notes

- **Cold starts:** Render's free tier sleeps after inactivity. The first request after a quiet period can take 30 to 60 seconds. Upgrading the plan removes this.
- **Document privacy:** the proxy forwards request bodies without logging them. Still, uploaded documents pass through Render and then IIIT's server, so only use the service for material you're allowed to process there.
- **Timeout:** tables can take a minute or more. The proxy waits up to 300 s (`UPSTREAM_TIMEOUT_SECONDS`).
- **Size limit:** requests larger than 25 MB are rejected. Change `MAX_BODY_BYTES` if needed.
- **Errors:** upstream errors (for example `No model available ...`) are returned unchanged, so the page will show them.
