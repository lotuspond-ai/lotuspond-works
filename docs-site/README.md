# LotusPond Works documentation site

The site uses pinned MkDocs 1.6.1 and only its bundled theme/assets, so the generated site can be served from GitHub Pages, an internal static host, or an offline web server.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/mkdocs build --strict -f mkdocs.yml
```

Preview with `.venv/bin/mkdocs serve -f mkdocs.yml`. Build output is `site/` and is intentionally ignored by Git.

For GitHub Pages, use a protected Actions deployment that builds with `--strict`, uploads `site/` with the official Pages artifact action, and deploys only from the default branch after review. The workflow is provided in `.github/workflows/docs.yml`. After connecting the repository and configuring Pages/its protected environment, set the repository variable `LOTUSPOND_DOCS_PUBLISH=true` to enable publication on documentation changes to main. Configure your custom subdomain in the hosting platform separately. No remote, Pages setting, or DNS record has been changed by this local work.
