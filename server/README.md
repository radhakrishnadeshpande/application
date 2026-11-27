# Table server (layout server)

`POST https://ilocr.iiit.ac.in/layout/table`

```
image ──> layout:table container (OhMyTable: table + cell boxes)
      ──> each cell cut into words ──> /ocr/infer (V-01.13.01.00 printed,
                                                   V-01.11.00.00 handwritten)
      ──> {"type": "table", "html": "<table>...</table>"}
```

## Form fields

| Field | Values | Default |
|---|---|---|
| `file` | png, jpg, jpeg, webp, bmp, tif | required |
| `lang` | en, hi, mr, te, ta, kn, ml, gu, pa, bn, ori, asa, mni | `hi` |
| `modality` | `printed`, `handwritten` | `printed` |
| `mode` | `auto` (find the table; none found → 422), `table` (if none found, read the whole image as a table), `page` | `auto` |

## 1. OhMyTable container

```
cd server/table_docker
docker build -t layout:table .
docker run -d --name layout-table --restart unless-stopped \
  -p 127.0.0.1:8890:8890 layout:table
curl http://127.0.0.1:8890/health        # {"ok":true}
```

GPU: build with `--build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu121`
and run with `--gpus all`.

## 2. Route in the layout server

```
cp -r server/table ~/layout-parser/server/modules/
```

In `~/layout-parser/server/app.py`:

```python
from .modules.table.table_routes import router as table_router
...
app.include_router(table_router)
```

Then `pm2 restart layout_api`.

## Settings (environment variables, all optional)

| Variable | Default | What it does |
|---|---|---|
| `TABLE_STRUCTURE_URL` | `http://127.0.0.1:8890/structure` | Where the OhMyTable container is |
| `ILOCR_URL` | `https://ilocr.iiit.ac.in/ocr/infer` | Cell OCR endpoint. An internal address is faster |
| `TABLE_PRINTED_VERSION` | `V-01.13.01.00` | Printed cell model, e.g. `shashiocr` |
| `TABLE_OCR_BATCH_SIZE` | `256` | Cells per OCR request. The OCR server loads the model per request, so bigger is faster |
| `TABLE_OCR_PARALLEL` | `1` | OCR requests at the same time |
| `TABLE_SPLIT_WORDS` | `1` | Cut cells into words (the model reads one word per image) |
| `TABLE_RUPEE_FIX` | `1` | Lone symbol before a number → `₹` |
| `TABLE_NUMERIC_LANG` | empty | Re-read number cells with this language, e.g. `en` |
| `TABLE_OCR_ALLOW_FALLBACK` | `0` | Allow printed cells to fall back to `v2` |
| `TABLE_PAGE_FALLBACK` | `0` | No table found → run page OCR Docker instead of 422 |
| `PAGE_OCR_IMAGE` | `ocr:V-01.13.01.00` | Page OCR Docker image (only with `TABLE_PAGE_FALLBACK=1`) |

## CORS (only if you don't use the Render proxy)

The page on GitHub Pages can call the servers directly if they allow its origin.
In `~/layout-parser/server/app.py`, after `app = FastAPI(...)`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://<your-username>.github.io"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)
```

The main OCR server (`/home/ocr/website/server`) needs the same lines for
`/ocr/infer`. If it already sends CORS headers through nginx, add the origin there instead.
