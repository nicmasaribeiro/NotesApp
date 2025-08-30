# biblio.py
# biblio.py
from __future__ import annotations
import os, re, json, datetime as dt
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, abort
from werkzeug.utils import secure_filename
from sqlalchemy import or_
from extensions import db   # ← IMPORTANT: no import from app
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON

biblio = Blueprint("biblio", __name__, template_folder="templates")

UPLOAD_DIR = os.path.join(os.getcwd(), "uploads", "papers")
os.makedirs(UPLOAD_DIR, exist_ok=True)

class BibEntry(db.Model):
    __tablename__ = "bib_entries"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(256), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    authors = db.Column(db.Text, nullable=False)
    venue = db.Column(db.String(512))
    year = db.Column(db.Integer)
    doi = db.Column(db.String(256))
    url = db.Column(db.String(1024))
    abstract = db.Column(db.Text)
    tags = db.Column(db.String(512))
    file_path = db.Column(db.String(1024))
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

class Citation(db.Model):
    __tablename__ = "citations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, index=True, nullable=False)  # FK to Users.id if you have it
    key = db.Column(db.String(128), nullable=False)               # e.g., "sutton1998reinforcement"
    title = db.Column(db.String(512), nullable=True)
    authors = db.Column(db.String(1024), nullable=True)           # "Surname, Name; Surname2, Name2"
    year = db.Column(db.String(16), nullable=True)
    venue = db.Column(db.String(512), nullable=True)
    doi = db.Column(db.String(256), nullable=True)
    url = db.Column(db.String(1024), nullable=True)
    tags = db.Column(db.String(512), nullable=True)               # comma- or semicolon-separated
    abstract = db.Column(db.Text, nullable=True)
    # Store a full raw blob for lossless export (BibTeX/CSL/RIS) and easy rebuilds:
    raw = db.Column(db.Text, nullable=True)                       # original BibTeX/RIS/etc. text
    csl_json = db.Column(SQLITE_JSON, nullable=True)              # normalized CSL-JSON if you have it

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_citations_user_key"),
        Index("ix_citations_user_key", "user_id", "key"),
        Index("ix_citations_user_title", "user_id", "title"),
        Index("ix_citations_user_tags", "user_id", "tags"),
    )

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "key": self.key,
            "title": self.title, "authors": self.authors, "year": self.year, "venue": self.venue,
            "doi": self.doi, "url": self.url, "tags": self.tags, "abstract": self.abstract,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
        }
# … keep the rest of your routes/helpers exactly as before …

# --- helpers -----------------------------------------------------------------

def parse_authors(authors_str: str):
    # expected: "Last, First; Last, First"
    parts = [a.strip() for a in authors_str.split(";") if a.strip()]
    return parts

def cite_apa(e: BibEntry) -> str:
    # Very light APA style approximation
    # "Last, F., & Last, F. (Year). Title. Venue. DOI/URL"
    def fmt_author(a):
        # "Last, First Middle" -> "Last, F. M."
        if "," in a:
            last, firsts = [s.strip() for s in a.split(",", 1)]
        else:
            parts = a.split()
            last, firsts = parts[-1], " ".join(parts[:-1])
        initials = " ".join([f"{p[0]}." for p in firsts.split() if p])
        return f"{last}, {initials}".strip().rstrip(",")
    A = parse_authors(e.authors)
    if len(A) > 1: auth = ", & ".join([fmt_author(a) for a in A[:-1]]) + f", & {fmt_author(A[-1])}"
    else: auth = fmt_author(A[0]) if A else ""
    year = f"({e.year})." if e.year else "(n.d.)."
    tail = e.doi or e.url or ""
    venue = f"{e.venue}." if e.venue else ""
    return f"{auth} {year} {e.title}. {venue} {tail}".strip()

def cite_mla(e: BibEntry) -> str:
    # Light MLA approximation
    # "Last, First, et al. 'Title.' Venue, Year, DOI/URL."
    A = parse_authors(e.authors)
    primary = A[0] if A else ""
    rest = " et al." if len(A) > 1 else ""
    year = f", {e.year}" if e.year else ""
    venue = f"{e.venue}" if e.venue else ""
    tail = e.doi or e.url or ""
    return f"{primary}{rest}. “{e.title}.” {venue}{year}. {tail}".strip()

