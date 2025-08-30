# biblio_bp.py
from flask import Blueprint, request, jsonify, render_template, abort, Response
from flask_login import current_user, login_required
from sqlalchemy import insert
from biblio import db, Citation
import json

biblio_bp = Blueprint("biblio_bp", __name__, url_prefix="/biblio")

def _uid():
    # Adjust if you don’t use flask-login; for testing you can hardcode 1
    return getattr(current_user, "id", None) or 1

@biblio_bp.get("/")
@login_required
def list_citations():
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()

    query = Citation.query.filter_by(user_id=_uid())
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Citation.title.ilike(like)) | (Citation.authors.ilike(like)) |
            (Citation.venue.ilike(like))  | (Citation.key.ilike(like))
        )
    if tag:
        query = query.filter(Citation.tags.ilike(f"%{tag}%"))

    rows = query.order_by(Citation.updated_at.desc()).all()
    # HTML or JSON depending on Accept
    if "text/html" in request.headers.get("Accept", ""):
        return render_template("biblio/index.html", items=rows, q=q, tag=tag)
    return jsonify([r.to_dict() for r in rows])

@biblio_bp.get("/<key>")
@login_required
def get_citation(key):
    c = Citation.query.filter_by(user_id=_uid(), key=key).first_or_404()
    if "text/html" in request.headers.get("Accept", ""):
        return render_template("biblio/show.html", c=c)
    return jsonify(c.to_dict())

@biblio_bp.post("/save")
@login_required
def save_citation():
    """
    Accepts JSON like:
    {
      "key": "...", "title":"...", "authors":"...", "year":"...",
      "venue":"...", "doi":"...", "url":"...", "tags":"a,b",
      "abstract":"...", "raw":"@article{...}", "csl_json": {...}
    }
    Upsert by (user_id, key).
    """
    data = request.get_json(force=True) or {}
    required = data.get("key")
    if not required:
        return jsonify({"error": "key is required"}), 400

    # Try ON CONFLICT upsert (SQLite / SQLAlchemy 2.x pattern)
    # Fallback to manual merge if your SQLAlchemy/DB doesn’t support it.
    try:
        stmt = insert(Citation).values(
            user_id=_uid(),
            key=data["key"].strip(),
            title=data.get("title"),
            authors=data.get("authors"),
            year=data.get("year"),
            venue=data.get("venue"),
            doi=data.get("doi"),
            url=data.get("url"),
            tags=data.get("tags"),
            abstract=data.get("abstract"),
            raw=data.get("raw"),
            csl_json=data.get("csl_json"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "key"],
            set_={
                "title": stmt.excluded.title,
                "authors": stmt.excluded.authors,
                "year": stmt.excluded.year,
                "venue": stmt.excluded.venue,
                "doi": stmt.excluded.doi,
                "url": stmt.excluded.url,
                "tags": stmt.excluded.tags,
                "abstract": stmt.excluded.abstract,
                "raw": stmt.excluded.raw,
                "csl_json": stmt.excluded.csl_json,
            },
        )
        db.session.execute(stmt)
        db.session.commit()
    except Exception:
        # Portable fallback (no on_conflict):
        db.session.rollback()
        c = Citation.query.filter_by(user_id=_uid(), key=data["key"]).first()
        if not c:
            c = Citation(user_id=_uid(), key=data["key"])
            db.session.add(c)
        for f in ("title","authors","year","venue","doi","url","tags","abstract","raw","csl_json"):
            if f in data: setattr(c, f, data[f])
        db.session.commit()

    return jsonify({"status": "ok", "key": data["key"]})

@biblio_bp.delete("/<key>")
@login_required
def delete_citation(key):
    c = Citation.query.filter_by(user_id=_uid(), key=key).first_or_404()
    db.session.delete(c)
    db.session.commit()
    return jsonify({"status": "deleted", "key": key})

# ---------- Exports ----------
@biblio_bp.get("/export/bibtex")
@login_required
def export_bibtex():
    rows = Citation.query.filter_by(user_id=_uid()).order_by(Citation.key.asc()).all()
    # Prefer the stored raw; otherwise synthesize a minimal BibTeX entry:
    lines = []
    for r in rows:
        if r.raw and r.raw.strip().startswith("@"):
            lines.append(r.raw.strip())
        else:
            lines.append(
                "@misc{%s,\n  title={%s},\n  author={%s},\n  year={%s},\n  howpublished={%s}\n}" % (
                    r.key or "nokey",
                    (r.title or "").replace("{","").replace("}",""),
                    (r.authors or ""),
                    (r.year or ""),
                    (r.url or r.doi or r.venue or "")
                )
            )
    payload = "\n\n".join(lines) + "\n"
    return Response(payload, mimetype="application/x-bibtex",
                    headers={"Content-Disposition":"attachment; filename=citations.bib"})

@biblio_bp.get("/export/csljson")
@login_required
def export_csljson():
    rows = Citation.query.filter_by(user_id=_uid()).all()
    items = []
    for r in rows:
        if r.csl_json:
            items.append(r.csl_json)
        else:
            items.append({
                "id": r.key, "type": "article-journal",
                "title": r.title, "author": [{"literal": r.authors}] if r.authors else [],
                "issued": {"raw": r.year} if r.year else {},
                "DOI": r.doi, "URL": r.url, "container-title": r.venue,
            })
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    return Response(payload, mimetype="application/json",
                    headers={"Content-Disposition":"attachment; filename=citations.json"})

@biblio_bp.get("/export/ris")
@login_required
def export_ris():
    rows = Citation.query.filter_by(user_id=_uid()).all()
    def ris_escape(s): return (s or "").replace("\n"," ").strip()
    chunks = []
    for r in rows:
        chunks.append("\n".join([
            "TY  - GEN",
            f"TI  - {ris_escape(r.title)}",
            f"AU  - {ris_escape(r.authors)}",
            f"PY  - {ris_escape(r.year)}",
            f"JO  - {ris_escape(r.venue)}",
            f"DO  - {ris_escape(r.doi)}",
            f"UR  - {ris_escape(r.url)}",
            "ER  - "
        ]))
    payload = "\n\n".join(chunks) + "\n"
    return Response(payload, mimetype="application/x-research-info-systems",
                    headers={"Content-Disposition":"attachment; filename=citations.ris"})
