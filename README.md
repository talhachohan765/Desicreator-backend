# DesiCreator Backend — free video-processing API

FastAPI + ffmpeg. Async jobs: `POST /process` -> `GET /job/{id}` -> `GET /download/{id}`.
Ops: `compress`, `trim`, `convert`, `speed`, `mute`, `extract-audio`.

## Deploy: Render (free, NO card needed) — recommended first try
1. render.com par account banao (email se, card nahi mangta).
2. New > Blueprint > is repo/folder ka `render.yaml` lagao, ya manually:
   New Web Service > Docker > is folder ko GitHub repo mein daal kar connect karo.
3. Deploy ke baad service URL milega, jaise `https://desicreator-backend.onrender.com`.
4. Dashboard > Environment mein `BACKEND_API_KEY` ki value copy karo — ye frontend mein lagegi.
5. Test: `GET <url>/health` -> `{"ok": true}`.

Note: free tier 15 min idle ke baad sota hai; pehli request 30-60 sec legi (cold start).

## Deploy: Modal ($30/mo free credits, card on file needed)
1. modal.com par account banao, card add karo.
2. `pip install modal`, phir:
   `modal deploy` ek `modal_app.py` ke saath jo is Dockerfile wali image use kare
   (ya Modal docs: custom container via `modal.Image.from_dockerfile("Dockerfile")`).
3. $30/month ke andar raho to $0 charge. Usage dashboard par nazar rakho.

## Frontend wiring (baad mein)
`assets/js/backend-config.js` mein `BACKEND_URL` + `BACKEND_API_KEY` set karo,
phir 7 video tools (`video-compressor`, `video-trimmer`, `speed-control`,
`silence-remover`, `video-reframer`, `color-filters`, `caption-styler`) is API ko
call karenge — pehle browser mein ffmpeg try, fail ho to backend fallback.

## Local test
```
pip install -r requirements.txt
BACKEND_API_KEY=test uvicorn app:app --port 8000
curl localhost:8000/health
```
