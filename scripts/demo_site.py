import asyncio
import html
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

PAGES = Path(__file__).resolve().parents[1] / "samples" / "pages"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

LOGIN_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>تسجيل الدخول</title></head>
<body><main><h1>سجّل دخولك لعرض الإعلان</h1><form method="post"><input name="user"><input type="password" name="pass">
<button>دخول</button></form></main></body></html>"""

SPA_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>App</title></head>
<body><div id="root"></div><script src="/static/app.js"></script></body></html>"""


@app.get("/ads/{name}", response_class=HTMLResponse)
def ad_page(name: str):
    path = (PAGES / name).resolve()
    if path.parent != PAGES.resolve() or path.suffix != ".html" or not path.is_file():
        raise HTTPException(status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/go/{ad_id}")
def short_link(ad_id: str):
    return RedirectResponse(f"/ads/{ad_id}.html", status_code=302)


@app.get("/private/{ad_id}")
def private_ad(ad_id: str):
    return RedirectResponse(f"/login?next=/ads/{html.escape(ad_id)}.html", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login():
    return HTMLResponse(LOGIN_PAGE)


@app.get("/members/{ad_id}")
def members_only(ad_id: str):
    raise HTTPException(status_code=401)


@app.get("/broken")
def broken():
    raise HTTPException(status_code=500)


@app.get("/spa", response_class=HTMLResponse)
def spa():
    return HTMLResponse(SPA_PAGE)


@app.get("/escape-internal")
def escape_internal():
    return RedirectResponse("http://169.254.169.254/latest/meta-data/", status_code=302)


@app.get("/escape-external")
def escape_external():
    return RedirectResponse("https://example.com/", status_code=302)


@app.get("/big", response_class=HTMLResponse)
def big():
    return HTMLResponse("<html><body><p>" + ("إعلان " * 600_000) + "</p></body></html>")


@app.get("/slow", response_class=HTMLResponse)
async def slow():
    await asyncio.sleep(20)
    return HTMLResponse("<html><body>late</body></html>")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8100)
