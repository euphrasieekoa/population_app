# -*- coding: utf-8 -*-
import os
import secrets
import datetime
import traceback
import re
import bcrypt
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from models import db, Person, AuditLog
from analysis import compute_all_stats, invalidate_cache
import pandas as pd
from sqlalchemy import or_

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# 🔧 Adaptation environnement : Render (/data) vs Local (./)
if os.path.isdir("/data"):
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////data/population.db"
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///population.db"

db.init_app(app)

with app.app_context():
    db.create_all()

def log_audit(action, code=None):
    try:
        ip = request.remote_addr
        masked = f"{code[:2]}****{code[-2:]}" if code and len(code) >= 4 else "****"
        db.session.add(AuditLog(action=action, code_masked=masked, ip_address=ip))
        db.session.commit()
    except Exception:
        pass

def generate_code():
    return secrets.token_urlsafe(4)[:6].upper()

def hash_code(code):
    return bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_code(code, hashed):
    return bcrypt.checkpw(code.encode('utf-8'), hashed.encode('utf-8'))

@app.route("/")
def index():
    return render_template("index.html", today=datetime.date.today())

@app.route("/submit", methods=["POST"])
def submit():
    if session.get("is_submitting"):
        return redirect(url_for("index"))
    session["is_submitting"] = True

    data = request.form
    errors = []
    try:
        nom = data.get("nom", "").strip().upper()
        if len(nom) < 2:
            errors.append("Nom : min 2 caracteres.")
        elif not re.match(r"^[A-Z\s\-\']+$", nom):
            errors.append("Nom : MAJUSCULES uniquement.")
        if data.get("consent_rgpd") != "true":
            errors.append("Consentement obligatoire.")

        date_str = data.get("date_naissance")
        date_naissance, age = None, None
        today_date = datetime.date.today()
        if date_str:
            try:
                date_naissance = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_naissance > today_date:
                    errors.append("Date invalide (future).")
                else:
                    age = today_date.year - date_naissance.year - ((today_date.month, today_date.day) < (date_naissance.month, date_naissance.day))
                    if age < 0 or age > 120:
                        errors.append("Age invalide (0-120).")
            except ValueError:
                errors.append("Format date invalide.")
        else:
            errors.append("Date obligatoire.")

        tel = data.get("telephone", "").strip()
        if tel and not re.match(r"^\+?[\d\s\-]{7,15}$", tel):
            errors.append("Telephone invalide.")

        def val_int(v, mx=None):
            try:
                val = int(v or 0)
                if mx and val > mx:
                    raise ValueError()
                return val
            except Exception:
                return 0

        def val_float(v):
            try:
                return float(v or 0)
            except Exception:
                return 0.0

        statut_pro = data.get("statut_pro", "")
        rev = val_float(data.get("revenus_usd"))
        if statut_pro in ["Etudiant", "Chomeur", "Inactif"] and rev > 5000:
            errors.append("Incoherence revenus/statut.")

        if errors:
            for e in errors:
                flash(e, "danger")
            session.pop("is_submitting", None)
            return redirect(url_for("index"))

        prenom_val = data.get("prenom", "").strip()

        # 🔒 VÉRIFICATION ANTI-REDONDANCE STRICTE
        existing = Person.query.filter_by(
            nom=nom,
            prenom=prenom_val,
            date_naissance=date_naissance,
            telephone=tel
        ).first()
        if existing:
            flash("⚠️ Enregistrement existant. Utilisez votre code.", "warning")
            session.pop("is_submitting", None)
            return redirect(url_for("index"))

        code = generate_code()
        while Person.query.filter(Person.access_code_hash == hash_code(code)).first():
            code = generate_code()

        p = Person(
            access_code_hash=hash_code(code),
            nom=nom,
            prenom=prenom_val,
            sexe=data.get("sexe"),
            date_naissance=date_naissance,
            age=age,
            nationalite=data.get("nationalite"),
            pays=data.get("pays"),
            situation=data.get("situation"),
            langues=data.get("langues"),
            personnes_a_charge=val_int(data.get("personnes_a_charge")),
            telephone=tel,
            email=data.get("email"),
            adresse=data.get("adresse"),
            quartier=data.get("quartier"),
            zone=data.get("zone"),
            statut_logement=data.get("statut_logement"),
            type_logement=data.get("type_logement"),
            nb_pieces=val_int(data.get("nb_pieces")),
            acces_eau=data.get("acces_eau"),
            acces_electricite=data.get("acces_electricite"),
            niveau_etude=data.get("niveau_etude"),
            statut_pro=statut_pro,
            profession=data.get("profession"),
            type_contrat=data.get("type_contrat"),
            revenus_usd=rev,
            etat_sante=data.get("etat_sante"),
            couverture_sante=data.get("couverture_sante"),
            handicap=(data.get("handicap") == "on"),
            acces_internet=data.get("acces_internet"),
            appareil=data.get("appareil"),
            temps_trajet_min=val_int(data.get("temps_trajet_min")),
            problemes=data.get("problemes"),
            besoins=data.get("besoins"),
            suggestions=data.get("suggestions"),
            satisfaction_globale=val_int(data.get("satisfaction_globale"), 5),
            consent_rgpd=True
        )
        db.session.add(p)
        db.session.commit()
        invalidate_cache()
        session["last_code"] = code
        session.pop("is_submitting", None)
        return redirect(url_for("success"))
    except Exception as e:
        db.session.rollback()
        app.logger.error("Erreur: {}".format(traceback.format_exc()))
        flash("Erreur technique.", "danger")
        session.pop("is_submitting", None)
        return redirect(url_for("index"))

