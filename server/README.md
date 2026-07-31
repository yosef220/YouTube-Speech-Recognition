---
title: HumFinder Server
emoji: 🎤
colorFrom: purple
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# HumFinder — melody matching server

The stage-2 humming engine for [HumFinder](https://github.com/yosef220/YouTube-Speech-Recognition).
Accepts a short hum/sing recording, extracts its key-invariant melody contour,
matches it against a prebuilt database with subsequence-DTW, and returns the top
candidates with their **exact YouTube video IDs**.

This folder is self-contained and deploys as a **Hugging Face Space (Docker
SDK)** on the free CPU tier — no GPU, no paid services.

## Endpoints

| Method | Path      | Purpose |
| ------ | --------- | ------- |
| GET    | `/health` | `{status, songs}` — how many songs are indexed |
| POST   | `/hum`    | multipart `file=<audio>` → `{matches:[{videoId,title,artist,score,url}]}` |

## Deploy to a free Hugging Face Space

1. Create a new Space → **SDK: Docker** → **Hardware: CPU basic (free)**.
2. Push the contents of this `server/` folder to the Space repo:
   ```bash
   git clone https://huggingface.co/spaces/<user>/<space> hf-space
   cp server/* hf-space/          # Dockerfile, README.md, app.py, melody.py, requirements.txt
   cd hf-space && git add -A && git commit -m "HumFinder server" && git push
   ```
   The Space builds the Dockerfile and comes up at
   `https://<user>-<space>.hf.space`.
3. In the HumFinder web page, open ⚙️ and set **hum server** to that `https://…hf.space`
   URL (it's https, so the GitHub Pages site can call it with no mixed-content block).

## Adding songs (the database)

`/hum` returns **503** until a melody database is present. Build one locally with
[`build_db/ingest.py`](../build_db) and add its two output files to the Space:

- commit `db/melodies.npy` + `db/meta.json` into the Space repo (simplest), **or**
- attach the Space's persistent storage and upload them to `/app/db`.

Keep the DB small and personal (300–800 songs you actually search for) — it
matches far better than trying to cover all of YouTube. See the main README for
the accuracy table and the legal note on how the database is built.