def cite_chicago(e: BibEntry) -> str:
    # Light Chicago approximation
    # "Last, First. 'Title.' Venue (Year). DOI/URL."
    A = parse_authors(e.authors)
    primary = A[0] if A else ""
    year = f" ({e.year})" if e.year else ""
    venue = f"{e.venue}" if e.venue else ""
    tail = e.doi or e.url or ""
    return f"{primary}. “{e.title}.” {venue}{year}. {tail}".strip()

STYLE_MAP = {"apa": cite_apa, "mla": cite_mla, "chicago": cite_chicago}

BIB_ENTRY_RE = re.compile(r"@\w+\s*{\s*([^,]+),([\s\S]*?)\n}\s*", re.MULTILINE)
FIELD_RE = re.compile(r"(\w+)\s*=\s*[{\"]([\s\S]*?)[\"]\s*,", re.MULTILINE)

def parse_bibtex(text: str):
    items = []
    for m in BIB_ENTRY_RE.finditer(text):
        key = m.group(1).strip()
        fields = dict((k.lower(), v.strip()) for k, v in FIELD_RE.findall(m.group(2)))
        items.append((key, fields))
    return items

def to_bibtex(e: BibEntry) -> str:
    # Minimal BibTeX emit (as @misc)
    fields = {
        "title": e.title,
        "author": " and ".join([a.strip() for a in e.authors.split(";") if a.strip()]),
        "year": str(e.year) if e.year else "",
        "howpublished": e.venue or "",
        "doi": e.doi or "",
        "url": e.url or "",
        "note": (e.tags or "")
    }
    lines = [f'  {k} = {{{v}}},' for k, v in fields.items() if v]
    return "@misc{%s,\n%s\n}" % (e.key, "\n".join(lines))

# --- routes ------------------------------------------------------------------
@biblio.route("/")
def home():
    return render_template("bib_index.html")

@biblio.route("/biblio", methods=["GET"])
def index():
    q = request.args.get("q", "").strip()
    year = request.args.get("year", "").strip()
    query = BibEntry.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(BibEntry.title.ilike(like),
                                 BibEntry.authors.ilike(like),
                                 BibEntry.venue.ilike(like),
                                 BibEntry.tags.ilike(like)))
    if year:
        query = query.filter(BibEntry.year == int(year))
    entries = query.order_by(BibEntry.updated_at.desc()).all()

    # year facet options
    years = [y for (y,) in db.session.query(BibEntry.year).filter(BibEntry.year.isnot(None)).distinct().order_by(BibEntry.year.desc()).all()]
    return render_template("bib_index.html", entries=entries, years=years, q=q, year=year)

def _unique_key(base_key: str) -> str:
    """
    Find an available unique key by appending -2, -3, ... if needed.
    """
    candidate = base_key
    i = 2
    while BibEntry.query.filter_by(key=candidate).first() is not None:
        candidate = f"{base_key}-{i}"
        i += 1
    return candidate

@biblio.route("/create", methods=["POST"])
def create():
    on_conflict = (request.form.get("on_conflict") or "update").lower()
    raw_key = request.form["key"].strip()

    # Handle file upload (optional)
    f = request.files.get("pdf")
    stored = None
    if f and f.filename:
        fn = secure_filename(f.filename)
        stored = f"{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{fn}"
        f.save(os.path.join(UPLOAD_DIR, stored))

    # Prepare fields
    payload = dict(
        title=request.form["title"].strip(),
        authors=request.form["authors"].strip(),
        venue=(request.form.get("venue") or "").strip() or None,
        year=int(request.form["year"]) if request.form.get("year") else None,
        doi=(request.form.get("doi") or "").strip() or None,
        url=(request.form.get("url") or "").strip() or None,
        abstract=(request.form.get("abstract") or "").strip() or None,
        tags=(request.form.get("tags") or "").strip() or None,
    )

    existing = BibEntry.query.filter_by(key=raw_key).first()

    if existing:
        if on_conflict == "skip":
            flash(f"Entry with key '{raw_key}' already exists — skipped.", "warning")
            return redirect(url_for("biblio.index"))

        if on_conflict == "newkey":
            # allocate a new unique key and insert a fresh row
            new_key = _unique_key(raw_key)
            e = BibEntry(key=new_key, **payload, file_path=stored)
            db.session.add(e)
            db.session.commit()
            flash(f"Duplicate key. Created as '{new_key}'.", "success")
            return redirect(url_for("biblio.index"))

        # default: update existing
        existing.title = payload["title"]
        existing.authors = payload["authors"]
        existing.venue = payload["venue"]
        existing.year = payload["year"]
        existing.doi = payload["doi"]
        existing.url = payload["url"]
        existing.abstract = payload["abstract"]
        existing.tags = payload["tags"]
        if stored:
            # optional: delete previous file if you want to avoid orphan files
            if existing.file_path:
                try:
                    os.remove(os.path.join(UPLOAD_DIR, existing.file_path))
                except Exception:
                    pass
            existing.file_path = stored

        db.session.commit()
        flash(f"Updated existing entry '{raw_key}'.", "success")
        return redirect(url_for("biblio.index"))

    # No existing: normal insert
    e = BibEntry(key=raw_key, **payload, file_path=stored)
    db.session.add(e)
    try:
        db.session.commit()
        flash("Entry added.", "success")
    except Exception as ex:
        db.session.rollback()
        flash(f"Error adding entry: {ex}", "danger")
    return redirect(url_for("biblio.index"))