@app.route("/success")
def success():
    code = session.pop("last_code", None)
    if not code:
        return redirect(url_for("index"))
    return render_template("index.html", code=code, submitted=True, today=datetime.date.today())

@app.route("/consult", methods=["GET", "POST"])
def consult():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        if len(code) != 6:
            flash("Format invalide.", "warning")
            log_audit("consult_fail", code)
            return redirect(url_for("consult"))
        found = next((p for p in Person.query.all() if verify_code(code, p.access_code_hash)), None)
        if found:
            session["current_user_id"] = found.id
            log_audit("consult_success", code)
            return redirect(url_for("consult"))
        flash("Code incorrect.", "danger")
        log_audit("consult_fail", code)
        return redirect(url_for("consult"))
    uid = session.get("current_user_id")
    user = db.session.get(Person, uid) if uid else None
    return render_template("consult.html", user=user, stats_loaded=(user is not None), today=datetime.date.today())

@app.route("/view-form", methods=["GET", "POST"])
def view_form():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        if len(code) != 6:
            flash("Format invalide.", "warning")
            return redirect(url_for("view_form"))
        found = next((p for p in Person.query.all() if verify_code(code, p.access_code_hash)), None)
        if found:
            log_audit("view_form_success", code)
            return render_template("view_form.html", person=found)
        flash("Code incorrect.", "danger")
        log_audit("view_form_fail", code)
        return redirect(url_for("view_form"))
    return render_template("view_form.html", person=None)

@app.route("/records")
def records():
    if not session.get("current_user_id"):
        return redirect(url_for("consult"))
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "").strip()
    query = Person.query.order_by(Person.created_at.desc())
    if q:
        query = query.filter(or_(
            Person.nom.contains(q.upper()),
            Person.prenom.contains(q),
            Person.nationalite.contains(q)
        ))
    return render_template("records.html", persons=query.paginate(page=page, per_page=10, error_out=False), search=q)

@app.route("/records/edit/<int:id>", methods=["GET", "POST"])
def edit_record(id):
    if not session.get("current_user_id"):
        return redirect(url_for("consult"))
    p = db.session.get(Person, id)
    if not p or p.id != session["current_user_id"]:
        flash("Acces refuse : vous ne pouvez modifier que votre fiche.", "danger")
        return redirect(url_for("records"))
    if request.method == "POST":
        p.nom = request.form.get("nom", "").strip().upper()
        p.prenom = request.form.get("prenom", "").strip()
        p.nationalite = request.form.get("nationalite", "").strip()
        p.pays = request.form.get("pays", "").strip()
        db.session.commit()
        invalidate_cache()
        flash("Modifie avec succes.", "success")
        return redirect(url_for("records"))
    return render_template("edit.html", person=p)

@app.route("/records/delete/<int:id>", methods=["POST"])
def delete_record(id):
    if not session.get("current_user_id"):
        return redirect(url_for("consult"))
    p = db.session.get(Person, id)
    if not p or p.id != session["current_user_id"]:
        flash("Acces refuse : vous ne pouvez supprimer que votre fiche.", "danger")
        return redirect(url_for("records"))
    db.session.delete(p)
    db.session.commit()
    invalidate_cache()
    flash("Supprime avec succes.", "info")
    return redirect(url_for("records"))

@app.route("/audit")
def audit():
    if not session.get("current_user_id"):
        return redirect(url_for("consult"))
    return render_template("audit.html", logs=AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(page=request.args.get("page", 1, type=int), per_page=20, error_out=False))

@app.route("/api/global-stats")
def api_global_stats():
    try:
        res = compute_all_stats(Person.query.all())
        return jsonify(res), 200
    except Exception:
        return jsonify({"total": 0, "error": "Erreur serveur."}), 200

@app.route("/logout")
def logout():
    session.pop("current_user_id", None)
    flash("Deconnexion.", "info")
    return redirect(url_for("consult"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
# Redeploy jeu. 30 avril 2026 19:31:24 WAT
# Hotfix redeploy jeu. 30 avril 2026 19:42:20 WAT
