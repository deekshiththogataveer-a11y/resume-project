from flask import Flask, render_template, request, redirect, session, send_file
import sqlite3
import re
import io
import json
import urllib.request
import PyPDF2
from PIL import Image
import pytesseract
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

app = Flask(__name__)
app.secret_key = "resume_ai_secure_key_2024"
app.permanent_session_lifetime = timedelta(minutes=60)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

def init_db():
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user')""")
        cur.execute("""CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT, filename TEXT, text TEXT, score REAL,
            ai_feedback TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.commit()
init_db()

SKILLS = {
    "python":5,"java":4,"sql":5,"machine learning":5,"deep learning":5,
    "nlp":4,"data science":5,"html":2,"css":2,"javascript":4,
    "flask":4,"django":4,"react":4,"tensorflow":4,"pytorch":4,"scikit":3,
    "docker":3,"git":2,"linux":2,"aws":3,"azure":3,"kubernetes":3,
    "mongodb":3,"postgresql":3,"redis":2,"c++":3,"typescript":3,"fastapi":3,
}

JOB_ROLES = {
    "Data Scientist":    ["python","machine learning","deep learning","sql","data science","tensorflow","pytorch","scikit"],
    "Web Developer":     ["html","css","javascript","react","flask","django","git"],
    "ML Engineer":       ["python","machine learning","deep learning","tensorflow","pytorch","docker","git"],
    "Backend Developer": ["python","java","sql","flask","django","docker","linux","git"],
    "NLP Engineer":      ["python","nlp","deep learning","machine learning","tensorflow","pytorch"],
    "Full Stack Dev":    ["html","css","javascript","react","flask","django","sql","git"],
    "DevOps Engineer":   ["docker","kubernetes","linux","git","aws","azure"],
    "Cloud Engineer":    ["aws","azure","docker","kubernetes","linux","python"],
}

def allowed_file(f):
    return "." in f and f.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

def ats_score(text):
    text = text.lower()
    total = sum(SKILLS.values())
    got = sum(v for k,v in SKILLS.items() if re.search(rf"\b{re.escape(k)}\b", text))
    return round((got/total)*100,2) if total else 0

def found_skills(text):
    text = text.lower()
    return [k for k in SKILLS if re.search(rf"\b{re.escape(k)}\b", text)]

def missing_skills(text):
    text = text.lower()
    return [k for k in SKILLS if not re.search(rf"\b{re.escape(k)}\b", text)]

def recommend_jobs(text):
    text = text.lower()
    results = []
    for role, keywords in JOB_ROLES.items():
        matched = [k for k in keywords if re.search(rf"\b{re.escape(k)}\b", text)]
        score = round((len(matched)/len(keywords))*100,1)
        results.append({"role":role,"score":score,"matched":matched,"total":len(keywords)})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:4]

def get_label(score):
    if score >= 70: return "Excellent","excellent"
    elif score >= 45: return "Good","good"
    elif score >= 20: return "Average","average"
    else: return "Weak","weak"

def get_ai_feedback(resume_text, score, found, missing):
    prompt = f"""You are an expert resume reviewer. Analyse this resume and respond ONLY with valid JSON.

ATS Score: {score}%
Skills Found: {', '.join(found) if found else 'None'}
Missing Skills: {', '.join(missing[:10]) if missing else 'None'}

Resume:
\"\"\"{resume_text[:3000]}\"\"\"

Respond ONLY with this JSON (no markdown, no extra text):
{{"summary":"2-3 sentence overall assessment","strengths":["s1","s2","s3"],"improvements":["i1","i2","i3"],"missing_skills":["m1","m2","m3"],"tips":["t1","t2","t3"],"score_breakdown":{{"content":75,"keywords":60,"structure":80}}}}"""

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "messages": [{"role":"user","content":prompt}]
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"content-type":"application/json","anthropic-version":"2023-06-01","x-api-key":""},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            raw = data["content"][0]["text"].strip()
            raw = re.sub(r"^```json\s*","",raw); raw = re.sub(r"\s*```$","",raw)
            return json.loads(raw)
    except Exception as e:
        return {
            "summary": "Upload complete. Run AI Feedback to get detailed analysis.",
            "strengths":[],"improvements":[],
            "missing_skills": missing[:5],
            "tips":["Add more technical keywords","Quantify achievements","Keep format clean"],
            "score_breakdown":{"content":int(score),"keywords":int(score*0.8),"structure":70}
        }

def generate_pdf_report(resume):
    buf = io.BytesIO()
    W, H = A4
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm)

    ink   = colors.HexColor("#1a1610")
    cream = colors.HexColor("#f7f3ec")
    gold  = colors.HexColor("#b8922a")
    muted = colors.HexColor("#8a7d6e")
    green = colors.HexColor("#1e7a4a")
    red   = colors.HexColor("#c0392b")
    amber = colors.HexColor("#c47a1e")
    bg    = colors.HexColor("#faf7f1")
    line  = colors.HexColor("#ddd6c8")

    score = resume["score"]
    _, cls = get_label(score)
    sc = {"excellent":green,"good":gold,"average":amber,"weak":red}[cls]

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    cW = W - 40*mm
    story = []

    # header
    hd = Table([[
        Paragraph("<b>Resume<i>AI</i></b>", ps("hl", fontName="Times-BoldItalic", fontSize=16, textColor=ink, leading=20)),
        Paragraph("ATS SCORE REPORT", ps("hr", fontName="Helvetica-Bold", fontSize=8, textColor=muted, leading=10, alignment=TA_RIGHT, letterSpacing=1.5))
    ]], colWidths=[cW*0.55, cW*0.45])
    hd.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
    story.append(hd)
    story.append(HRFlowable(width="100%", thickness=0.6, color=gold, spaceAfter=14))

    # title
    story.append(Paragraph(resume["filename"] or "Resume", ps("ti", fontName="Times-Bold", fontSize=22, textColor=ink, leading=26, spaceAfter=3)))
    story.append(Paragraph(f"Report generated · {resume['uploaded_at'][:10] if resume['uploaded_at'] else 'N/A'}", ps("su", fontName="Helvetica", fontSize=9, textColor=muted, leading=13, spaceAfter=14)))

    # score card
    sc_hex = {"excellent":"1e7a4a","good":"b8922a","average":"c47a1e","weak":"c0392b"}[cls]
    sc_tbl = Table([[
        Paragraph(f"<font size='44' color='#{sc_hex}'><b>{int(score)}%</b></font>", ps("sn", fontName="Times-Bold", fontSize=44, textColor=sc, leading=48, alignment=TA_CENTER)),
        [Paragraph(f"<b>{get_label(score)[0]}</b>", ps("sl", fontName="Helvetica-Bold", fontSize=13, textColor=sc, leading=17)),
         Paragraph("ATS Match Score", ps("sh", fontName="Helvetica", fontSize=9, textColor=muted, leading=12)),
         Spacer(1,6),
         Paragraph(f"File: {resume['filename'] or '—'}", ps("sf", fontName="Helvetica", fontSize=8, textColor=muted, leading=11))]
    ]], colWidths=[cW*0.28, cW*0.72])
    sc_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),bg),
        ("BOX",(0,0),(-1,-1),0.5,line),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),14),("BOTTOMPADDING",(0,0),(-1,-1),14),
        ("LEFTPADDING",(0,0),(0,-1),8),("LEFTPADDING",(1,0),(1,-1),14),
    ]))
    story.append(sc_tbl)
    story.append(Spacer(1,14))

    fb = resume.get("ai_feedback_parsed") or {}

    def h2(txt):
        story.append(Paragraph(txt, ps("h2", fontName="Times-Bold", fontSize=12, textColor=ink, leading=16, spaceBefore=12, spaceAfter=5)))
        story.append(HRFlowable(width="100%", thickness=0.4, color=line, spaceAfter=7))

    def body(txt):
        return Paragraph(txt, ps("bd", fontName="Helvetica", fontSize=9, textColor=ink, leading=13, spaceAfter=4))

    def bul(txt):
        return Paragraph(f"• {txt}", ps("bu", fontName="Helvetica", fontSize=9, textColor=ink, leading=13, spaceAfter=3, leftIndent=10))

    # summary
    if fb.get("summary"):
        h2("Overview")
        story.append(body(fb["summary"]))

    # breakdown
    bd = fb.get("score_breakdown",{})
    if bd:
        h2("Score Breakdown")
        rows = [
            [Paragraph("<b>Category</b>", ps("bh", fontName="Helvetica-Bold", fontSize=9, textColor=muted, leading=12)),
             Paragraph("<b>Score</b>", ps("bh2", fontName="Helvetica-Bold", fontSize=9, textColor=muted, leading=12, alignment=TA_CENTER))],
        ]
        for cat, val in [("Content Quality", bd.get("content","—")), ("Keyword Match", bd.get("keywords","—")), ("Structure", bd.get("structure","—"))]:
            rows.append([body(cat), Paragraph(f"{val}%", ps("bv", fontName="Helvetica-Bold", fontSize=9, textColor=ink, leading=12, alignment=TA_CENTER))])
        bt = Table(rows, colWidths=[cW*0.72, cW*0.28])
        bt.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#ede8de")),
            ("GRID",(0,0),(-1,-1),0.3,line),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",(0,0),(-1,-1),8),
        ]))
        story.append(bt)
        story.append(Spacer(1,8))

    # strengths + improvements 2-col
    if fb.get("strengths") or fb.get("improvements"):
        h2("Strengths & Improvements")
        def col_items(title, items, c):
            out = [Paragraph(title, ps("ch", fontName="Helvetica-Bold", fontSize=9, textColor=c, leading=12, spaceAfter=4))]
            for item in (items or []):
                out.append(bul(item))
            return out
        two = Table([[
            col_items("✓  Strengths",    fb.get("strengths",[]),    green),
            col_items("↑  Improvements", fb.get("improvements",[]), amber),
        ]], colWidths=[(cW-5)/2, (cW-5)/2])
        two.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),0)]))
        story.append(two)
        story.append(Spacer(1,8))

    # tips
    if fb.get("tips"):
        h2("Action Tips")
        for i, tip in enumerate(fb["tips"],1):
            story.append(bul(f"{i}. {tip}"))
        story.append(Spacer(1,8))

    # skills found
    fsk = found_skills(resume.get("text",""))
    if fsk:
        h2("Skills Detected")
        story.append(body(", ".join(fsk)))
        story.append(Spacer(1,6))

    # footer
    story.append(Spacer(1,14))
    story.append(HRFlowable(width="100%", thickness=0.4, color=line, spaceAfter=6))
    story.append(Paragraph("Generated by ResumeAI · AI-powered resume screening platform",
        ps("ft", fontName="Helvetica", fontSize=7.5, textColor=muted, leading=11)))

    doc.build(story)
    buf.seek(0)
    return buf


@app.route("/")
def home(): return redirect("/login")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","")
        with sqlite3.connect("database.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email=?", (email,))
            user = cur.fetchone()
        if user and check_password_hash(user[2], password):
            session.permanent = True
            session["user"] = email
            session["role"] = user[3]
            return redirect("/dashboard")
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email","").strip()
        password = request.form.get("password","")
        if not email or not password:
            return render_template("register.html", error="All fields are required.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")
        hashed = generate_password_hash(password)
        role = "f" if email == "admin@gmail.com" else "user"
        try:
            with sqlite3.connect("database.db") as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO users(email,password,role) VALUES(?,?,?)", (email,hashed,role))
                conn.commit()
            return redirect("/login")
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Email already registered.")
    return render_template("register.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session: return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM resumes WHERE email=? ORDER BY uploaded_at DESC", (session["user"],))
        rows = cur.fetchall()
    resumes = []
    for r in rows:
        label, cls = get_label(r[4])
        fb_parsed = None
        if r[5]:
            try: fb_parsed = json.loads(r[5])
            except: pass
        resumes.append({
            "id":r[0],"email":r[1],"filename":r[2],"text":r[3],"score":r[4],
            "ai_feedback":r[5],"ai_feedback_parsed":fb_parsed,
            "label":label,"cls":cls,"uploaded_at":r[6],
            "jobs":recommend_jobs(r[3] or ""),
            "found_skills":found_skills(r[3] or ""),
            "missing_skills":missing_skills(r[3] or "")[:8],
        })
    return render_template("dashboard.html", resumes=resumes, user=session["user"], role=session.get("role","user"))

@app.route("/upload", methods=["POST"])
def upload():
    if "user" not in session: return redirect("/login")
    file = request.files.get("resume")
    if not file or file.filename == "" or not allowed_file(file.filename):
        return redirect("/dashboard")
    text = ""
    try:
        if file.filename.lower().endswith(".pdf"):
            pdf = PyPDF2.PdfReader(file)
            for p in pdf.pages:
                t = p.extract_text()
                if t: text += t
        else:
            text = pytesseract.image_to_string(Image.open(file))
    except: return redirect("/dashboard")
    score = ats_score(text)
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO resumes(email,filename,text,score) VALUES(?,?,?,?)",
                    (session["user"], file.filename, text, score))
        conn.commit()
    return redirect("/dashboard")

@app.route("/ai-feedback/<int:rid>")
def ai_feedback(rid):
    if "user" not in session: return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM resumes WHERE id=? AND email=?", (rid, session["user"]))
        row = cur.fetchone()
    if not row: return redirect("/dashboard")
    if not row[5]:
        fsk = found_skills(row[3] or "")
        msk = missing_skills(row[3] or "")
        fb  = get_ai_feedback(row[3] or "", row[4], fsk, msk)
        with sqlite3.connect("database.db") as conn:
            cur = conn.cursor()
            cur.execute("UPDATE resumes SET ai_feedback=? WHERE id=?", (json.dumps(fb), rid))
            conn.commit()
    return redirect("/dashboard")

@app.route("/report/<int:rid>")
def download_report(rid):
    if "user" not in session: return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM resumes WHERE id=? AND email=?", (rid, session["user"]))
        row = cur.fetchone()
    if not row: return redirect("/dashboard")
    fb_parsed = None
    if row[5]:
        try: fb_parsed = json.loads(row[5])
        except: pass
    resume = {"id":row[0],"email":row[1],"filename":row[2],"text":row[3],"score":row[4],"ai_feedback_parsed":fb_parsed,"uploaded_at":row[6]}
    buf = generate_pdf_report(resume)
    safe = re.sub(r"[^\w\-.]","_", resume["filename"] or "resume")
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=f"ATS_Report_{safe}.pdf")

@app.route("/analytics")
def analytics():
    if "user" not in session: return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT score,uploaded_at,filename FROM resumes WHERE email=? ORDER BY uploaded_at ASC", (session["user"],))
        rows = cur.fetchall()
    if not rows:
        return render_template("analytics.html", user=session["user"], has_data=False)
    scores = [r[0] for r in rows]
    dates  = [r[1][:10] if r[1] else "" for r in rows]
    fnames = [r[2] or "Resume" for r in rows]
    avg    = round(sum(scores)/len(scores),1)
    buckets = {"Weak":0,"Average":0,"Good":0,"Excellent":0}
    for s in scores:
        if s < 20: buckets["Weak"]+=1
        elif s < 45: buckets["Average"]+=1
        elif s < 70: buckets["Good"]+=1
        else: buckets["Excellent"]+=1
    all_fsk = {}
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT text FROM resumes WHERE email=?", (session["user"],))
        for (txt,) in cur.fetchall():
            for sk in found_skills(txt or ""):
                all_fsk[sk] = all_fsk.get(sk,0)+1
    top_skills = sorted(all_fsk.items(), key=lambda x:x[1], reverse=True)[:8]
    return render_template("analytics.html",
        user=session["user"], has_data=True,
        scores=json.dumps(scores), dates=json.dumps(dates), fnames=json.dumps(fnames),
        dist_labels=json.dumps(list(buckets.keys())),
        dist_data=json.dumps(list(buckets.values())),
        skill_labels=json.dumps([s[0] for s in top_skills]),
        skill_data=json.dumps([s[1] for s in top_skills]),
        avg=avg, best=max(scores), latest=scores[-1], count=len(scores),
    )

@app.route("/admin")
def admin():
    if session.get("role") != "admin": return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users"); total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM resumes"); total_resumes = cur.fetchone()[0]
        cur.execute("SELECT AVG(score) FROM resumes"); avg_score = cur.fetchone()[0] or 0
        cur.execute("SELECT id,email,filename,score,uploaded_at FROM resumes ORDER BY uploaded_at DESC")
        data = cur.fetchall()
        cur.execute("SELECT email,role FROM users ORDER BY id DESC")
        users_list = cur.fetchall()
    enriched = []
    for row in data:
        label,cls = get_label(row[3])
        enriched.append({"id":row[0],"email":row[1],"filename":row[2],"score":row[3],"label":label,"cls":cls,"uploaded_at":row[4]})
    return render_template("admin.html", total_users=total_users, total_resumes=total_resumes,
        avg_score=round(avg_score,2), data=enriched, users_list=users_list)

@app.route("/delete/<int:rid>")
def delete_resume(rid):
    if session.get("role") != "admin": return redirect("/login")
    with sqlite3.connect("database.db") as conn:
        cur = conn.cursor(); cur.execute("DELETE FROM resumes WHERE id=?", (rid,)); conn.commit()
    return redirect("/admin")

@app.route("/logout")
def logout():
    session.clear(); return redirect("/login")

if __name__ == "__main__":
    app.run(debug=True)