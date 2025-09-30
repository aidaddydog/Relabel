import os, uvicorn

host = os.environ.get("HOST", "0.0.0.0")
port = int(os.environ.get("PORT", "8000"))

uvicorn.run("app.main:app", host=host, port=port, reload=False, workers=1)