@biblio.route("/biblio/delete/<int:entry_id>")
def delete(entry_id):
    e = BibEntry.query.get_or_404(entry_id)
    if e.file_path:
        try: os.remove(os.path.join(UPLOAD_DIR, e.file_path))
        except Exception: pass
    db.session.delete(e)
    db.session.commit()
    flash("Entry deleted.", "success")
    return redirect(url_for("biblio.index"))

@biblio.route("/biblio/download/<int:entry_id>")
def download_pdf(entry_id):
    e = BibEntry.query.get_or_404(entry_id)
    if not e.file_path: abort(404)
    return send_from_directory(UPLOAD_DIR, e.file_path, as_attachment=True)

@biblio.route("/biblio/cite/<int:entry_id>")
def cite(entry_id):
    style = request.args.get("style", "apa").lower()
    fn = STYLE_MAP.get(style, cite_apa)
    e = BibEntry.query.get_or_404(entry_id)
    return fn(e)

@biblio.route("/biblio/export")
def export_all():
    fmt = request.args.get("fmt", "json")
    entries = BibEntry.query.order_by(BibEntry.key.asc()).all()
    if fmt == "bib":
        bib = "\n\n".join([to_bibtex(e) for e in entries])
        return bib, 200, {"Content-Type": "text/plain; charset=utf-8"}
    # default json
    data = [{
        "key": e.key, "title": e.title, "authors": e.authors, "venue": e.venue,
        "year": e.year, "doi": e.doi, "url": e.url, "abstract": e.abstract,
        "tags": e.tags, "file_path": bool(e.file_path)
    } for e in entries]
    return json.dumps(data, indent=2), 200, {"Content-Type": "application/json; charset=utf-8"}

@biblio.route("/biblio/import", methods=["POST"])
def import_bib():
    f = request.files.get("bibfile")
    if not f: 
        flash("No file provided.", "danger")
        return redirect(url_for("biblio.index"))
    text = f.read().decode("utf-8", errors="replace")
    items = parse_bibtex(text)
    added = 0
    for key, fields in items:
        title = fields.get("title") or ""
        authors_raw = fields.get("author", "")
        authors = "; ".join([a.strip() for a in authors_raw.replace(" and ", "; ").split(";") if a.strip()])
        venue = fields.get("journal") or fields.get("booktitle") or fields.get("howpublished") or None
        year  = fields.get("year")
        try: year = int(year) if year else None
        except: year = None
        doi = fields.get("doi") or None
        url = fields.get("url") or None
        if not title: continue
        exists = BibEntry.query.filter_by(key=key).first()
        if exists: 
            # update minimal fields if missing
            exists.title = exists.title or title
            exists.authors = exists.authors or authors
            exists.venue = exists.venue or venue
            exists.year = exists.year or year
            exists.doi = exists.doi or doi
            exists.url = exists.url or url
        else:
            db.session.add(BibEntry(
                key=key, title=title, authors=authors, venue=venue,
                year=year, doi=doi, url=url
            ))
        added += 1
    db.session.commit()
    flash(f"Imported/updated {added} entries.", "success")
    return redirect(url_for("biblio.index"))
