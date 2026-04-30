import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score
from datetime import datetime, timedelta
import threading

class StatsCache:
    def __init__(self, ttl_seconds=300):
        self._cache = {}
        self._timestamps = {}
        self._lock = threading.Lock()
        self._ttl = timedelta(seconds=ttl_seconds)
    def get(self, key):
        with self._lock:
            if key in self._cache and datetime.now() - self._timestamps[key] < self._ttl: return self._cache[key]
            self._cache.pop(key, None); self._timestamps.pop(key, None)
        return None
    def set(self, key, value):
        with self._lock: self._cache[key] = value; self._timestamps[key] = datetime.now()
    def clear(self):
        with self._lock: self._cache.clear(); self._timestamps.clear()

cache = StatsCache(ttl_seconds=300)

def get_global_stats(df):
    if df.empty: return {"total": 0, "age_moyen": 0, "mediane_revenus_usd": 0, "sexe_dist": {}, "etude_dist": {}, "situation_dist": {}}
    df = df.copy()
    df["date_naissance"] = pd.to_datetime(df["date_naissance"], errors="coerce")
    df = df.dropna(subset=["date_naissance"])
    if df.empty: return {"total": 0, "age_moyen": 0, "mediane_revenus_usd": 0, "sexe_dist": {}, "etude_dist": {}, "situation_dist": {}}
    df["age"] = (datetime.now() - df["date_naissance"]).dt.days // 365
    age_mean = df["age"].mean()
    rev_median = df["revenus_usd"].median()
    return {
        "total": int(len(df)),
        "age_moyen": round(float(age_mean), 1) if pd.notna(age_mean) else 0,
        "mediane_revenus_usd": round(float(rev_median), 2) if pd.notna(rev_median) else 0,
        "sexe_dist": {str(k): int(v) for k, v in df["sexe"].dropna().value_counts().items()},
        "etude_dist": {str(k): int(v) for k, v in df["niveau_etude"].dropna().value_counts().items()},
        "situation_dist": {str(k): int(v) for k, v in df["situation"].dropna().value_counts().items()}
    }

def predict_revenues(df):
    default = {"r2": 0.0, "pred_master_usd": 0.0, "coeff": 0.0, "message": "Donnees insuffisantes"}
    if df.empty: return default
    valid = df.dropna(subset=["niveau_etude", "revenus_usd"]).copy()
    if len(valid) < 5: return default
    mapping = {"Aucun": 0, "Primaire": 1, "Secondaire": 2, "Licence": 3, "Master+": 4}
    valid = valid[valid["niveau_etude"].isin(mapping.keys())].copy()
    if len(valid) < 5: return default
    valid["niveau_code"] = valid["niveau_etude"].map(mapping)
    X, y = valid[["niveau_code"]], valid["revenus_usd"]
    try:
        if len(X) < 10:
            model = LinearRegression().fit(X, y)
            return {"r2": 0.0, "pred_master_usd": round(float(model.predict([[3]])[0]), 2), "coeff": round(float(model.coef_[0]), 2), "message": "Prediction indicative"}
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        model = LinearRegression().fit(X_train, y_train)
        return {"r2": round(float(r2_score(y_test, model.predict(X_test))), 3), "pred_master_usd": round(float(model.predict([[3]])[0]), 2), "coeff": round(float(model.coef_[0]), 2), "message": "Prediction fiable"}
    except Exception: return default

def project_demographics(df):
    default_proj = {"years": [], "hommes": [], "femmes": [], "autres": [], "message": "Donnees insuffisantes"}
    if df.empty or "sexe" not in df.columns: return default_proj
    sexe_counts = df["sexe"].dropna().value_counts()
    if len(sexe_counts) == 0: return default_proj
    growth_rate = 0.0105
    years = list(range(0, 101, 10))
    return {
        "years": years,
        "hommes": [int(sexe_counts.get("Homme", 0) * ((1 + growth_rate) ** y)) for y in years],
        "femmes": [int(sexe_counts.get("Femme", 0) * ((1 + growth_rate) ** y)) for y in years],
        "autres": [int(sexe_counts.get("Autre", 0) * ((1 + growth_rate) ** y)) for y in years],
        "message": f"Projection basee sur un taux de croissance annuel de {growth_rate*100:.2f}%"
    }

def get_education_curve(df):
    default_curve = {"levels": [], "counts": [], "cumulative": [], "percentages": [], "total": 0, "message": "Donnees insuffisantes"}
    if df.empty or "niveau_etude" not in df.columns: return default_curve
    level_order = ["Aucun", "Primaire", "Secondaire", "Licence", "Master+"]
    etude_counts = df["niveau_etude"].dropna().value_counts()
    levels, counts, cumulative, percentages = [], [], [], []
    total = sum(int(etude_counts.get(l, 0)) for l in level_order)
    running_total = 0
    for level in level_order:
        count = int(etude_counts.get(level, 0))
        running_total += count
        levels.append(level); counts.append(count); cumulative.append(running_total)
        percentages.append(round((running_total / total * 100) if total > 0 else 0, 1))
    return {"levels": levels, "counts": counts, "cumulative": cumulative, "percentages": percentages, "total": total, "message": "Courbe cumulative : pourcentage ayant au moins ce niveau"}

def compute_all_stats(persons_list):
    try:
        cached = cache.get("global_stats")
        if cached: return cached
        df = pd.DataFrame([{"date_naissance": p.date_naissance, "sexe": p.sexe, "niveau_etude": p.niveau_etude, "revenus_usd": p.revenus_usd, "situation": p.situation} for p in persons_list if p.date_naissance])
        result = {
            **get_global_stats(df),
            "prediction": predict_revenues(df),
            "demographic_projection": project_demographics(df),
            "education_curve": get_education_curve(df)
        }
        cache.set("global_stats", result)
        return result
    except Exception as e:
        return {"total": 0, "error": str(e)}

def invalidate_cache(): cache.clear()
